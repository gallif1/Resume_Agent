"""Shared HTTP helpers for live scanner collectors."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from live_scanner.constants import HTTP_RETRIES, HTTP_TIMEOUT_SECONDS

logger = logging.getLogger("live_scanner.http")

_USER_AGENT = "ResumeAgent-LiveScanner/1.0 (+https://github.com/gallif1/Resume_Agent)"


def http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    retries: int = HTTP_RETRIES,
) -> tuple[Any, int]:
    """GET/POST JSON. Returns (parsed_json, http_status). Raises on hard failure."""
    payload = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
    if payload is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)

    last_error: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        req = urllib.request.Request(url, data=payload, headers=hdrs, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                status = getattr(resp, "status", 200) or 200
                if not raw:
                    return None, status
                return json.loads(raw.decode("utf-8")), status
        except urllib.error.HTTPError as exc:
            last_error = exc
            body_preview = ""
            try:
                body_preview = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"HTTP {exc.code} for {url}: {body_preview or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc
    raise RuntimeError(f"Request failed for {url}: {last_error}")


def host_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def path_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).path or ""
    except Exception:  # noqa: BLE001
        return ""
