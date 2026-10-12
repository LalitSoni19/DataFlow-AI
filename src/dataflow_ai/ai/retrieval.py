"""Lightweight local retrieval over business and schema docs."""

from __future__ import annotations

import math
import re
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{1,}")


@dataclass(frozen=True)
class RetrievedDocument:
    source: str
    text: str
    score: float


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


class MetadataRetriever:
    """Hash-vector retrieval without external vector DB or API key."""

    def __init__(
        self,
        documents: Mapping[str, str] | dict[str, str],
        dimensions: int = 256,
    ) -> None:
        self.documents = dict(documents)
        self.dimensions = dimensions
        self._vectors = {
            source: self._embed(text)
            for source, text in self.documents.items()
        }

    @classmethod
    def from_project_docs(cls, docs_dir: Path) -> MetadataRetriever:
        documents: dict[str, str] = {
            str(path.relative_to(docs_dir)): path.read_text(encoding="utf-8")
            for path in docs_dir.glob("*.md")
        }
        if not documents:
            documents = {"built_in_metadata": str(_DEFAULT_METADATA)}
        return cls(documents)

    def search(self, question: str, limit: int = 3) -> list[RetrievedDocument]:
        query = self._embed(question)
        scored = [
            RetrievedDocument(
                source=source,
                text=text,
                score=self._cosine(query, self._vectors[source]),
            )
            for source, text in self.documents.items()
        ]
        sorted_scored = sorted(
            scored, key=lambda item: item.score, reverse=True
        )
        return [item for item in sorted_scored[:limit] if item.score > 0]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            vector[zlib.crc32(token.encode("utf-8")) % self.dimensions] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right, strict=True))


_DEFAULT_METADATA = (
    "fact_sales contains completed order lines. net_revenue equals quantity "
    "times unit_price minus discount_amount.\n"
    "dim_customer contains customer and region attributes. dim_product "
    "contains product and category attributes.\n"
    "Revenue excludes cancelled and refunded orders because fact_sales only "
    "contains completed orders.\n"
    "monthly_sales contains monthly aggregate revenue by region. All "
    "analysis must use analytics schema tables."
)
