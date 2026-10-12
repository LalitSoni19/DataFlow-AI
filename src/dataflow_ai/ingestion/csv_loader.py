"""CSV ingestion adapter."""

from __future__ import annotations

import csv
from pathlib import Path

from dataflow_ai.ingestion.raw_store import RawDataLake, count_csv_rows
from dataflow_ai.models import IngestionReceipt


class CsvIngestor:
    def __init__(self, lake: RawDataLake) -> None:
        self.lake = lake

    def ingest(self, source_path: Path, dataset: str, run_id: str) -> IngestionReceipt:
        content = source_path.read_bytes()
        self._validate_header(content, source_path)
        return self.lake.store_file(
            dataset=dataset,
            source_type="csv",
            source_path=source_path,
            run_id=run_id,
            row_count=count_csv_rows(content),
        )

    @staticmethod
    def _validate_header(content: bytes, source_path: Path) -> None:
        try:
            rows = csv.reader(content.decode("utf-8-sig").splitlines())
            header = next(rows, [])
        except UnicodeDecodeError as error:
            raise ValueError(f"CSV must be UTF-8 encoded: {source_path}") from error
        if not header or not any(column.strip() for column in header):
            raise ValueError(f"CSV has no usable header: {source_path}")
        if len(header) != len(set(header)):
            raise ValueError(f"CSV has duplicate columns: {source_path}")

