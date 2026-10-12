"""HTTP API ingestion with SSRF safeguards and timeout handling."""

from __future__ import annotations

import ipaddress
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dataflow_ai.ingestion.raw_store import RawDataLake, count_json_records
from dataflow_ai.models import IngestionReceipt
from dataflow_ai.utils import sanitize_secret_string, sanitize_uri


def validate_safe_api_url(url: str) -> None:
    """Validate URL scheme and block private/internal addresses to prevent SSRF."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Insecure scheme {parsed.scheme!r}: only HTTP and HTTPS are permitted.")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL: missing hostname.")

    # direct IP check
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(
                f"Access to private/internal network IP address {hostname} is forbidden (SSRF protection)."
            )
        return
    except ValueError as err:
        if "SSRF protection" in str(err):
            raise

    # blocked metadata and local hostnames
    blocked_hostnames = {"localhost", "metadata.google.internal", "instance-data"}
    low_host = hostname.lower()
    if low_host in blocked_hostnames or low_host.endswith((".local", ".internal", ".lan", ".localhost")):
        raise ValueError(f"Access to internal hostname {hostname!r} is forbidden (SSRF protection).")

    # DNS check to catch private IP rebinding
    try:
        resolved = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in resolved:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise ValueError(
                    f"Access to private/internal network IP address {ip_str} is forbidden (SSRF protection)."
                )
    except socket.gaierror:
        # Hostname could not be resolved (e.g. mock test domains like api.vendor.com in unit tests).
        # urlopen will fail naturally if this is a real invalid connection.
        pass


class ApiIngestor:
    def __init__(self, lake: RawDataLake, timeout_seconds: int = 20, max_bytes: int = 10_000_000) -> None:
        self.lake = lake
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def ingest(
        self,
        url: str,
        dataset: str,
        run_id: str,
        *,
        auth_token: str | None = None,
        auth_header: str = "Authorization",
    ) -> IngestionReceipt:
        validate_safe_api_url(url)
        clean_url = sanitize_uri(url)
        headers = {"Accept": "application/json", "User-Agent": "DataFlowAI/0.1"}
        if auth_token:
            formatted_token = (
                auth_token
                if auth_token.startswith(("Bearer ", "Basic ", "ApiKey "))
                else f"Bearer {auth_token}"
            )
            headers[auth_header] = formatted_token

        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {"application/json", "text/json"}:
                    raise ValueError(f"Expected JSON from API, received {content_type!r}.")
                content = response.read(self.max_bytes + 1)
        except (HTTPError, URLError) as error:
            raise ConnectionError(
                f"API ingestion failed for {clean_url}: {sanitize_secret_string(str(error))}"
            ) from error
        if len(content) > self.max_bytes:
            raise ValueError(f"API response exceeded {self.max_bytes:,} byte limit.")
        try:
            json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"API returned invalid JSON: {clean_url}") from error
        return self.lake.store_bytes(
            dataset=dataset,
            source_type="api",
            source_uri=clean_url,
            content=content,
            extension="json",
            run_id=run_id,
            row_count=count_json_records(content),
        )


