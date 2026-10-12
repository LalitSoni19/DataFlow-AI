"""Validation rules and schema checks for raw datasets."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from dataflow_ai.models import DatasetQualityResult, QualityIssue

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_ORDER_STATUSES = {"completed", "cancelled", "refunded"}


def _required(record: dict[str, Any], *fields: str) -> list[tuple[str, str]]:
    return [("required", f"{field} is required") for field in fields if not str(record.get(field, "")).strip()]


def _valid_date(value: Any) -> bool:
    if value is None:
        return False
    try:
        datetime.strptime(str(value), "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return True


def _positive(record: dict[str, Any], field: str, *, allow_zero: bool = False) -> list[tuple[str, str]]:
    try:
        numeric = float(record[field])
    except (KeyError, TypeError, ValueError):
        return [("numeric", f"{field} must be numeric")]
    valid = numeric >= 0 if allow_zero else numeric > 0
    return [] if valid else [("range", f"{field} must be {'non-negative' if allow_zero else 'greater than zero'}")]


def _validate(
    dataset: str,
    records: list[dict[str, Any]],
    rule: Callable[[dict[str, Any], int], list[tuple[str, str]]],
) -> DatasetQualityResult:
    result = DatasetQualityResult(dataset=dataset, rows_read=len(records))
    for row_number, record in enumerate(records, start=2):
        failures = rule(record, row_number)
        if not failures:
            result.accepted_records.append(record)
            continue
        issues = [
            QualityIssue(dataset=dataset, row_number=row_number, rule=code, detail=detail, record=record)
            for code, detail in failures
        ]
        result.issues.extend(issues)
        result.rejected_records.append(
            {"record": record, "issues": [{"rule": issue.rule, "detail": issue.detail} for issue in issues]}
        )
    return result


def validate_customers(records: list[dict[str, Any]]) -> DatasetQualityResult:
    seen: set[str] = set()

    def rule(record: dict[str, Any], _: int) -> list[tuple[str, str]]:
        failures = _required(record, "customer_id", "email", "signup_date", "region")
        customer_id = str(record.get("customer_id", ""))
        if customer_id in seen:
            failures.append(("unique", "customer_id is duplicated"))
        elif customer_id:
            seen.add(customer_id)
        if record.get("email") and not EMAIL_PATTERN.match(str(record["email"])):
            failures.append(("format", "email is not valid"))
        if record.get("signup_date") and not _valid_date(record["signup_date"]):
            failures.append(("format", "signup_date must use YYYY-MM-DD"))
        return failures

    return _validate("customers", records, rule)


def validate_products(records: list[dict[str, Any]]) -> DatasetQualityResult:
    seen: set[str] = set()

    def rule(record: dict[str, Any], _: int) -> list[tuple[str, str]]:
        failures = _required(record, "product_id", "product_name", "category", "list_price", "unit_cost")
        product_id = str(record.get("product_id", ""))
        if product_id in seen:
            failures.append(("unique", "product_id is duplicated"))
        elif product_id:
            seen.add(product_id)
        for field in ("list_price", "unit_cost"):
            if record.get(field) not in (None, ""):
                failures.extend(_positive(record, field, allow_zero=True))
        return failures

    return _validate("products", records, rule)


def validate_orders(records: list[dict[str, Any]], customer_ids: set[str]) -> DatasetQualityResult:
    seen: set[str] = set()

    def rule(record: dict[str, Any], _: int) -> list[tuple[str, str]]:
        failures = _required(record, "order_id", "customer_id", "order_date", "status", "region")
        order_id = str(record.get("order_id", ""))
        if order_id in seen:
            failures.append(("unique", "order_id is duplicated"))
        elif order_id:
            seen.add(order_id)
        if record.get("customer_id") and record["customer_id"] not in customer_ids:
            failures.append(("relationship", "customer_id does not exist in accepted customers"))
        if record.get("order_date") and not _valid_date(record["order_date"]):
            failures.append(("format", "order_date must use YYYY-MM-DD"))
        if record.get("status") and record["status"] not in VALID_ORDER_STATUSES:
            failures.append(("accepted_values", "status must be completed, cancelled, or refunded"))
        return failures

    return _validate("orders", records, rule)


def validate_order_items(
    records: list[dict[str, Any]],
    order_ids: set[str],
    product_ids: set[str] | dict[str, float],
    catalog_prices: dict[str, float] | None = None,
) -> DatasetQualityResult:
    seen: set[str] = set()
    if isinstance(product_ids, dict):
        catalog = catalog_prices or product_ids
        valid_products = set(product_ids.keys())
    else:
        catalog = catalog_prices or {}
        valid_products = set(product_ids)

    def rule(record: dict[str, Any], _: int) -> list[tuple[str, str]]:
        failures = _required(record, "order_item_id", "order_id", "product_id", "quantity", "unit_price")
        item_id = str(record.get("order_item_id", ""))
        if item_id in seen:
            failures.append(("unique", "order_item_id is duplicated"))
        elif item_id:
            seen.add(item_id)
        if record.get("order_id") and record["order_id"] not in order_ids:
            failures.append(("relationship", "order_id does not exist in accepted orders"))
        if record.get("product_id") and record["product_id"] not in valid_products:
            failures.append(("relationship", "product_id does not exist in accepted products"))
        for field in ("quantity", "unit_price"):
            if record.get(field) not in (None, ""):
                failures.extend(_positive(record, field))
        if record.get("discount_amount") not in (None, ""):
            failures.extend(_positive(record, "discount_amount", allow_zero=True))

        # sanity checks on price & discount against catalog
        try:
            qty = float(record.get("quantity") or 0)
            submitted_price = float(record.get("unit_price") or 0)
            discount = float(record.get("discount_amount") or 0)
            pid = str(record.get("product_id", ""))

            # price should roughly align with list price
            if pid in catalog:
                list_price = float(catalog[pid])
                if submitted_price < (list_price * 0.50):
                    failures.append((
                        "price_tampering",
                        f"unit_price {submitted_price} is anomalously below catalog price {list_price} (min allowed: {list_price * 0.50:.2f})",
                    ))
                elif submitted_price > (list_price * 2.0):
                    failures.append((
                        "price_tampering",
                        f"unit_price {submitted_price} exceeds maximum catalog ceiling {list_price * 2.0:.2f}",
                    ))

            # discount cannot exceed line total or 50% max policy cap
            if qty > 0 and submitted_price > 0:
                gross_total = qty * submitted_price
                if discount > gross_total:
                    failures.append((
                        "discount_tampering",
                        f"discount_amount ({discount}) cannot exceed line gross total ({gross_total})",
                    ))
                elif discount > (gross_total * 0.50):
                    failures.append((
                        "discount_policy",
                        f"discount_amount ({discount}) exceeds maximum allowed 50% discount cap ({gross_total * 0.50:.2f})",
                    ))
        except (ValueError, TypeError):
            pass

        return failures

    return _validate("order_items", records, rule)


def validate_events(records: list[dict[str, Any]], customer_ids: set[str]) -> DatasetQualityResult:
    seen: set[str] = set()

    def rule(record: dict[str, Any], _: int) -> list[tuple[str, str]]:
        failures = _required(record, "event_id", "customer_id", "timestamp", "event_type")
        event_id = str(record.get("event_id", ""))
        if event_id in seen:
            failures.append(("unique", "event_id is duplicated"))
        elif event_id:
            seen.add(event_id)
        if record.get("customer_id") and record["customer_id"] not in customer_ids:
            failures.append(("relationship", "customer_id does not exist in accepted customers"))
        try:
            datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            failures.append(("format", "timestamp must be ISO-8601"))
        return failures

    return _validate("website_events", records, rule)


def validate_source_records(records_by_dataset: dict[str, list[dict[str, Any]]]) -> dict[str, DatasetQualityResult]:
    """Validate datasets and check referential integrity across related tables."""

    customers = validate_customers(records_by_dataset["customers"])
    products = validate_products(records_by_dataset["products"])
    orders = validate_orders(
        records_by_dataset["orders"], {row["customer_id"] for row in customers.accepted_records}
    )
    catalog_prices = {
        row["product_id"]: float(row["list_price"])
        for row in products.accepted_records
        if "product_id" in row and "list_price" in row
    }
    items = validate_order_items(
        records_by_dataset["order_items"],
        {row["order_id"] for row in orders.accepted_records},
        {row["product_id"] for row in products.accepted_records},
        catalog_prices=catalog_prices,
    )
    events = validate_events(
        records_by_dataset.get("website_events", []),
        {row["customer_id"] for row in customers.accepted_records},
    )
    return {"customers": customers, "products": products, "orders": orders, "order_items": items, "website_events": events}


