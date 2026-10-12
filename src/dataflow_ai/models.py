"""Shared domain models and dataclasses for pipeline runs, quality results, and queries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class IngestionReceipt:
    dataset: str
    source_type: str
    source_uri: str
    storage_path: str
    row_count: int
    sha256: str
    ingested_at: str
    run_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityIssue:
    dataset: str
    row_number: int
    rule: str
    detail: str
    record: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetQualityResult:
    dataset: str
    rows_read: int
    accepted_records: list[dict[str, Any]] = field(default_factory=list)
    rejected_records: list[dict[str, Any]] = field(default_factory=list)
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_records)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_records)

    @property
    def quality_score(self) -> float:
        return round(100.0 * self.accepted_count / self.rows_read, 2) if self.rows_read else 100.0


@dataclass(frozen=True)
class PipelineRunSummary:
    run_id: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    ingested_rows: int
    accepted_rows: int
    rejected_rows: int
    quality_score: float
    receipts: list[IngestionReceipt]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["receipts"] = [receipt.to_dict() for receipt in self.receipts]
        return payload


@dataclass(frozen=True)
class SQLExecutionResult:
    sql: str
    parameters: list[Any]
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    executed_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
