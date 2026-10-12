"""Pydantic request and response contracts for the REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000, examples=["Why did revenue decrease in August 2026?"])


class AskResponse(BaseModel):
    answer: str
    sql: str | None
    parameters: list[Any]
    rows: list[dict[str, Any]]
    source_documents: list[str]
    provider: str
    request_id: str
    warning: str | None = None
    mode: str = "warehouse_query"
    data_grounded: bool = True

