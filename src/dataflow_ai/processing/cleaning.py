"""Field normalization and basic data cleanup."""

from __future__ import annotations

from typing import Any

TEXT_FIELDS = {
    "customer_id",
    "customer_name",
    "email",
    "city",
    "state",
    "country",
    "region",
    "segment",
    "product_id",
    "product_name",
    "category",
    "subcategory",
    "supplier",
    "order_id",
    "order_item_id",
    "status",
    "payment_method",
    "marketing_channel",
    "event_id",
    "event_type",
    "page",
    "device",
}


def _clean_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if key in {"email", "status", "payment_method", "marketing_channel", "event_type", "device"}:
            return value.lower()
        if key in {"region", "category", "segment", "country", "city", "state"}:
            return " ".join(part.capitalize() for part in value.split())
        return value
    return value


def clean_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean keys, normalize strings, and set fallback placeholders for missing optional fields."""

    cleaned: list[dict[str, Any]] = []
    for record in records:
        normalized = {}
        for key, value in record.items():
            clean_key = key.strip().lower().replace(" ", "_")
            normalized[clean_key] = _clean_value(clean_key, value)

        # fallback defaults for missing non-critical attributes
        if "customer_name" in normalized and not normalized["customer_name"]:
            normalized["customer_name"] = "Unknown"
        elif "customer_id" in normalized and "customer_name" not in normalized:
            normalized["customer_name"] = "Unknown"

        if "region" in normalized and not normalized["region"]:
            normalized["region"] = "Unspecified"
        if "category" in normalized and not normalized["category"]:
            normalized["category"] = "Unspecified"
        if "status" in normalized and not normalized["status"]:
            normalized["status"] = "completed"

        cleaned.append(normalized)
    return cleaned

