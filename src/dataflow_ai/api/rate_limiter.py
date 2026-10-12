"""In-memory sliding-window rate limiter for sensitive and authentication endpoints."""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request

_LOCK = threading.Lock()
_REQUEST_HISTORY: dict[str, list[float]] = defaultdict(list)

_TRUSTED_PROXY_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _is_trusted_proxy(host: str) -> bool:
    if host in ("testclient", "localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in net for net in _TRUSTED_PROXY_NETWORKS)
    except ValueError:
        return False


def get_client_ip(request: Request) -> str:
    """Get client IP, checking X-Forwarded-For only for trusted proxies."""
    client_host = request.client.host if request.client else "127.0.0.1"

    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and _is_trusted_proxy(client_host):
        # use first client IP in forwarded chain
        return forwarded.split(",")[0].strip()

    return client_host


def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    """Sliding-window check returning (allowed, retry_after_seconds)."""
    now = time.time()
    cutoff = now - window_seconds
    with _LOCK:
        history = _REQUEST_HISTORY[key]
        # prune entries outside the current window
        _REQUEST_HISTORY[key] = [t for t in history if t > cutoff]
        history = _REQUEST_HISTORY[key]

        if len(history) >= max_requests:
            oldest = history[0]
            retry_after = max(1, int(oldest + window_seconds - now))
            return False, retry_after

        history.append(now)
        return True, 0


def enforce_rate_limit(request: Request, action: str, max_requests: int, window_seconds: int) -> None:
    """Enforce rate limit for client IP and action. Raises HTTP 429 if exceeded."""
    client_ip = get_client_ip(request)
    key = f"{action}:{client_ip}"
    allowed, retry_after = check_rate_limit(key, max_requests, window_seconds)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests for {action}. Please retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit_dependency(action: str, max_requests: int, window_seconds: int) -> Callable[[Request], None]:
    """FastAPI Depends helper for declarative endpoint rate limiting."""
    def dependency(request: Request) -> None:
        enforce_rate_limit(request, action, max_requests, window_seconds)
    return dependency


def reset_rate_limiter() -> None:
    """Reset all history for testing."""
    with _LOCK:
        _REQUEST_HISTORY.clear()
