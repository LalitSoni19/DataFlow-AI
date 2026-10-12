"""Utility helpers for serialization, sanitization, and PII masking."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")
    os.replace(temporary, path)


def write_json_lines(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, default=json_default) + "\n")
    os.replace(temporary, path)


def json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()


def safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    if not normalized:
        raise ValueError("A dataset name must contain at least one letter or number.")
    return normalized


SENSITIVE_PARAM_NAMES = frozenset(
    {"api_key", "apikey", "key", "token", "secret", "password", "auth", "access_token", "refresh_token"}
)

BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]+")
BASIC_PATTERN = re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]+")
KEY_PATTERN = re.compile(r"\b(sk-[a-zA-Z0-9_\-]{8,})\b")
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9-_=]+\.eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*\b")
CONN_PWD_PATTERN = re.compile(r"(://[^:@\s]+):([^@\s]+)@")


def sanitize_secret_string(text: str | None) -> str:
    """Mask common secret patterns (bearer tokens, API keys, passwords) in error messages and logs."""
    if not text:
        return ""
    result = BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    result = BASIC_PATTERN.sub("Basic [REDACTED]", result)
    result = KEY_PATTERN.sub("sk-[REDACTED]", result)
    result = JWT_PATTERN.sub("[JWT-REDACTED]", result)
    result = CONN_PWD_PATTERN.sub(r"\1:***@", result)
    return result


def sanitize_uri(uri: str | None) -> str:
    """Scrub passwords and sensitive query parameter values from database or API URIs."""
    if not uri:
        return ""

    try:
        parts = urlsplit(uri)
        if not parts.scheme:
            return sanitize_secret_string(uri)

        # redact embedded user:password
        netloc = parts.netloc
        if "@" in netloc and ":" in netloc.split("@")[0]:
            userpass, hostport = netloc.split("@", 1)
            user = userpass.split(":", 1)[0]
            netloc = f"{user}:***@{hostport}"

        # redact sensitive query params (api_key, token, secret, etc.)
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)
        sanitized_pairs = [
            (k, "[REDACTED]" if k.lower() in SENSITIVE_PARAM_NAMES else v)
            for k, v in query_pairs
        ]
        sanitized_query = urlencode(sanitized_pairs, safe="[]")

        return urlunsplit((parts.scheme, netloc, parts.path, sanitized_query, parts.fragment))
    except Exception:
        return sanitize_secret_string(uri)


EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
PHONE_REGEX = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
PII_FIELD_NAMES = frozenset({
    "email", "email_address", "customer_name", "customer", "client", "client_name", "buyer",
    "contact", "contact_name", "full_name", "name", "phone", "phone_number", "mobile", "address",
    "street", "postal_code", "zip", "zip_code",
})
CONTEXTUAL_NAME_REGEX = re.compile(
    r"\b(?:customer|client|buyer|contact)\s+(?:named\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
)


def mask_email(email: str | None) -> str:
    """Mask email for privacy, e.g. 'alice.smith@example.com' -> 'a***@example.com'."""
    if not email or "@" not in email:
        return email or ""
    user, domain = email.split("@", 1)
    if len(user) <= 1:
        masked_user = "*"
    else:
        masked_user = user[0] + "***"
    return f"{masked_user}@{domain}"


def mask_name(name: str | None) -> str:
    """Mask personal name for privacy, e.g. 'John Doe' -> 'J*** D***'."""
    if not name:
        return ""
    parts = name.strip().split()
    if not parts:
        return ""
    masked_parts = [f"{p[0]}***" if len(p) > 1 else p for p in parts]
    return " ".join(masked_parts)


def redact_pii_text(text: str | None) -> str:
    """Redact email addresses, phone numbers, customer names, and secrets from free-form text strings."""
    if not text:
        return ""
    sanitized = sanitize_secret_string(text)
    sanitized = EMAIL_REGEX.sub("[REDACTED_EMAIL]", sanitized)
    sanitized = PHONE_REGEX.sub("[REDACTED_PHONE]", sanitized)
    sanitized = CONTEXTUAL_NAME_REGEX.sub(r"customer [REDACTED_NAME]", sanitized)
    return sanitized


def mask_record_pii(record: dict[str, Any], *, mask_names: bool = False) -> dict[str, Any]:
    """Return a copy of the record with sensitive PII masked."""
    masked = dict(record)
    for key, value in record.items():
        low_key = key.lower()
        # provide readable defaults for empty / null values
        if value is None or (isinstance(value, str) and value.strip().lower() in {"", "null", "none", "nan"}):
            if any(term in low_key for term in ("customer", "client", "buyer", "user", "name")):
                masked[key] = "Unknown"
            elif any(term in low_key for term in ("region", "category", "subcategory", "city", "state", "country")):
                masked[key] = "Unspecified"
            elif low_key == "status":
                masked[key] = "completed"
            continue

        str_val = str(value).strip()
        # redact phone, address, secrets, and emails
        if any(term in low_key for term in ("phone", "mobile", "contact_no", "cell")):
            masked[key] = "[REDACTED_PHONE]"
        elif any(term in low_key for term in ("address", "street", "postal", "zip", "zipcode")):
            masked[key] = "[REDACTED_ADDRESS]"
        elif any(term in low_key for term in ("password", "secret", "ssn", "aadhaar", "pan")):
            masked[key] = "[REDACTED]"
        elif "email" in low_key:
            masked[key] = mask_email(str_val)
        elif mask_names and any(term in low_key for term in ("customer_name", "client_name", "buyer_name", "full_name")):
            masked[key] = mask_name(str_val)

    return masked



