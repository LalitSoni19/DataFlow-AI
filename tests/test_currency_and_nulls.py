from decimal import Decimal
from dataflow_ai.ingestion.flexible_sales import _parse_number, _detect_currency_symbol, _canonical_row
from dataflow_ai.utils import mask_record_pii
from dataflow_ai.warehouse.repository import WarehouseRepository
from dataflow_ai.settings import Settings


def test_currency_symbol_detection() -> None:
    assert _detect_currency_symbol("$1,200.50") == "$"
    assert _detect_currency_symbol("USD 1200") == "$"
    assert _detect_currency_symbol("1200 USD") == "$"
    assert _detect_currency_symbol("₹5,000") == "₹"
    assert _detect_currency_symbol("INR 5000") == "₹"
    assert _detect_currency_symbol("Rs. 5000") == "₹"
    assert _detect_currency_symbol("5000 Rupees") == "₹"
    assert _detect_currency_symbol("€250") == "€"
    assert _detect_currency_symbol("£100") == "£"


def test_parse_number_with_currency_tokens() -> None:
    assert _parse_number("$1,200.50") == Decimal("1200.50")
    assert _parse_number("USD 1,200.50") == Decimal("1200.50")
    assert _parse_number("1,200.50 USD") == Decimal("1200.50")
    assert _parse_number("₹5,000") == Decimal("5000")
    assert _parse_number("INR 5,000") == Decimal("5000")
    assert _parse_number("Rs. 5,000") == Decimal("5000")
    assert _parse_number("(500.00)") == Decimal("-500.00")
    # Identifiers should still be safely rejected
    assert _parse_number("INV-001") is None
    assert _parse_number("SKU-999") is None


def test_customer_name_visible_and_pii_masked() -> None:
    record = {
        "customer_name": "Aarav Sharma",
        "phone": "+91-9876543210",
        "mobile": "9876543210",
        "email": "aarav.sharma@example.com",
        "address": "42 MG Road, Bengaluru",
        "revenue": 1500.0,
    }
    masked = mask_record_pii(record)
    # Customer name MUST be visible
    assert masked["customer_name"] == "Aarav Sharma"
    # Confidential data MUST be masked
    assert masked["phone"] == "[REDACTED_PHONE]"
    assert masked["mobile"] == "[REDACTED_PHONE]"
    assert masked["address"] == "[REDACTED_ADDRESS]"
    assert masked["email"] == "a***@example.com"
    assert masked["revenue"] == 1500.0


def test_null_handling_in_records() -> None:
    record_with_nulls = {
        "customer_name": None,
        "region": "",
        "status": None,
        "revenue": 200.0,
    }
    masked = mask_record_pii(record_with_nulls)
    # Null customer name should cleanly default to Unknown without throwing an error
    assert masked["customer_name"] == "Unknown"
    assert masked["region"] == "Unspecified"
    assert masked["status"] == "completed"
    assert masked["revenue"] == 200.0


def test_canonical_row_currency_and_null_defaults() -> None:
    row = {
        "order_id": "ORD-123",
        "customer": None,
        "product": "Widget A",
        "amount": "$250.00",
    }
    canonical = _canonical_row(
        row,
        source_file="test.csv",
        source_row=2,
        mapping={"order_id": "order_id", "product": "product", "revenue": "amount"},
        mapped_columns={"order_id", "product", "amount"},
    )
    assert canonical["order_id"] == "ORD-123"
    assert canonical["customer"] == "Unknown"
    assert canonical["product"] == "Widget A"
    assert canonical["revenue"] == 250.0
    assert canonical["currency"] == "$"
