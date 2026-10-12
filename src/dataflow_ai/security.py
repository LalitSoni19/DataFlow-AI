"""Authentication and cryptographic helpers (passwords, JWTs, webhook verification)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid


def hash_password(password: str, *, salt: bytes | None = None, n: int = 16384, r: int = 8, p: int = 1) -> str:
    """Hash password with scrypt and a random 16-byte salt."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    salt_bytes = salt or os.urandom(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt_bytes, n=n, r=r, p=p, maxmem=32 * 1024 * 1024)
    salt_b64 = base64.b64encode(salt_bytes).decode("ascii")
    derived_b64 = base64.b64encode(derived).decode("ascii")
    return f"scrypt${n}${r}${p}${salt_b64}${derived_b64}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored scrypt hash using constant-time comparison."""
    if not isinstance(password, str) or not isinstance(hashed, str):
        return False
    parts = hashed.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt_bytes = base64.b64decode(parts[4])
        expected_derived = base64.b64decode(parts[5])
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt_bytes, n=n, r=r, p=p, maxmem=32 * 1024 * 1024)
        return hmac.compare_digest(derived, expected_derived)
    except (ValueError, TypeError, KeyError):
        return False


# In-memory JWT blacklist for revoked tokens
class TokenBlacklist:

    def __init__(self) -> None:
        self._revoked: dict[str, float] = {}  # jti -> exp_timestamp
        self._lock = threading.Lock()

    def revoke(self, jti: str, exp: float) -> None:
        with self._lock:
            self._revoked[jti] = exp

    def is_revoked(self, jti: str) -> bool:
        with self._lock:
            now = time.time()
            # Clean expired tokens
            self._revoked = {j: e for j, e in self._revoked.items() if e > now}
            return jti in self._revoked

    def clear(self) -> None:
        with self._lock:
            self._revoked.clear()


TOKEN_BLACKLIST = TokenBlacklist()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_jwt(
    user_id: str,
    role: str,
    secret: str,
    *,
    expires_in_seconds: int = 1800,
) -> str:
    """Generate a signed HMAC-SHA256 JWT containing sub, role, jti, iat, and exp claims."""
    if not secret or len(secret) < 16:
        raise ValueError("JWT signing secret must be at least 16 characters.")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "role": role,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    encoded_header = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    encoded_signature = _b64url_encode(signature)
    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def verify_jwt(token: str, secret: str) -> dict:
    """Verify an HS256 JWT signature, expiry, and revocation status."""
    if not isinstance(token, str) or not token:
        raise ValueError("Token must be a non-empty string.")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT structure; expected 3 segments.")

    encoded_header, encoded_payload, encoded_signature = parts
    try:
        header = json.loads(_b64url_decode(encoded_header).decode("utf-8"))
    except Exception as error:
        raise ValueError("Invalid JWT header encoding.") from error

    if header.get("alg") != "HS256":
        raise ValueError(f"Unsupported JWT algorithm: {header.get('alg')!r}. Only HS256 is permitted.")

    # verify HMAC signature
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(encoded_signature)
    except Exception as error:
        raise ValueError("Invalid JWT signature encoding.") from error

    if not hmac.compare_digest(actual_sig, expected_sig):
        raise ValueError("JWT signature mismatch.")

    try:
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except Exception as error:
        raise ValueError("Invalid JWT payload encoding.") from error

    # check expiration
    now = time.time()
    exp = payload.get("exp")
    if exp is None or not isinstance(exp, (int, float)):
        raise ValueError("JWT missing 'exp' claim.")
    if now > exp:
        raise ValueError("JWT has expired.")

    # check revocation list
    jti = payload.get("jti")
    if jti and TOKEN_BLACKLIST.is_revoked(jti):
        raise ValueError("JWT has been revoked (logged out).")

    return payload


# In-memory store for single-use password reset tokens (max 15 min TTL)
class PasswordResetStore:

    def __init__(self, ttl_minutes: int = 15) -> None:
        self.ttl_minutes = min(ttl_minutes, 15)  # Enforce 15-minute max
        self._tokens: dict[str, dict] = {}
        self._lock = threading.Lock()

    def generate_reset_token(self, email: str) -> str:
        """Generate a single-use token tied to an email."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = time.time() + (self.ttl_minutes * 60)
        with self._lock:
            self._tokens[token_hash] = {
                "email": email.lower(),
                "expires_at": expires_at,
                "used": False,
            }
        return raw_token

    def verify_and_consume_token(self, raw_token: str) -> str:
        """Verify token, enforce single-use, check expiry, and mark consumed."""
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            record = self._tokens.get(token_hash)
            if not record:
                raise ValueError("Invalid or unknown password reset token.")
            if record["used"]:
                raise ValueError("Password reset token has already been used.")
            if now > record["expires_at"]:
                del self._tokens[token_hash]
                raise ValueError("Password reset token has expired (15 minute limit exceeded).")
            # mark token as used immediately
            record["used"] = True
            return record["email"]

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()


RESET_TOKEN_STORE = PasswordResetStore(ttl_minutes=15)


# Webhook signature verifiers
def verify_stripe_webhook_signature(
    payload: bytes,
    sig_header: str | None,
    secret: str | None,
    *,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify Stripe-Signature header with HMAC-SHA256 and replay tolerance."""
    if not sig_header or not secret or not payload:
        return False

    elements: dict[str, list[str]] = {}
    for item in sig_header.split(","):
        if "=" in item:
            k, v = item.strip().split("=", 1)
            elements.setdefault(k, []).append(v)

    timestamps = elements.get("t", [])
    signatures = elements.get("v1", [])
    if not timestamps or not signatures:
        return False

    try:
        timestamp_int = int(timestamps[0])
    except ValueError:
        return False

    # timestamp must fall within tolerance window to prevent replay attacks
    if abs(time.time() - timestamp_int) > tolerance_seconds:
        return False

    signed_payload = f"{timestamp_int}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()

    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def verify_razorpay_webhook_signature(
    payload: bytes,
    signature: str | None,
    secret: str | None,
) -> bool:
    """Verify Razorpay webhook signature using HMAC-SHA256."""
    if not signature or not secret or not payload:
        return False
    computed = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)

