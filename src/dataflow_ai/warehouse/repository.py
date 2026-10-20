"""Transactional DuckDB load logic and read-only analytics queries."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from dataflow_ai.ingestion.flexible_sales import FlexibleSalesImport
from dataflow_ai.models import DatasetQualityResult
from dataflow_ai.settings import Settings

LOGGER = logging.getLogger(__name__)


class WarehouseRepository:
    """Owns the warehouse schema and all parameterized database access."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cloud_env_configured = False
        self._initialized = False
        self._initialization_lock = threading.Lock()

    def _ensure_motherduck_env(self) -> None:
        """Set MOTHERDUCK_TOKEN in process env exactly once (required by DuckDB driver)."""
        if self._cloud_env_configured:
            return
        if self.settings.motherduck_token:
            os.environ["motherduck_token"] = self.settings.motherduck_token
            os.environ["MOTHERDUCK_TOKEN"] = self.settings.motherduck_token
            self._cloud_env_configured = True

    @contextmanager
    def connection(self, *, read_only: bool = False) -> Generator[duckdb.DuckDBPyConnection]:
        is_cloud = self.settings.is_cloud_database
        if is_cloud:
            self._ensure_motherduck_env()
        if not read_only:
            self.settings.ensure_directories()

        config: dict[str, Any] = {}
        # Local DuckDB benefits from a custom temp directory; MotherDuck manages its own.
        if not is_cloud and self.settings.temp_dir:
            self.settings.temp_dir.mkdir(parents=True, exist_ok=True)
            config["temp_directory"] = str(self.settings.temp_dir)

        dsn = self.settings.effective_database_dsn
        # MotherDuck does not support the read_only flag (access is managed server-side).
        # Do not wrap this call in a ThreadPoolExecutor: a timeout on a Future
        # does not cancel duckdb.connect(), and the executor context waits for
        # that worker during cleanup.  That made the previous "12 second"
        # timeout capable of hanging indefinitely and retried failed auth with
        # a different configuration.
        if is_cloud:
            if self.settings.motherduck_token:
                config["motherduck_token"] = self.settings.motherduck_token
            connection = duckdb.connect(dsn, config=config)
        else:
            connection = duckdb.connect(dsn, read_only=read_only, config=config)
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        if self._initialized:
            return
        # A runner uses this repository for start/load/finish.  Running the
        # full DDL script before each operation adds remote round trips and
        # makes MotherDuck jobs noticeably slower.
        with self._initialization_lock:
            if self._initialized:
                return
            schema_path = Path(__file__).with_name("schema.sql")
            with self.connection() as connection:
                connection.execute(schema_path.read_text(encoding="utf-8"))
            self._initialized = True

    def start_run(self, run_id: str, started_at: datetime) -> None:
        self.initialize()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO governance.pipeline_runs (run_id, status, started_at) VALUES (?, ?, ?)",
                [run_id, "running", started_at],
            )

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime,
        duration_seconds: float,
        ingested_rows: int,
        accepted_rows: int,
        rejected_rows: int,
        quality_score: float,
        error_message: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE governance.pipeline_runs
                SET status = ?, finished_at = ?, duration_seconds = ?, ingested_rows = ?,
                    accepted_rows = ?, rejected_rows = ?, quality_score = ?, error_message = ?
                WHERE run_id = ?
                """,
                [
                    status,
                    finished_at,
                    duration_seconds,
                    ingested_rows,
                    accepted_rows,
                    rejected_rows,
                    quality_score,
                    error_message,
                    run_id,
                ],
            )

    def load_validated_data(self, quality_results: dict[str, DatasetQualityResult], run_id: str) -> None:
        """Atomically refresh staging and dimensional marts from one validated batch."""

        self.initialize()
        with self.connection() as connection:
            # MotherDuck shares DuckDB's transactional semantics.  Keeping the
            # delete, load, marts, and quality records in one transaction means
            # a reader sees either the old batch or the new batch, never a
            # partially rebuilt warehouse.
            connection.execute("BEGIN TRANSACTION")
            try:
                for table in ("website_events", "order_items", "orders", "products", "customers"):
                    connection.execute(f"DELETE FROM staging.{table}")
                self._insert_records(connection, "customers", quality_results["customers"].accepted_records)
                self._insert_records(connection, "products", quality_results["products"].accepted_records)
                self._insert_records(connection, "orders", quality_results["orders"].accepted_records)
                self._insert_records(connection, "order_items", quality_results["order_items"].accepted_records)
                self._insert_records(connection, "website_events", quality_results["website_events"].accepted_records)
                self._build_marts(connection)
                self._record_quality_results(connection, quality_results, run_id)
                self._set_active_sales_dataset(connection, mode="curated", run_id=run_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def load_flexible_sales(self, sales_import: FlexibleSalesImport, run_id: str) -> None:
        """Atomically replace the active flexible sales dataset.

        ``analytics.flex_sales`` is stable enough for canonical KPIs.  The
        source-shaped ``analytics.sales_upload`` table preserves every detected
        column so the AI can answer questions about fields such as channel,
        sales representative, or tax that were not part of the original app's
        five-file contract.
        """

        self.initialize()
        profile = sales_import.profile
        with self.connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute("DELETE FROM analytics.flex_sales")
                self._create_source_shaped_sales_table(
                    connection,
                    columns=profile.columns,
                    column_types=profile.column_types,
                    rows=sales_import.rows,
                )
                canonical_columns = [
                    "source_file", "source_row", "sales_date", "order_id", "customer",
                    "customer_id", "customer_name", "product", "product_name",
                    "category", "region", "quantity", "unit_price", "discount",
                    "revenue", "net_revenue", "cost", "profit", "status", "currency",
                    "attributes_json",
                ]
                self._insert_rows(
                    connection,
                    "analytics.flex_sales",
                    canonical_columns,
                    sales_import.canonical_rows,
                )
                connection.execute(
                    """
                    INSERT INTO governance.sales_imports
                      (run_id, imported_at, source_files_json, semantic_mapping_json, column_types_json,
                       warnings_json, row_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        datetime.now(timezone.utc).replace(tzinfo=None),
                        json.dumps(profile.source_files, ensure_ascii=False),
                        json.dumps(profile.semantic_mapping, ensure_ascii=False, sort_keys=True),
                        json.dumps(profile.column_types, ensure_ascii=False, sort_keys=True),
                        json.dumps(profile.warnings, ensure_ascii=False),
                        profile.row_count,
                    ],
                )
                connection.execute("DELETE FROM governance.data_quality_results WHERE run_id = ?", [run_id])
                connection.execute(
                    """
                    INSERT INTO governance.data_quality_results
                      (run_id, dataset, rows_read, accepted_rows, rejected_rows, quality_score, issue_count, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        "flexible_sales",
                        profile.row_count,
                        profile.accepted_row_count,
                        profile.rejected_row_count,
                        round(100 * profile.accepted_row_count / profile.row_count, 2) if profile.row_count else 100.0,
                        len(profile.warnings),
                        datetime.now(timezone.utc).replace(tzinfo=None),
                    ],
                )
                self._set_active_sales_dataset(connection, mode="flexible", run_id=run_id)

                for idx_sql in (
                    "CREATE INDEX IF NOT EXISTS idx_flex_sales_date     ON analytics.flex_sales (sales_date)",
                    "CREATE INDEX IF NOT EXISTS idx_flex_sales_category ON analytics.flex_sales (category)",
                    "CREATE INDEX IF NOT EXISTS idx_flex_sales_region   ON analytics.flex_sales (region)",
                ):
                    connection.execute(idx_sql)

                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _create_source_shaped_sales_table(
        connection: duckdb.DuckDBPyConnection,
        *,
        columns: list[str],
        column_types: dict[str, str],
        rows: list[dict[str, Any]],
    ) -> None:
        if not columns:
            raise ValueError("A flexible sales import needs at least one source column.")
        allowed_types = {"VARCHAR", "DOUBLE", "BIGINT", "DATE"}
        definitions = []
        for column in columns:
            inferred_type = column_types.get(column, "VARCHAR").upper()
            if inferred_type not in allowed_types:
                raise ValueError(f"Unsupported inferred type for flexible sales column {column!r}.")
            definitions.append(f"{_quote_identifier(column)} {inferred_type}")
        connection.execute("DROP TABLE IF EXISTS analytics.sales_upload")
        connection.execute(f"CREATE TABLE analytics.sales_upload ({', '.join(definitions)})")
        WarehouseRepository._insert_rows(connection, "analytics.sales_upload", columns, rows)

    @staticmethod
    def _insert_rows(
        connection: duckdb.DuckDBPyConnection,
        table: str,
        columns: list[str],
        records: list[dict[str, Any]],
    ) -> None:
        if not records:
            return
        safe_columns = ", ".join(_quote_identifier(column) for column in columns)
        values = [tuple(record.get(column) for column in columns) for record in records]
        # Registering Arrow batches turns thousands of remote parameterized
        # inserts into a small number of set-based INSERT ... SELECT calls.
        # It is materially faster for MotherDuck and still works locally.  The
        # executemany path remains only as a compatibility fallback.
        try:
            import pyarrow as pa

            batch_size = 25_000
            for start in range(0, len(records), batch_size):
                batch_records = [
                    {column: record.get(column) for column in columns}
                    for record in records[start : start + batch_size]
                ]
                relation_name = f"_dataflow_batch_{uuid.uuid4().hex}"
                connection.register(relation_name, pa.Table.from_pylist(batch_records))
                try:
                    connection.execute(
                        f"INSERT INTO {table} ({safe_columns}) "
                        f"SELECT {safe_columns} FROM {_quote_identifier(relation_name)}"
                    )
                finally:
                    connection.unregister(relation_name)
            return
        except (ImportError, ModuleNotFoundError):
            pass

        placeholders = ", ".join("?" for _ in columns)
        statement = f"INSERT INTO {table} ({safe_columns}) VALUES ({placeholders})"
        for start in range(0, len(values), 5_000):
            connection.executemany(statement, values[start : start + 5_000])

    @staticmethod
    def _set_active_sales_dataset(
        connection: duckdb.DuckDBPyConnection,
        *,
        mode: str,
        run_id: str,
    ) -> None:
        connection.execute("DELETE FROM governance.active_sales_dataset WHERE singleton_id = 1")
        connection.execute(
            """
            INSERT INTO governance.active_sales_dataset (singleton_id, mode, run_id, activated_at)
            VALUES (1, ?, ?, ?)
            """,
            [mode, run_id, datetime.now(timezone.utc).replace(tzinfo=None)],
        )

    @staticmethod
    def _insert_records(
        connection: duckdb.DuckDBPyConnection, table: str, records: list[dict[str, Any]]
    ) -> None:
        if not records:
            return
        column_sets = {
            "customers": ["customer_id", "customer_name", "email", "city", "state", "country", "region", "signup_date", "segment"],
            "products": ["product_id", "product_name", "category", "subcategory", "list_price", "unit_cost", "supplier"],
            "orders": ["order_id", "customer_id", "order_date", "status", "payment_method", "region", "marketing_channel"],
            "order_items": ["order_item_id", "order_id", "product_id", "quantity", "unit_price", "discount_amount"],
            "website_events": ["event_id", "customer_id", "timestamp", "event_type", "page", "device"],
        }
        columns = column_sets[table]
        target_columns = ["event_timestamp" if column == "timestamp" else column for column in columns]
        WarehouseRepository._insert_rows(
            connection,
            f"staging.{table}",
            target_columns,
            [{target: record.get(source) for source, target in zip(columns, target_columns, strict=True)} for record in records],
        )

    @staticmethod
    def _build_marts(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE OR REPLACE TABLE analytics.dim_customer AS
            SELECT customer_id, customer_name, email, city, state, country, region, signup_date, segment
            FROM staging.customers
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE analytics.dim_product AS
            SELECT product_id, product_name, category, subcategory, list_price, unit_cost, supplier
            FROM staging.products
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE analytics.dim_region AS
            SELECT DISTINCT region, region AS region_name
            FROM staging.orders
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE analytics.dim_date AS
            SELECT DISTINCT
                order_date AS date_key,
                EXTRACT(YEAR FROM order_date)::INTEGER AS year,
                EXTRACT(MONTH FROM order_date)::INTEGER AS month,
                STRFTIME(order_date, '%Y-%m') AS year_month,
                STRFTIME(order_date, '%B') AS month_name,
                EXTRACT(QUARTER FROM order_date)::INTEGER AS quarter
            FROM staging.orders
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE analytics.fact_sales AS
            SELECT
                i.order_item_id AS sale_key,
                o.order_id,
                o.customer_id,
                i.product_id,
                o.order_date AS date_key,
                o.region,
                o.marketing_channel,
                o.status,
                i.quantity,
                i.unit_price,
                i.discount_amount,
                (i.quantity * i.unit_price)::DOUBLE AS gross_revenue,
                GREATEST((i.quantity * i.unit_price) - i.discount_amount, 0)::DOUBLE AS net_revenue,
                (i.quantity * p.unit_cost)::DOUBLE AS cost_amount,
                (GREATEST((i.quantity * i.unit_price) - i.discount_amount, 0) - (i.quantity * p.unit_cost))::DOUBLE AS profit_amount
            FROM staging.order_items AS i
            INNER JOIN staging.orders AS o ON o.order_id = i.order_id
            INNER JOIN staging.products AS p ON p.product_id = i.product_id
            WHERE o.status = 'completed'
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE analytics.monthly_sales AS
            SELECT
                STRFTIME(date_key, '%Y-%m') AS year_month,
                region,
                SUM(net_revenue)::DOUBLE AS revenue,
                COUNT(DISTINCT order_id)::BIGINT AS orders,
                COUNT(DISTINCT customer_id)::BIGINT AS customers,
                AVG(net_revenue)::DOUBLE AS average_line_value
            FROM analytics.fact_sales
            GROUP BY 1, 2
            """
        )

        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_fact_sales_date     ON analytics.fact_sales (date_key)",
            "CREATE INDEX IF NOT EXISTS idx_fact_sales_region   ON analytics.fact_sales (region)",
            "CREATE INDEX IF NOT EXISTS idx_fact_sales_customer ON analytics.fact_sales (customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_fact_sales_product  ON analytics.fact_sales (product_id)",
        ):
            connection.execute(idx_sql)

    @staticmethod
    def _record_quality_results(
        connection: duckdb.DuckDBPyConnection,
        results: dict[str, DatasetQualityResult],
        run_id: str,
    ) -> None:
        recorded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute("DELETE FROM governance.data_quality_results WHERE run_id = ?", [run_id])
        connection.executemany(
            """
            INSERT INTO governance.data_quality_results
              (run_id, dataset, rows_read, accepted_rows, rejected_rows, quality_score, issue_count, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    result.dataset,
                    result.rows_read,
                    result.accepted_count,
                    result.rejected_count,
                    result.quality_score,
                    len(result.issues),
                    recorded_at,
                )
                for result in results.values()
            ],
        )

    def fetch_all(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run a trusted parameterized query and return JSON-serializable dictionaries.

        DuckDB exposes ``interrupt`` on a live connection.  Using it from a
        timer gives the AI executor a real query-work bound; its response-row
        limit alone cannot stop a costly sort or Cartesian product.
        """

        with self.connection(read_only=True) as connection:
            timer: threading.Timer | None = None
            timed_out = threading.Event()
            if timeout_seconds is not None:
                if timeout_seconds <= 0:
                    raise ValueError("timeout_seconds must be positive when supplied.")

                def interrupt() -> None:
                    timed_out.set()
                    try:
                        connection.interrupt()
                    except Exception:  # pragma: no cover - defensive for driver shutdown races
                        LOGGER.debug("Unable to interrupt timed-out DuckDB query.", exc_info=True)

                timer = threading.Timer(timeout_seconds, interrupt)
                timer.daemon = True
                timer.start()
            try:
                cursor = connection.execute(sql, parameters or [])
                columns = [item[0] for item in cursor.description]
                rows = [dict(zip(columns, (_json_value(value) for value in row), strict=True)) for row in cursor.fetchall()]
            except Exception as error:
                if timed_out.is_set():
                    raise TimeoutError(f"Warehouse query exceeded {timeout_seconds:.1f} seconds.") from error
                raise
            finally:
                if timer is not None:
                    timer.cancel()
            return rows

    def ai_allowed_tables(self) -> set[str]:
        """Return the only relations that a model-generated query may read."""

        curated_tables = {
            "analytics.fact_sales",
            "analytics.dim_customer",
            "analytics.dim_product",
            "analytics.dim_date",
            "analytics.dim_region",
            "analytics.monthly_sales",
        }
        if self.active_sales_mode() == "flexible":
            return {"analytics.flex_sales", "analytics.sales_upload"}
        return curated_tables

    def active_sales_mode(self) -> str:
        """Return the currently published semantic model, defaulting safely.

        This state deliberately is not cached.  A scheduled ingestion worker,
        API process, and Streamlit process commonly have separate repository
        objects pointed at the same warehouse.  Caching the value in one
        process made it serve the schema from a previous import after another
        process activated a flexible dataset.
        """

        self.initialize()
        try:
            rows = self.fetch_all(
                "SELECT mode FROM governance.active_sales_dataset WHERE singleton_id = 1"
            )
        except Exception:  # pragma: no cover - only supports legacy partially migrated databases
            return "curated"
        mode = str(rows[0].get("mode", "curated")) if rows else "curated"
        return mode if mode in {"curated", "flexible"} else "curated"

    def ai_schema_catalog(self) -> str:
        """Build live schema context instead of relying on stale Markdown docs."""

        self.initialize()
        table_names = sorted(table.rsplit(".", 1)[1] for table in self.ai_allowed_tables())
        placeholders = ", ".join("?" for _ in table_names)
        rows = self.fetch_all(
            f"""
            SELECT table_name, column_name, data_type, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = 'analytics' AND table_name IN ({placeholders})
            ORDER BY table_name, ordinal_position
            """,
            table_names,
        )
        by_table: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_table.setdefault(str(row["table_name"]), []).append(row)
        if not by_table:
            return "No analytical tables are currently loaded. Ask the user to import sales data first."
        lines = ["Active warehouse schema (use only these analytics tables):"]
        for table_name, columns in by_table.items():
            lines.append(f"TABLE analytics.{table_name}")
            lines.extend(f"  {column['column_name']} {column['data_type']}" for column in columns)
        profile = self.flexible_sales_profile() if self.active_sales_mode() == "flexible" else None
        if profile:
            mapping = profile.get("semantic_mapping", {})
            if mapping:
                rendered = ", ".join(f"{target} <- {source}" for target, source in sorted(mapping.items()))
                lines.append(f"Flexible-import semantic mapping: {rendered}")
        curr_code = self.active_currency_code()
        curr_sym = self.active_currency_symbol()
        lines.append(f"Active dataset currency: {curr_code} ({curr_sym}). All calculations and output values must use this currency without conversion.")
        return "\n".join(lines)

    def flexible_sales_profile(self) -> dict[str, Any] | None:
        """Return the current flexible-import mapping without exposing raw source values."""

        self.initialize()
        if self.active_sales_mode() != "flexible":
            return None
        rows = self.fetch_all(
            """
            SELECT imports.run_id, imports.source_files_json, imports.semantic_mapping_json,
                   imports.column_types_json, imports.warnings_json, imports.row_count
            FROM governance.sales_imports AS imports
            INNER JOIN governance.active_sales_dataset AS active ON active.run_id = imports.run_id
            WHERE active.singleton_id = 1 AND active.mode = 'flexible'
            """
        )
        if not rows:
            return None
        row = rows[0]
        for key in ("source_files", "semantic_mapping", "column_types", "warnings"):
            raw_key = f"{key}_json"
            try:
                row[key] = json.loads(row.pop(raw_key))
            except (TypeError, json.JSONDecodeError):
                row[key] = {} if key in {"semantic_mapping", "column_types"} else []
        return row

    def active_currency_code(self) -> str:
        """Return ISO currency code of active data (e.g. 'USD' or 'INR')."""
        self.initialize()
        if self.active_sales_mode() == "flexible":
            try:
                rows = self.fetch_all(
                    """
                    SELECT currency, COUNT(*) AS cnt
                    FROM analytics.flex_sales
                    WHERE currency IS NOT NULL AND TRIM(currency) != ''
                    GROUP BY currency
                    ORDER BY cnt DESC
                    LIMIT 1
                    """
                )
                if rows and rows[0].get("currency"):
                    cur = str(rows[0]["currency"]).strip().upper()
                    if "$" in cur or cur in {"USD", "DOLLAR", "DOLLARS"}:
                        return "USD"
                    if "₹" in cur or cur in {"INR", "RS", "RS.", "RUPEE", "RUPEES"}:
                        return "INR"
                    if "€" in cur or cur in {"EUR", "EURO", "EUROS"}:
                        return "EUR"
                    if "£" in cur or cur in {"GBP", "POUND", "POUNDS"}:
                        return "GBP"
                    return cur
            except Exception:
                pass
            return "USD"
        return "INR"

    def active_currency_symbol(self) -> str:
        """Return the visual currency symbol ('$' or '₹') without conversion."""
        code = self.active_currency_code()
        if code in {"USD", "$"}:
            return "$"
        if code in {"INR", "₹"}:
            return "₹"
        if code in {"EUR", "€"}:
            return "€"
        if code in {"GBP", "£"}:
            return "£"
        return "$" if "$" in code else ("₹" if "₹" in code else code)

    def kpi_overview(self) -> dict[str, Any]:
        if self.active_sales_mode() == "flexible":
            rows = self.fetch_all(
                """
                SELECT COALESCE(SUM(revenue), 0)::DOUBLE AS total_revenue,
                       COALESCE(SUM(CASE WHEN quantity IS NOT NULL AND unit_price IS NOT NULL THEN (quantity * unit_price) ELSE revenue END), SUM(revenue), 0)::DOUBLE AS total_sales_amount,
                       COALESCE(NULLIF(COUNT(DISTINCT order_id), 0), COUNT(*))::BIGINT AS total_orders,
                       COALESCE(NULLIF(COUNT(DISTINCT CASE WHEN customer IS NOT NULL AND customer != 'Unknown' THEN customer END), 0), 1)::BIGINT AS active_customers,
                       COALESCE(AVG(revenue), 0)::DOUBLE AS average_line_value
                FROM analytics.flex_sales
                """
            )
            return rows[0]
        rows = self.fetch_all(
            """
            SELECT
              COALESCE(SUM(net_revenue), 0)::DOUBLE AS total_revenue,
              COALESCE(SUM(gross_revenue), 0)::DOUBLE AS total_sales_amount,
              COUNT(DISTINCT order_id)::BIGINT AS total_orders,
              COUNT(DISTINCT customer_id)::BIGINT AS active_customers,
              COALESCE(AVG(net_revenue), 0)::DOUBLE AS average_line_value
            FROM analytics.fact_sales
            """
        )
        return rows[0]

    def sales(self, start: str | None = None, end: str | None = None, region: str | None = None) -> list[dict[str, Any]]:
        if self.active_sales_mode() == "flexible":
            clauses = ["sales_date IS NOT NULL"]
            parameters: list[Any] = []
            if start:
                clauses.append("sales_date >= ?")
                parameters.append(start)
            if end:
                clauses.append("sales_date < ?")
                parameters.append(end)
            if region:
                clauses.append("region = ?")
                parameters.append(region)
            return self.fetch_all(
                f"""
                SELECT sales_date AS date_key, COALESCE(region, 'Unspecified') AS region,
                       SUM(revenue)::DOUBLE AS revenue, COUNT(DISTINCT order_id)::BIGINT AS orders
                FROM analytics.flex_sales
                WHERE {' AND '.join(clauses)}
                GROUP BY sales_date, COALESCE(region, 'Unspecified')
                ORDER BY sales_date, region
                """,
                parameters,
            )
        clauses = ["1 = 1"]
        parameters: list[Any] = []
        if start:
            clauses.append("date_key >= ?")
            parameters.append(start)
        if end:
            clauses.append("date_key < ?")
            parameters.append(end)
        if region:
            clauses.append("region = ?")
            parameters.append(region)
        return self.fetch_all(
            f"""
            SELECT date_key, region, SUM(net_revenue)::DOUBLE AS revenue,
                   COUNT(DISTINCT order_id)::BIGINT AS orders
            FROM analytics.fact_sales
            WHERE {' AND '.join(clauses)}
            GROUP BY date_key, region
            ORDER BY date_key, region
            """,
            parameters,
        )

    def top_products(self, limit: int = 10) -> list[dict[str, Any]]:
        if self.active_sales_mode() == "flexible":
            return self.fetch_all(
                """
                SELECT product AS product_name, category, SUM(revenue)::DOUBLE AS revenue,
                       COALESCE(SUM(quantity), 0)::DOUBLE AS units_sold
                FROM analytics.flex_sales
                WHERE product IS NOT NULL
                GROUP BY product, category
                ORDER BY revenue DESC
                LIMIT ?
                """,
                [limit],
            )
        return self.fetch_all(
            """
            SELECT p.product_name, p.category, SUM(f.net_revenue)::DOUBLE AS revenue,
                   SUM(f.quantity)::BIGINT AS units_sold
            FROM analytics.fact_sales AS f
            INNER JOIN analytics.dim_product AS p ON p.product_id = f.product_id
            GROUP BY p.product_name, p.category
            ORDER BY revenue DESC
            LIMIT ?
            """,
            [limit],
        )

    def pipeline_status(self) -> dict[str, Any] | None:
        rows = self.fetch_all(
            """
            SELECT run_id, status, started_at, finished_at, duration_seconds,
                   ingested_rows, accepted_rows, rejected_rows, quality_score, error_message
            FROM governance.pipeline_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        return rows[0] if rows else None

    def data_quality(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            WITH latest_run AS (SELECT run_id FROM governance.pipeline_runs ORDER BY started_at DESC LIMIT 1)
            SELECT dataset, rows_read, accepted_rows, rejected_rows, quality_score, issue_count, recorded_at
            FROM governance.data_quality_results
            WHERE run_id = (SELECT run_id FROM latest_run)
            ORDER BY dataset
            """
        )

    def monthly_trend(self, months: int = 6) -> list[dict[str, Any]]:
        """Return aggregated revenue, orders, and customers for the most recent N months."""

        if self.active_sales_mode() == "flexible":
            return self.fetch_all(
                """
                WITH monthly AS (
                    SELECT STRFTIME(sales_date, '%Y-%m') AS year_month,
                           COALESCE(SUM(revenue), 0)::DOUBLE AS revenue,
                           COUNT(DISTINCT order_id)::BIGINT AS orders,
                           COUNT(DISTINCT customer)::BIGINT AS customers
                    FROM analytics.flex_sales
                    WHERE sales_date IS NOT NULL
                    GROUP BY 1
                )
                SELECT year_month, revenue, orders, customers
                FROM monthly ORDER BY year_month DESC LIMIT ?
                """,
                [months],
            )
        return self.fetch_all(
            """
            WITH monthly AS (
                SELECT
                    STRFTIME(date_key, '%Y-%m') AS year_month,
                    COALESCE(SUM(net_revenue), 0)::DOUBLE AS revenue,
                    COUNT(DISTINCT order_id)::BIGINT AS orders,
                    COUNT(DISTINCT customer_id)::BIGINT AS customers
                FROM analytics.fact_sales
                GROUP BY 1
            )
            SELECT year_month, revenue, orders, customers
            FROM monthly
            ORDER BY year_month DESC
            LIMIT ?
            """,
            [months],
        )

    def sales_by_category(self) -> list[dict[str, Any]]:
        """Return total revenue grouped by product category."""

        if self.active_sales_mode() == "flexible":
            return self.fetch_all(
                """
                SELECT COALESCE(category, 'Unspecified') AS category,
                       COALESCE(SUM(revenue), 0)::DOUBLE AS revenue,
                       COALESCE(SUM(quantity), 0)::DOUBLE AS units_sold,
                       COUNT(DISTINCT order_id)::BIGINT AS orders
                FROM analytics.flex_sales
                GROUP BY COALESCE(category, 'Unspecified')
                ORDER BY revenue DESC
                """
            )
        return self.fetch_all(
            """
            SELECT p.category,
                   COALESCE(SUM(f.net_revenue), 0)::DOUBLE AS revenue,
                   COALESCE(SUM(f.quantity), 0)::BIGINT AS units_sold,
                   COUNT(DISTINCT f.order_id)::BIGINT AS orders
            FROM analytics.fact_sales AS f
            INNER JOIN analytics.dim_product AS p ON p.product_id = f.product_id
            GROUP BY p.category
            ORDER BY revenue DESC
            """
        )

    def monthly_summary(self, year_month: str) -> dict[str, Any]:
        """Generate monthly business metrics for the reports page."""

        if self.active_sales_mode() == "flexible":
            return self._flexible_monthly_summary(year_month)
        current_data = self.fetch_all(
            """
            SELECT
                SUM(net_revenue)::DOUBLE AS total_revenue,
                COUNT(DISTINCT order_id)::BIGINT AS total_orders,
                COUNT(DISTINCT customer_id)::BIGINT AS total_customers,
                AVG(net_revenue)::DOUBLE AS avg_line_value
            FROM analytics.fact_sales
            WHERE STRFTIME(date_key, '%Y-%m') = ?
            """,
            [year_month],
        )
        previous_month = self._previous_month(year_month)
        previous_data = self.fetch_all(
            """
            SELECT
                SUM(net_revenue)::DOUBLE AS total_revenue,
                COUNT(DISTINCT order_id)::BIGINT AS total_orders,
                COUNT(DISTINCT customer_id)::BIGINT AS total_customers
            FROM analytics.fact_sales
            WHERE STRFTIME(date_key, '%Y-%m') = ?
            """,
            [previous_month],
        )
        category_data = self.fetch_all(
            """
            SELECT p.category,
                   SUM(f.net_revenue)::DOUBLE AS revenue,
                   SUM(f.quantity)::BIGINT AS units_sold
            FROM analytics.fact_sales AS f
            INNER JOIN analytics.dim_product AS p ON p.product_id = f.product_id
            WHERE STRFTIME(f.date_key, '%Y-%m') = ?
            GROUP BY p.category
            ORDER BY revenue DESC
            """,
            [year_month],
        )
        prev_category_data = self.fetch_all(
            """
            SELECT p.category, SUM(f.net_revenue)::DOUBLE AS revenue
            FROM analytics.fact_sales AS f
            INNER JOIN analytics.dim_product AS p ON p.product_id = f.product_id
            WHERE STRFTIME(f.date_key, '%Y-%m') = ?
            GROUP BY p.category
            """,
            [previous_month],
        )
        region_data = self.fetch_all(
            """
            SELECT region, SUM(net_revenue)::DOUBLE AS revenue,
                   COUNT(DISTINCT order_id)::BIGINT AS orders
            FROM analytics.fact_sales
            WHERE STRFTIME(date_key, '%Y-%m') = ?
            GROUP BY region
            ORDER BY revenue DESC
            """,
            [year_month],
        )
        current = current_data[0] if current_data else {}
        previous = previous_data[0] if previous_data else {}
        prev_cat_map = {row["category"]: row["revenue"] for row in prev_category_data}

        # Category growth
        category_growth = []
        for cat in category_data:
            prev_rev = prev_cat_map.get(cat["category"], 0)
            growth_pct = ((cat["revenue"] - prev_rev) / prev_rev * 100) if prev_rev else 0
            category_growth.append({**cat, "previous_revenue": prev_rev, "growth_pct": round(growth_pct, 1)})

        return {
            "year_month": year_month,
            "previous_month": previous_month,
            "total_revenue": current.get("total_revenue", 0),
            "total_orders": current.get("total_orders", 0),
            "total_customers": current.get("total_customers", 0),
            "avg_line_value": current.get("avg_line_value", 0),
            "prev_revenue": previous.get("total_revenue", 0),
            "prev_orders": previous.get("total_orders", 0),
            "prev_customers": previous.get("total_customers", 0),
            "revenue_change_pct": round(
                ((current.get("total_revenue", 0) - previous.get("total_revenue", 0))
                 / previous.get("total_revenue", 1) * 100), 1
            ) if previous.get("total_revenue") else 0,
            "customer_change_pct": round(
                ((current.get("total_customers", 0) - previous.get("total_customers", 0))
                 / previous.get("total_customers", 1) * 100), 1
            ) if previous.get("total_customers") else 0,
            "categories": category_growth,
            "regions": region_data,
            "top_region": region_data[0]["region"] if region_data else "N/A",
            "top_region_revenue": region_data[0]["revenue"] if region_data else 0,
            "highest_growth_category": max(category_growth, key=lambda c: c["growth_pct"])["category"] if category_growth else "N/A",
            "highest_growth_pct": max(category_growth, key=lambda c: c["growth_pct"])["growth_pct"] if category_growth else 0,
        }

    @staticmethod
    def _previous_month(year_month: str) -> str:
        year, month = int(year_month[:4]), int(year_month[5:7])
        if month == 1:
            return f"{year - 1}-12"
        return f"{year}-{month - 1:02d}"

    def _flexible_monthly_summary(self, year_month: str) -> dict[str, Any]:
        """Report using only optional fields available in a flexible export."""

        previous_month = self._previous_month(year_month)
        aggregate_sql = """
            SELECT COALESCE(SUM(revenue), 0)::DOUBLE AS total_revenue,
                   COUNT(DISTINCT order_id)::BIGINT AS total_orders,
                   COUNT(DISTINCT customer)::BIGINT AS total_customers,
                   COALESCE(AVG(revenue), 0)::DOUBLE AS avg_line_value
            FROM analytics.flex_sales
            WHERE STRFTIME(sales_date, '%Y-%m') = ?
        """
        current = self.fetch_all(aggregate_sql, [year_month])[0]
        previous = self.fetch_all(aggregate_sql, [previous_month])[0]
        category_data = self.fetch_all(
            """
            SELECT COALESCE(category, 'Unspecified') AS category, SUM(revenue)::DOUBLE AS revenue,
                   COALESCE(SUM(quantity), 0)::DOUBLE AS units_sold
            FROM analytics.flex_sales
            WHERE STRFTIME(sales_date, '%Y-%m') = ?
            GROUP BY COALESCE(category, 'Unspecified')
            ORDER BY revenue DESC
            """,
            [year_month],
        )
        previous_categories = self.fetch_all(
            """
            SELECT COALESCE(category, 'Unspecified') AS category, SUM(revenue)::DOUBLE AS revenue
            FROM analytics.flex_sales
            WHERE STRFTIME(sales_date, '%Y-%m') = ?
            GROUP BY COALESCE(category, 'Unspecified')
            """,
            [previous_month],
        )
        regions = self.fetch_all(
            """
            SELECT COALESCE(region, 'Unspecified') AS region, SUM(revenue)::DOUBLE AS revenue,
                   COUNT(DISTINCT order_id)::BIGINT AS orders
            FROM analytics.flex_sales
            WHERE STRFTIME(sales_date, '%Y-%m') = ?
            GROUP BY COALESCE(region, 'Unspecified')
            ORDER BY revenue DESC
            """,
            [year_month],
        )
        previous_map = {row["category"]: float(row["revenue"] or 0) for row in previous_categories}
        categories = []
        for category in category_data:
            current_revenue = float(category["revenue"] or 0)
            previous_revenue = previous_map.get(category["category"], 0.0)
            growth = ((current_revenue - previous_revenue) / previous_revenue * 100) if previous_revenue else 0.0
            categories.append({**category, "previous_revenue": previous_revenue, "growth_pct": round(growth, 1)})
        return {
            "year_month": year_month,
            "previous_month": previous_month,
            "total_revenue": current["total_revenue"],
            "total_orders": current["total_orders"],
            "total_customers": current["total_customers"],
            "avg_line_value": current["avg_line_value"],
            "prev_revenue": previous["total_revenue"],
            "prev_orders": previous["total_orders"],
            "prev_customers": previous["total_customers"],
            "revenue_change_pct": round(
                ((float(current["total_revenue"] or 0) - float(previous["total_revenue"] or 0)) /
                 float(previous["total_revenue"]) * 100),
                1,
            ) if previous["total_revenue"] else 0,
            "customer_change_pct": round(
                ((float(current["total_customers"] or 0) - float(previous["total_customers"] or 0)) /
                 float(previous["total_customers"]) * 100),
                1,
            ) if previous["total_customers"] else 0,
            "categories": categories,
            "regions": regions,
            "top_region": regions[0]["region"] if regions else "N/A",
            "top_region_revenue": regions[0]["revenue"] if regions else 0,
            "highest_growth_category": max(categories, key=lambda item: item["growth_pct"])["category"] if categories else "N/A",
            "highest_growth_pct": max(categories, key=lambda item: item["growth_pct"])["growth_pct"] if categories else 0,
        }

    def available_months(self) -> list[str]:
        """Return all year-month periods that have sales data, most recent first."""

        if self.active_sales_mode() == "flexible":
            return [
                row["year_month"]
                for row in self.fetch_all(
                    """
                    SELECT DISTINCT STRFTIME(sales_date, '%Y-%m') AS year_month
                    FROM analytics.flex_sales
                    WHERE sales_date IS NOT NULL
                    ORDER BY year_month DESC
                    """
                )
            ]
        return [
            row["year_month"]
            for row in self.fetch_all(
                """
                SELECT DISTINCT STRFTIME(date_key, '%Y-%m') AS year_month
                FROM analytics.fact_sales
                ORDER BY year_month DESC
                """
            )
        ]

    def table_metadata(self) -> list[dict[str, str]]:
        return [
            {"table": "analytics.fact_sales", "description": "Completed order lines with revenue, cost, and profit measures."},
            {"table": "analytics.dim_customer", "description": "Customer profile and geography dimension."},
            {"table": "analytics.dim_product", "description": "Product catalogue and category dimension."},
            {"table": "analytics.dim_date", "description": "Calendar dimension for daily, monthly, and quarterly analysis."},
            {"table": "analytics.dim_region", "description": "Sales-region dimension."},
            {"table": "analytics.monthly_sales", "description": "Monthly sales aggregate by region."},
            {"table": "analytics.flex_sales", "description": "Canonical fact rows inferred from the active flexible sales import."},
            {"table": "analytics.sales_upload", "description": "Source-shaped active sales upload with dynamically profiled columns."},
        ]

    def audit_ai_query(
        self,
        *,
        question: str,
        generated_sql: str | None,
        execution_status: str,
        provider: str,
        row_count: int | None = None,
        error_message: str | None = None,
    ) -> str:
        request_id = str(uuid.uuid4())
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO governance.ai_audit_log
                (request_id, asked_at, question, generated_sql, execution_status, row_count, provider, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    request_id,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    question,
                    generated_sql,
                    execution_status,
                    row_count,
                    provider,
                    error_message,
                ],
            )
        return request_id

    def get_customer_privacy_status(self, customer_id: str) -> dict[str, Any] | None:
        """Query privacy status of a customer record in staging and dimensional marts."""
        rows = self.fetch_all(
            "SELECT customer_id, customer_name, email, city, region, segment FROM staging.customers WHERE customer_id = ?",
            [customer_id],
        )
        if not rows:
            return None
        customer = rows[0]
        is_anonymized = (
            customer["customer_name"] == "Anonymized User"
            or "anonymized" in customer["email"].lower()
        )
        return {
            "customer_id": customer_id,
            "is_anonymized": is_anonymized,
            "email": customer["email"],
            "name": customer["customer_name"],
            "region": customer["region"],
        }

    def delete_or_anonymize_customer(self, customer_id: str) -> dict[str, Any]:
        """Atomically scrub personal data for a customer (GDPR Right to Erasure).

        Anonymizes customer identifiers and contact details in staging and dimensional marts,
        purges behavioral event logs, and retains transaction totals for financial reporting integrity.
        """
        self.initialize()
        status = self.get_customer_privacy_status(customer_id)
        if not status:
            raise KeyError(f"Customer {customer_id} was not found in the warehouse.")

        anonymized_name = "Anonymized User"
        anonymized_email = f"anonymized_{customer_id.lower().replace('-', '_')}@privacy.local"
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with self.connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                # 1. Anonymize personal info in staging.customers
                connection.execute(
                    """
                    UPDATE staging.customers
                    SET customer_name = ?, email = ?, city = 'REDACTED', state = 'REDACTED', country = 'REDACTED'
                    WHERE customer_id = ?
                    """,
                    [anonymized_name, anonymized_email, customer_id],
                )

                # 2. Purge personal web-tracking and behavioral events
                connection.execute(
                    "DELETE FROM staging.website_events WHERE customer_id = ?",
                    [customer_id],
                )

                # 3. Refresh dimensional marts so dim_customer immediately reflects erasure
                self._build_marts(connection)

                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        return {
            "customer_id": customer_id,
            "status": "anonymized",
            "anonymized_at": now.isoformat() + "Z",
            "message": f"Personal data for {customer_id} has been permanently anonymized across warehouse marts.",
        }



def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _quote_identifier(identifier: str) -> str:
    """Quote a profiler-produced identifier before composing trusted DDL."""

    if not identifier or not identifier.replace("_", "").isalnum() or identifier[0].isdigit():
        raise ValueError(f"Unsafe warehouse column identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'
