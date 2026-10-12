"""JSON ingestion adapter."""

from __future__ import annotations

import json
from pathlib import Path

from dataflow_ai.ingestion.raw_store import RawDataLake, count_json_records
from dataflow_ai.models import IngestionReceipt


class JsonIngestor:
    def __init__(self, lake: RawDataLake) -> None:
        self.lake = lake

    def ingest(self, source_path: Path, dataset: str, run_id: str) -> IngestionReceipt:
        content = source_path.read_bytes()
        try:
            parsed = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid UTF-8 JSON source: {source_path}") from error
        if not isinstance(parsed, (list, dict)):
            raise TypeError("JSON source must contain an object or array.")
        return self.lake.store_file(
            dataset=dataset,
            source_type="json",
            source_path=source_path,
            run_id=run_id,
            row_count=count_json_records(content),
        )

