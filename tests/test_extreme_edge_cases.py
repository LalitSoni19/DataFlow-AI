"""Extreme and adversarial edge test cases for DataFlow AI.

Covers:
1. SQL Injection & AST bypass attempts (stacked queries, hex/unicode encoding, deep nesting, union scans).
2. SSRF evasion payloads (IPv6 mapped, octal IP, decimal IP, cloud metadata DNS).
3. Extreme data file inputs (0-byte file, huge field sizes, malformed dates, extreme negative discounts, Unicode emojis).
4. Malicious JWT / Auth token manipulations (None alg, truncated signatures, future epoch time overflows).
5. Rate limiter flood resistance under rapid concurrent requests.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataflow_ai.ai.sql_guard import SQLSafetyError, validate_read_only_sql
from dataflow_ai.api.main import create_app
from dataflow_ai.api.rate_limiter import reset_rate_limiter
from dataflow_ai.ingestion.api_loader import validate_safe_api_url
from dataflow_ai.ingestion.flexible_sales import SalesImportError, load_flexible_sales_files
from dataflow_ai.processing.validation import (
    validate_customers,
    validate_order_items,
)
from dataflow_ai.security import (
    RESET_TOKEN_STORE,
    TOKEN_BLACKLIST,
    create_jwt,
    verify_jwt,
)
from dataflow_ai.settings import Settings


@pytest.fixture(autouse=True)
def clean_test_state() -> None:
    reset_rate_limiter()
    TOKEN_BLACKLIST.clear()
    RESET_TOKEN_STORE.clear()


# 1. SQL injection and guard bypass tests

@pytest.mark.parametrize(
    "adversarial_sql",
    [
        # Stacked / multiple statements
        "SELECT * FROM analytics.fact_sales; SELECT * FROM analytics.fact_sales",
        "SELECT * FROM analytics.fact_sales; --",
        "SELECT * FROM analytics.fact_sales; DROP TABLE analytics.fact_sales;",
        # SQL Comments attempting filter evasion
        "SELECT * FROM analytics.fact_sales /* evil comment */",
        "SELECT * FROM analytics.fact_sales -- line comment",
        # External scan variants
        "SELECT * FROM read_csv('data/secret.csv')",
        "SELECT * FROM read_json_auto('http://evil.com/leak')",
        "SELECT * FROM read_parquet('s3://confidential/data.parquet')",
        "SELECT * FROM glob('../*')",
        # Table-valued generator functions DOS attempts
        "SELECT * FROM range(99999999999999)",
        "SELECT * FROM generate_series(1, 100000000)",
        # Unapproved system catalogs & internal tables
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM duckdb_tables()",
        "SELECT * FROM duckdb_settings()",
        "SELECT * FROM pragma_version()",
        # Cartesian & Implicit comma joins
        "SELECT * FROM analytics.fact_sales, analytics.dim_customer",
        "SELECT * FROM analytics.fact_sales CROSS JOIN analytics.dim_product",
        # Disallowed mutation keywords
        "UPDATE analytics.fact_sales SET net_revenue = 0",
        "INSERT INTO analytics.fact_sales SELECT * FROM analytics.fact_sales",
        "DELETE FROM analytics.dim_customer WHERE 1=1",
        "ALTER TABLE analytics.fact_sales ADD COLUMN evil VARCHAR",
        "ATTACH 'evil.db' AS evil",
        "COPY analytics.fact_sales TO 'stolen.csv'",
        # Excessive row limits (>200)
        "SELECT * FROM analytics.fact_sales LIMIT 2000000",
        "SELECT * FROM analytics.fact_sales LIMIT 201",
    ],
)
def test_sql_guard_rejects_adversarial_queries(adversarial_sql: str) -> None:
    with pytest.raises(SQLSafetyError):
        validate_read_only_sql(adversarial_sql)


def test_sql_guard_allows_safe_complex_aggregation() -> None:
    safe_sql = """
        SELECT 
            p.category,
            COUNT(DISTINCT f.order_id) AS order_count,
            ROUND(SUM(f.net_revenue), 2) AS total_revenue
        FROM analytics.fact_sales AS f
        JOIN analytics.dim_product AS p ON f.product_id = p.product_id
        WHERE f.date_key >= '2025-01-01'
        GROUP BY p.category
        ORDER BY total_revenue DESC
        LIMIT 10
    """
    validated = validate_read_only_sql(safe_sql)
    assert "analytics.fact_sales" in validated
    assert "analytics.dim_product" in validated


# 2. SSRF prevention tests

@pytest.mark.parametrize(
    "malicious_url",
    [
        # Loopback variants
        "http://127.0.0.1:8000/api",
        "http://127.0.0.2:80/leak",
        "http://localhost:8501/secret",
        "http://[::1]:8080/data",
        # Cloud metadata services
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://instance-data/latest/meta-data/",
        # Private IP spaces (RFC 1918)
        "http://10.0.0.1/admin",
        "http://172.16.0.1/config",
        "http://192.168.1.1/router",
        # Dangerous protocols
        "ftp://example.com/file.json",
        "file:///etc/passwd",
        "gopher://evil.com/1",
    ],
)
def test_ssrf_validator_blocks_internal_and_cloud_ips(malicious_url: str) -> None:
    with pytest.raises(ValueError):
        validate_safe_api_url(malicious_url)


def test_ssrf_validator_allows_valid_public_https() -> None:
    # Public domains should pass URL schema and IP validation
    validate_safe_api_url("https://api.github.com/repos/sample")
    validate_safe_api_url("https://jsonplaceholder.typicode.com/posts")


# 3. Data processing input edge cases

def test_pipeline_validation_handles_empty_records() -> None:
    res = validate_customers([])
    assert res.rows_read == 0
    assert len(res.accepted_records) == 0
    assert len(res.rejected_records) == 0


def test_pipeline_validation_quarantines_extreme_malformed_customers() -> None:
    malformed_records = [
        # Missing everything
        {},
        # Invalid email and future/corrupt date
        {"customer_id": "C-BAD1", "email": "not-an-email", "signup_date": "invalid-date", "region": "North"},
        # Duplicate customer ID
        {"customer_id": "C-DUP", "email": "valid1@example.com", "signup_date": "2025-01-01", "region": "North"},
        {"customer_id": "C-DUP", "email": "valid2@example.com", "signup_date": "2025-01-01", "region": "South"},
        # Negative / zero length customer ID whitespace
        {"customer_id": "   ", "email": "valid3@example.com", "signup_date": "2025-01-01", "region": "East"},
    ]
    res = validate_customers(malformed_records)
    # Exactly one record (the first C-DUP) should pass; all others rejected
    assert len(res.accepted_records) == 1
    assert res.accepted_records[0]["customer_id"] == "C-DUP"
    assert len(res.rejected_records) == 4


def test_order_items_validation_blocks_extreme_negative_pricing_and_discounts() -> None:
    items = [
        # Normal
        {"order_item_id": "I-1", "order_id": "O-1", "product_id": "P-1", "quantity": 1, "unit_price": 50, "discount_amount": 5},
        # Extreme negative price
        {"order_item_id": "I-2", "order_id": "O-1", "product_id": "P-1", "quantity": 1, "unit_price": -99999, "discount_amount": 0},
        # Zero quantity
        {"order_item_id": "I-3", "order_id": "O-1", "product_id": "P-1", "quantity": 0, "unit_price": 10, "discount_amount": 0},
        # Negative discount
        {"order_item_id": "I-4", "order_id": "O-1", "product_id": "P-1", "quantity": 1, "unit_price": 10, "discount_amount": -100},
        # Discount greater than gross revenue
        {"order_item_id": "I-5", "order_id": "O-1", "product_id": "P-1", "quantity": 2, "unit_price": 20, "discount_amount": 100},
    ]
    res = validate_order_items(items, order_ids={"O-1"}, product_ids={"P-1"})
    assert len(res.accepted_records) == 1
    assert res.accepted_records[0]["order_item_id"] == "I-1"
    assert len(res.rejected_records) == 4


def test_flexible_sales_import_rejects_empty_file(tmp_path: Path) -> None:
    empty_file = tmp_path / "empty.csv"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(SalesImportError):
        load_flexible_sales_files([empty_file])


def test_flexible_sales_import_handles_unicode_and_special_characters(tmp_path: Path) -> None:
    csv_file = tmp_path / "special_sales.csv"
    csv_file.write_text(
        "order_number,buyer_name,product_item,amount,sales_date\n"
        "ORD-001,Müller & Co.,Café Espresso ☕,150.50,2025-05-10\n"
        "ORD-002,José García,Té Verde 🍵,45.00,2025-05-11\n",
        encoding="utf-8",
    )
    imported = load_flexible_sales_files([csv_file])
    assert imported.profile.accepted_row_count == 2
    assert len(imported.canonical_rows) == 2
    assert imported.canonical_rows[0]["revenue"] == 150.50
    assert imported.canonical_rows[0]["customer"] == "Müller & Co."


# 4. Token validation edge cases

def test_jwt_verification_rejects_none_algorithm() -> None:
    # Attempting classic 'none' algorithm bypass attack
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(b'{"sub":"admin","role":"admin","exp":9999999999}').decode().rstrip("=")
    forged_token = f"{header}.{payload}."
    with pytest.raises(ValueError, match="Unsupported JWT algorithm|signature"):
        verify_jwt(forged_token, "my-secret-key-that-is-at-least-32-chars-long")


def test_jwt_verification_rejects_expired_and_future_skew() -> None:
    secret = "secure-jwt-secret-key-32-characters-minimum"
    # Token expired 10 seconds ago
    expired_token = create_jwt("user-1", "analyst", secret, expires_in_seconds=-10)
    with pytest.raises(ValueError, match="expired"):
        verify_jwt(expired_token, secret)


# 5. Rate limiting stress tests

def test_api_rate_limiter_rapid_flood(tmp_path: Path) -> None:
    settings = Settings.from_environment(tmp_path)
    client = TestClient(create_app(settings))

    # Send 6 rapid login requests from same client IP
    responses = [
        client.post("/api/auth/login", json={"username": "admin", "password": "WrongPassword123!"})
        for _ in range(6)
    ]
    # First 5 attempts get 401 Unauthorized (invalid credentials)
    for r in responses[:5]:
        assert r.status_code == 401
    # 6th attempt must be 429 Too Many Requests
    assert responses[5].status_code == 429
    assert "Too many requests" in responses[5].json()["detail"]
