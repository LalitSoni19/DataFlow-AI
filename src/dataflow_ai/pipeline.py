"""Pipeline execution: handles source ingestion, quality checks, and warehouse loading."""

from __future__ import annotations

import csv
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dataflow_ai.ingestion import (
    CsvIngestor,
    JsonIngestor,
    RawDataLake,
    is_supported_sales_file,
    load_flexible_sales_files,
)
from dataflow_ai.models import DatasetQualityResult, PipelineRunSummary
from dataflow_ai.processing import clean_records, validate_source_records
from dataflow_ai.sample_data import generate_sample_data
from dataflow_ai.settings import Settings
from dataflow_ai.utils import atomic_write_json, sanitize_secret_string, write_json_lines
from dataflow_ai.warehouse.repository import WarehouseRepository

LOGGER = logging.getLogger(__name__)


class PipelineRunner:
    """Orchestrates ingestion, data validation, and warehouse loading."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = WarehouseRepository(settings)
        self.lake = RawDataLake(settings.raw_dir)
        self.csv_ingestor = CsvIngestor(self.lake)
        self.json_ingestor = JsonIngestor(self.lake)

    def run_sample_pipeline(self, *, regenerate_sample: bool = False) -> PipelineRunSummary:
        if regenerate_sample or not (self.settings.sample_dir / "customers.csv").exists():
            generate_sample_data(self.settings.sample_dir)
        return self.run_from_sources(self.settings.sample_dir)

    def run_from_sources(self, source_dir: Path) -> PipelineRunSummary:
        """Ingest, validate, and load files from a source directory into the warehouse."""

        source_dir = source_dir.resolve()
        required_sources = {
            "customers": source_dir / "customers.csv",
            "products": source_dir / "products.csv",
            "orders": source_dir / "orders.csv",
            "order_items": source_dir / "order_items.csv",
            "website_events": source_dir / "website_events.json",
        }
        missing = [str(path) for path in required_sources.values() if not path.is_file()]
        if missing:
            flexible_paths = sorted(
                (path for path in source_dir.iterdir() if is_supported_sales_file(path)),
                key=lambda path: path.name.lower(),
            ) if source_dir.is_dir() else []
            if flexible_paths:
                return self.run_flexible_sales_files(flexible_paths)
            raise FileNotFoundError("Required source files are missing: " + ", ".join(missing))

        self.settings.ensure_directories()
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        started_timer = time.perf_counter()
        self.repository.start_run(run_id, started_at)
        receipts = []
        quality_results: dict[str, DatasetQualityResult] = {}
        try:
            for dataset, path in required_sources.items():
                ingestor = self.json_ingestor if path.suffix.lower() == ".json" else self.csv_ingestor
                receipts.append(ingestor.ingest(path, dataset, run_id))
            api_snapshot = source_dir / "campaigns_api_snapshot.json"
            if api_snapshot.is_file():
                receipts.append(self.json_ingestor.ingest(api_snapshot, "campaigns_api_snapshot", run_id))

            records_by_dataset = {
                dataset: clean_records(self._read_records(Path(receipt.storage_path)))
                for dataset, receipt in ((receipt.dataset, receipt) for receipt in receipts)
                if dataset in required_sources
            }
            quality_results = validate_source_records(records_by_dataset)
            self._persist_quality_outputs(run_id, quality_results)
            self.repository.load_validated_data(quality_results, run_id)

            finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            duration = round(time.perf_counter() - started_timer, 3)
            ingested_rows = sum(receipt.row_count for receipt in receipts)
            accepted_rows = sum(result.accepted_count for result in quality_results.values())
            rejected_rows = sum(result.rejected_count for result in quality_results.values())
            quality_score = round(
                100 * accepted_rows / (accepted_rows + rejected_rows), 2
            ) if accepted_rows + rejected_rows else 100.0
            self.repository.finish_run(
                run_id=run_id,
                status="completed",
                finished_at=finished_at,
                duration_seconds=duration,
                ingested_rows=ingested_rows,
                accepted_rows=accepted_rows,
                rejected_rows=rejected_rows,
                quality_score=quality_score,
            )
            summary = PipelineRunSummary(
                run_id=run_id,
                status="completed",
                started_at=started_at.isoformat() + "Z",
                finished_at=finished_at.isoformat() + "Z",
                duration_seconds=duration,
                ingested_rows=ingested_rows,
                accepted_rows=accepted_rows,
                rejected_rows=rejected_rows,
                quality_score=quality_score,
                receipts=receipts,
            )
            atomic_write_json(self.settings.processed_dir / f"run-{run_id}.json", summary.to_dict())
            LOGGER.info("Pipeline run %s completed with quality score %.2f", run_id, quality_score)
            return summary
        except Exception as error:
            duration = round(time.perf_counter() - started_timer, 3)
            self.repository.finish_run(
                run_id=run_id,
                status="failed",
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                duration_seconds=duration,
                ingested_rows=sum(receipt.row_count for receipt in receipts),
                accepted_rows=sum(result.accepted_count for result in quality_results.values()),
                rejected_rows=sum(result.rejected_count for result in quality_results.values()),
                quality_score=0,
                error_message=sanitize_secret_string(str(error))[:2_000],
            )
            LOGGER.exception("Pipeline run %s failed", run_id)
            raise

    def run_flexible_sales_files(self, source_paths: list[Path]) -> PipelineRunSummary:
        """Load arbitrary sales exports into the raw lake and warehouse tables."""

        resolved_paths = [Path(path).resolve() for path in source_paths]
        sales_import = load_flexible_sales_files(resolved_paths)
        self.settings.ensure_directories()
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        started_timer = time.perf_counter()
        self.repository.start_run(run_id, started_at)
        receipts = []
        try:
            for path in resolved_paths:
                suffix = path.suffix.lower()
                source_type = "spreadsheet" if suffix == ".xlsx" else ("json" if suffix in {".json", ".jsonl"} else "csv")
                receipts.append(
                    self.lake.store_file(
                        dataset="flexible_sales",
                        source_type=source_type,
                        source_path=path,
                        run_id=run_id,
                        row_count=sales_import.profile.source_row_counts.get(path.name, 0),
                    )
                )
            self.repository.load_flexible_sales(sales_import, run_id)
            self._persist_flexible_sales_profile(run_id, sales_import.profile.to_dict())
            finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            duration = round(time.perf_counter() - started_timer, 3)
            ingested_rows = sales_import.profile.row_count
            accepted_rows = sales_import.profile.accepted_row_count
            rejected_rows = sales_import.profile.rejected_row_count
            quality_score = round(100 * accepted_rows / ingested_rows, 2) if ingested_rows else 100.0
            self.repository.finish_run(
                run_id=run_id,
                status="completed",
                finished_at=finished_at,
                duration_seconds=duration,
                ingested_rows=ingested_rows,
                accepted_rows=accepted_rows,
                rejected_rows=rejected_rows,
                quality_score=quality_score,
            )
            summary = PipelineRunSummary(
                run_id=run_id,
                status="completed",
                started_at=started_at.isoformat() + "Z",
                finished_at=finished_at.isoformat() + "Z",
                duration_seconds=duration,
                ingested_rows=ingested_rows,
                accepted_rows=accepted_rows,
                rejected_rows=rejected_rows,
                quality_score=quality_score,
                receipts=receipts,
            )
            atomic_write_json(self.settings.processed_dir / f"run-{run_id}.json", summary.to_dict())
            LOGGER.info("Flexible sales import %s completed with %s row(s)", run_id, accepted_rows)
            return summary
        except Exception as error:
            duration = round(time.perf_counter() - started_timer, 3)
            self.repository.finish_run(
                run_id=run_id,
                status="failed",
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                duration_seconds=duration,
                ingested_rows=sales_import.profile.row_count,
                accepted_rows=0,
                rejected_rows=sales_import.profile.row_count,
                quality_score=0,
                error_message=sanitize_secret_string(str(error))[:2_000],
            )
            LOGGER.exception("Flexible sales import %s failed", run_id)
            raise

    def _persist_flexible_sales_profile(self, run_id: str, profile: dict[str, Any]) -> None:
        destination = self.settings.processed_dir / run_id / "sales-import-profile.json"
        atomic_write_json(destination, profile)

    def _persist_quality_outputs(self, run_id: str, results: dict[str, DatasetQualityResult]) -> None:
        for dataset, result in results.items():
            write_json_lines(self.settings.processed_dir / run_id / f"{dataset}.jsonl", result.accepted_records)
            if result.rejected_records:
                write_json_lines(self.settings.quarantine_dir / run_id / f"{dataset}.jsonl", result.rejected_records)
        atomic_write_json(
            self.settings.processed_dir / run_id / "quality-summary.json",
            {
                dataset: {
                    "rows_read": result.rows_read,
                    "accepted_rows": result.accepted_count,
                    "rejected_rows": result.rejected_count,
                    "quality_score": result.quality_score,
                    "issue_count": len(result.issues),
                }
                for dataset, result in results.items()
            },
        )

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                return list(csv.DictReader(stream))
        if path.suffix.lower() == ".json":
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, list) and all(isinstance(row, dict) for row in parsed):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        raise ValueError(f"Unsupported record source: {path}")
