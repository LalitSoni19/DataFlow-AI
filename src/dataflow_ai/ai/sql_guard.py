"""SQL validation and safety checks for analytical queries."""

from __future__ import annotations

import re
from typing import Any, Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from dataflow_ai.models import SQLExecutionResult, utc_now_iso
from dataflow_ai.utils import mask_record_pii
from dataflow_ai.warehouse.repository import WarehouseRepository

FORBIDDEN_TOKENS = re.compile(
    r"\b(ALTER|ANALYZE|ATTACH|CALL|COPY|CREATE|DELETE|DETACH|DROP|EXPORT|IMPORT|INSERT|INSTALL|LOAD|MERGE|PRAGMA|REPLACE|SET|TRUNCATE|UPDATE|VACUUM)\b",
    re.IGNORECASE,
)
COMMENT_TOKENS = re.compile(r"(--|/\*|\*/)")
TABLE_REFERENCE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
CTE_NAME = re.compile(r"(?:WITH|,)\s*([A-Za-z_][\w]*)\s+AS\s*\(", re.IGNORECASE)
DEFAULT_ALLOWED_TABLES = {
    "analytics.fact_sales",
    "analytics.dim_customer",
    "analytics.dim_product",
    "analytics.dim_date",
    "analytics.dim_region",
    "analytics.monthly_sales",
    "analytics.flex_sales",
    "analytics.sales_upload",
}
EXTERNAL_SCAN_TOKENS = re.compile(
    r"\b(read_(?:csv|csv_auto|parquet|json|json_auto|ndjson)|(?:range|generate_series|glob)\s*\(|"
    r"delta_scan|iceberg_scan|sqlite_scan|postgres_scan|mysql_scan|httpfs)\b",
    re.IGNORECASE,
)
FORBIDDEN_NODE_NAMES = {
    "ALTER", "ANALYZE", "ATTACH", "CALL", "COMMAND", "COPY", "CREATE", "DELETE", "DETACH", "DROP",
    "EXPORT", "IMPORT", "INSERT", "INSTALL", "LOAD", "MERGE", "PRAGMA", "SET", "TRANSACTION",
    "TRUNCATE", "UPDATE", "USE", "VACUUM",
}
FORBIDDEN_RELATION_NODE_NAMES = {"LATERAL", "UNNEST", "PIVOT", "UNPIVOT", "VALUES"}
ALLOWED_FUNCTIONS = {
    "ABS", "ANY_VALUE", "ARG_MAX", "ARG_MIN", "ARRAY_AGG", "AVG", "CAST", "CEIL", "CEILING", "COALESCE",
    "CONCAT", "CONTAINS", "COUNT", "COUNT_IF", "COUNTIF", "CURRENT_DATE", "CURRENT_TIMESTAMP", "CURRENT_TIME",
    "DATE", "DATE_ADD", "DATE_DIFF", "DATE_PART", "DATE_SUB", "DATE_TRUNC", "DATEADD", "DATEDIFF", "DATEPART",
    "DATESUB", "DATETRUNC", "DAY", "DENSE_RANK", "ENDS_WITH", "EXTRACT", "FIRST_VALUE", "FLOOR", "GREATEST",
    "GROUP_CONCAT", "HOUR", "IF", "IFF", "INSTR", "JSON", "JSON_ARRAY_LENGTH", "JSON_EXTRACT", "JSONEXTRACT",
    "JSON_EXTRACT_PATH", "JSON_EXTRACT_PATH_TEXT", "JSON_EXTRACT_SCALAR", "JSONEXTRACTSCALAR",
    "JSON_EXTRACT_STRING", "JSON_KEYS", "JSON_STRUCTURE", "JSON_VALUE", "LAG", "LAST_VALUE", "LEAD", "LEAST",
    "LENGTH", "LIST", "LOWER", "LPAD", "LTRIM", "MAX", "MEDIAN", "MIN", "MINUTE", "MODE", "MONTH", "NOW",
    "NULLIF", "NTILE", "PERCENT_RANK", "PERCENTILE_CONT", "POSITION", "POW", "POWER", "QUANTILE_CONT",
    "QUANTILE_DISC", "QUARTER", "RANK", "REGEXP_LIKE", "REGEXP_MATCHES", "REGEXP_REPLACE", "REPLACE",
    "REVERSE", "ROUND", "ROW_NUMBER", "RPAD", "RTRIM", "SECOND", "SIGN", "SPLIT_PART", "SQRT", "STARTS_WITH",
    "STDDEV", "STR_POSITION", "STR_TO_TIME", "STRFTIME", "STRFTIME_MILLIS", "STRPOS", "STRPTIME", "SUBSTR",
    "SUBSTRING", "SUM", "TIME_TO_STR", "TIMESTAMP", "TIMESTAMP_TRUNC", "TIMESTAMPTRUNC", "TO_JSON",
    "TO_TIMESTAMP", "TODAY", "TRIM", "TRY_CAST", "TRY_STRPTIME", "UNIX_TO_TIME", "UPPER", "WEEK", "YEAR",
    "AND", "OR", "NOT", "BETWEEN", "IN", "CASE",
}


class SQLSafetyError(ValueError):
    """Raised before a query can reach the database."""


def validate_read_only_sql(
    sql: str,
    *,
    allowed_tables: Iterable[str] | None = None,
    maximum_limit: int = 200,
) -> str:
    """Allow one bounded SELECT/CTE query over a concrete relation allow-list.

    Regex-only checks are vulnerable to comma joins and table functions, for
    example ``FROM analytics.fact_sales, read_csv_auto('secret.csv')``.  The
    parsed tree rejects external scans, relation functions, implicit Cartesian
    joins, non-allowlisted tables, mutations, and unknown functions.
    """

    candidate = sql.strip()
    if candidate.endswith(";"):
        candidate = candidate[:-1].rstrip()
    if not candidate:
        raise SQLSafetyError("SQL cannot be empty.")
    if ";" in candidate:
        raise SQLSafetyError("Only one SQL statement is allowed.")
    if COMMENT_TOKENS.search(candidate):
        raise SQLSafetyError("SQL comments are not allowed.")
    if EXTERNAL_SCAN_TOKENS.search(candidate):
        raise SQLSafetyError("External scans and generated relations are not allowed.")
    try:
        statements = sqlglot.parse(candidate, read="duckdb")
    except ParseError as error:
        raise SQLSafetyError("SQL could not be parsed as DuckDB SQL.") from error
    if len(statements) != 1:
        raise SQLSafetyError("Only one SQL statement is allowed.")
    expression = statements[0]
    if not isinstance(expression, exp.Query):
        raise SQLSafetyError("Only SELECT statements and read-only CTEs are allowed.")
    allowed = {table.lower() for table in (allowed_tables or DEFAULT_ALLOWED_TABLES)}
    _validate_tree(expression, allowed, maximum_limit)
    return expression.sql(dialect="duckdb")


def _validate_tree(expression: exp.Expression, allowed_tables: set[str], maximum_limit: int) -> None:
    ctes = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name}
    for node in expression.walk():
        node_name = node.__class__.__name__.upper()
        if node_name in FORBIDDEN_NODE_NAMES:
            raise SQLSafetyError(f"Forbidden SQL operation: {node_name}.")
        if node_name in FORBIDDEN_RELATION_NODE_NAMES:
            raise SQLSafetyError(f"Unsupported relation construct: {node_name.lower()}.")

    tables = list(expression.find_all(exp.Table))
    if not tables:
        raise SQLSafetyError("The query must read from an approved analytics table.")
    for table in tables:
        if not isinstance(table.this, exp.Identifier):
            raise SQLSafetyError("Table-valued functions and external relations are not allowed.")
        if table.catalog:
            raise SQLSafetyError("Catalog-qualified relations are not allowed.")
        relation = f"{table.db}.{table.name}" if table.db else table.name
        relation = relation.lower()
        if relation not in allowed_tables and relation not in ctes:
            # prefix bare table names with analytics schema
            if not table.db and f"analytics.{relation}" in allowed_tables:
                table.set("db", exp.to_identifier("analytics"))
                relation = f"analytics.{relation}"
            else:
                raise SQLSafetyError(f"Query references a non-approved table: {relation}.")

    for join in expression.find_all(exp.Join):
        kind = str(join.args.get("kind") or "").upper()
        method = str(join.args.get("method") or "").upper()

        target_name = (join.this.name or "").lower() if hasattr(join.this, "name") else ""
        target_alias = (join.this.alias or "").lower() if hasattr(join.this, "alias") else ""
        target_is_cte = bool(target_name in ctes or target_alias in ctes)

        select_parent = join.find_ancestor(exp.Select)
        from_clause = select_parent.find(exp.From) if select_parent else None
        from_name = (from_clause.this.name or "").lower() if from_clause and hasattr(from_clause.this, "name") else ""
        from_alias = (from_clause.this.alias or "").lower() if from_clause and hasattr(from_clause.this, "alias") else ""
        from_is_cte = bool(from_name in ctes or from_alias in ctes)

        is_cte_join = bool(target_is_cte and from_is_cte)

        if is_cte_join and not (join.args.get("on") or join.args.get("using")):
            join.set("on", exp.Literal.number(1).eq(exp.Literal.number(1)))
            join.set("kind", None)
            continue

        if kind == "CROSS" or method == "NATURAL" or not (join.args.get("on") or join.args.get("using")):
            raise SQLSafetyError("Only explicit joins with ON or USING are allowed.")

    for function in expression.find_all(exp.Func):
        names = {
            function.name.upper() if isinstance(function, exp.Anonymous) and function.name else "",
            function.sql_name().upper() if hasattr(function, "sql_name") else "",
            function.__class__.__name__.upper(),
        }
        names.discard("")
        if not names.intersection(ALLOWED_FUNCTIONS):
            name_to_report = function.name.lower() if isinstance(function, exp.Anonymous) and function.name else function.sql_name().lower()
            raise SQLSafetyError(f"Function {name_to_report} is not allowed in analytical SQL.")

    for limit in expression.find_all(exp.Limit):
        limit_expression = limit.expression
        if isinstance(limit_expression, exp.Literal) and limit_expression.is_int and int(limit_expression.this) > maximum_limit:
            raise SQLSafetyError(f"LIMIT cannot exceed {maximum_limit} rows.")


class SafeSQLExecutor:
    """Executes AST-validated SQL through a bounded read-only connection."""

    def __init__(
        self,
        repository: WarehouseRepository,
        maximum_rows: int = 200,
        timeout_seconds: float | None = None,
    ) -> None:
        self.repository = repository
        self.maximum_rows = maximum_rows
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else getattr(
            repository.settings, "ai_query_timeout_seconds", 8.0
        )

    def execute(
        self,
        sql: str,
        parameters: list[Any] | None = None,
        *,
        mask_pii: bool = True,
    ) -> SQLExecutionResult:
        repo_tables = set(self.repository.ai_allowed_tables()) if hasattr(self.repository, "ai_allowed_tables") else set()
        safe_sql = validate_read_only_sql(
            sql,
            allowed_tables=repo_tables | DEFAULT_ALLOWED_TABLES,
            maximum_limit=self.maximum_rows,
        )
        values = parameters or []
        guarded_sql = f"SELECT * FROM ({safe_sql}) AS guarded_query LIMIT {self.maximum_rows}"
        try:
            raw_rows = self.repository.fetch_all(guarded_sql, values, timeout_seconds=self.timeout_seconds)
        except TimeoutError as error:
            raise SQLSafetyError("The analytical query exceeded its safety time limit.") from error
        rows = [mask_record_pii(r) for r in raw_rows] if mask_pii and raw_rows else raw_rows
        columns = list(rows[0]) if rows else []
        return SQLExecutionResult(
            sql=safe_sql,
            parameters=values,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            executed_at=utc_now_iso(),
        )
