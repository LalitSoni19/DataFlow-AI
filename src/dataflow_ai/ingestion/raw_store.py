"""Raw data storage and per-run manifest tracking."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dataflow_ai.models import IngestionReceipt
from dataflow_ai.utils import atomic_write_json, safe_name, sha256_file


class RawDataLake:
    """Stores incoming raw source files partitioned by date and run ID."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def store_file(
        self,
        *,
        dataset: str,
        source_type: str,
        source_path: Path,
        run_id: str,
        row_count: int,
    ) -> IngestionReceipt:
        if not source_path.is_file():
            raise FileNotFoundError(
                f"Source file does not exist: {source_path}"
            )
        destination = self._destination(dataset, run_id, source_path.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        shutil.copyfile(source_path, temporary)
        os.replace(temporary, destination)
        now_iso = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        receipt = IngestionReceipt(
            dataset=safe_name(dataset),
            source_type=source_type,
            source_uri=str(source_path.resolve()),
            storage_path=str(destination.resolve()),
            row_count=row_count,
            sha256=sha256_file(destination),
            ingested_at=now_iso,
            run_id=run_id,
        )
        self._write_manifest(receipt)
        return receipt

    def store_bytes(
        self,
        *,
        dataset: str,
        source_type: str,
        source_uri: str,
        content: bytes,
        extension: str,
        run_id: str,
        row_count: int,
    ) -> IngestionReceipt:
        suffix = extension if extension.startswith(".") else f".{extension}"
        destination = self._destination(dataset, run_id, f"payload{suffix}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
        now_iso = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        receipt = IngestionReceipt(
            dataset=safe_name(dataset),
            source_type=source_type,
            source_uri=source_uri,
            storage_path=str(destination.resolve()),
            row_count=row_count,
            sha256=sha256_file(destination),
            ingested_at=now_iso,
            run_id=run_id,
        )
        self._write_manifest(receipt)
        return receipt

    def _destination(self, dataset: str, run_id: str, filename: str) -> Path:
        date_partition = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return (
            self.root
            / safe_name(dataset)
            / f"ingest_date={date_partition}"
            / run_id
            / filename
        )

    def _write_manifest(self, receipt: IngestionReceipt) -> None:
        manifest_path = Path(receipt.storage_path).parent / "manifest.json"
        manifest: dict[str, Any]
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {"run_id": receipt.run_id, "assets": []}
        assets = manifest.get("assets")
        if isinstance(assets, list):
            assets.append(receipt.to_dict())
        else:
            manifest["assets"] = [receipt.to_dict()]
        atomic_write_json(manifest_path, manifest)


def count_csv_rows(content: bytes) -> int:
    if not content.strip():
        return 0
    try:
        stream = io.StringIO(content.decode("utf-8-sig"))
        return sum(1 for _ in csv.DictReader(stream))
    except UnicodeDecodeError:
        return 0


def count_json_records(content: bytes) -> int:
    if not content.strip():
        return 0
    try:
        parsed = json.loads(content.decode("utf-8"))
        return len(parsed) if isinstance(parsed, list) else 1
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
