"""Reverse-proxy the isolated WhatsApp Monitor Node service.

Mounted under ``/whatsapp-monitor`` by the host Resume Agent. Does not import
any WhatsApp/session logic — the Node process owns that.

If the Node sidecar is down, this module attempts to start it automatically
(see ``whatsapp_monitor_launcher``) so EC2 --no-build injects and local
``python api_server.py`` still serve the UI.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

WHATSAPP_UPSTREAM = os.getenv("WHATSAPP_MONITOR_UPSTREAM", "http://127.0.0.1:3100").rstrip("/")
BASE_PATH = (os.getenv("WHATSAPP_MONITOR_BASE_PATH") or "/whatsapp-monitor").rstrip("/") or "/whatsapp-monitor"

router = APIRouter(tags=["whatsapp-monitor-proxy"])

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _filter_request_headers(headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _filter_response_headers(headers) -> dict[str, str]:
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk == "content-encoding":
            continue
        out[k] = v
    return out


def _ensure_upstream() -> dict:
    try:
        from whatsapp_monitor_launcher import ensure_whatsapp_monitor_running

        return ensure_whatsapp_monitor_running(wait_seconds=45.0)
    except Exception as exc:  # noqa: BLE001
        return {"action": "launcher_error", "last_error": f"{type(exc).__name__}: {exc}"}


def _unavailable_html(info: dict | None = None) -> str:
    info = info or {}
    detail = info.get("last_error") or info.get("action") or "unknown"
    backend = info.get("backend_dir") or "—"
    node = info.get("node_bin") or "—"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>WhatsApp Monitor</title>
<meta http-equiv="refresh" content="5"/>
<style>
 body{{font-family:system-ui,sans-serif;padding:2rem;max-width:40rem;line-height:1.45}}
 code{{background:#f1f5f9;padding:.15rem .35rem;border-radius:.35rem}}
 .muted{{color:#64748b;font-size:.9rem}}
</style></head><body>
<h1>WhatsApp Monitor</h1>
<p>Starting the WhatsApp Monitor service on the cloud server…</p>
<p class="muted">First boot may download Node.js and install dependencies. This page auto-refreshes.</p>
<ul class="muted">
 <li>status: <code>{detail}</code></li>
 <li>backend: <code>{backend}</code></li>
 <li>node: <code>{node}</code></li>
</ul>
<p><a href="/whatsapp-monitor/">Retry now</a> · <a href="/">Back to Resume Agent</a></p>
<script>setTimeout(function(){{location.reload()}},4000)</script>
</body></html>"""


async def _proxy(
    request: Request,
    full_path: str = "",
    *,
    _retried: bool = False,
    _body: bytes | None = None,
) -> Response:
    """Forward every /whatsapp-monitor/* request to the local Node service."""
    suffix = (full_path or "").lstrip("/")
    upstream_url = f"{WHATSAPP_UPSTREAM}{BASE_PATH}"
    if suffix:
        upstream_url = f"{upstream_url}/{suffix}"
    elif (request.url.path or "").endswith("/"):
        upstream_url = f"{upstream_url}/"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    body = _body if _body is not None else await request.body()
    headers = _filter_request_headers(request.headers)

    accept = (request.headers.get("accept") or "").lower()
    is_events = suffix.endswith("events") or "text/event-stream" in accept
    timeout = httpx.Timeout(None if is_events else 60.0, connect=5.0)

    try:
        if is_events and request.method == "GET":
            # Ensure sidecar before opening a long-lived stream.
            if not _retried:
                _ensure_upstream()
            client = httpx.AsyncClient(timeout=timeout)

            async def event_stream() -> AsyncIterator[bytes]:
                try:
                    async with client.stream(
                        "GET",
                        upstream_url,
                        headers={**headers, "accept": "text/event-stream"},
                    ) as upstream:
                        async for chunk in upstream.aiter_bytes():
                            yield chunk
                except Exception:
                    yield b'event: error\ndata: {"error":"upstream unavailable"}\n\n'
                finally:
                    await client.aclose()

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                request.method,
                upstream_url,
                headers=headers,
                content=body,
            )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        info = _ensure_upstream()
        if not _retried and info.get("action") in {
            "started",
            "starting",
            "already_running",
        }:
            return await _proxy(request, full_path, _retried=True, _body=body)

        wants_html = "text/html" in accept or suffix in ("",) or suffix.endswith("index.html")
        if wants_html and request.method == "GET":
            return HTMLResponse(content=_unavailable_html(info), status_code=503)
        return JSONResponse(
            {
                "ok": False,
                "service": "whatsapp-monitor",
                "status": "unavailable",
                **{
                    k: info.get(k)
                    for k in ("action", "last_error", "backend_dir", "node_bin", "upstream_up")
                },
            },
            status_code=503,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"ok": False, "error": type(exc).__name__},
            status_code=502,
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


@router.get("/api/sidecar-status")
async def whatsapp_sidecar_status() -> dict:
    """Host-side status (does not require the Node process)."""
    try:
        from whatsapp_monitor_launcher import status as launcher_status

        info = launcher_status()
    except Exception as exc:  # noqa: BLE001
        info = {"error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "service": "whatsapp-monitor-proxy", **info}


@router.post("/api/sidecar-start")
async def whatsapp_sidecar_start() -> dict:
    info = _ensure_upstream()
    return {"ok": bool(info.get("upstream_up")), **info}


@router.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_whatsapp_monitor_root(request: Request) -> Response:
    return await _proxy(request, "")


@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_whatsapp_monitor_path(full_path: str, request: Request) -> Response:
    # Host-managed endpoints (must not be forwarded blindly before matching).
    # FastAPI matches more specific routes first; these are registered above.
    return await _proxy(request, full_path)


def register_whatsapp_monitor_proxy(app) -> None:
    """Attach the proxy router and best-effort start the Node sidecar."""
    app.include_router(router, prefix=BASE_PATH)
    print(f"[info] WhatsApp Monitor proxy mounted at {BASE_PATH} → {WHATSAPP_UPSTREAM}")
    try:
        info = _ensure_upstream()
        print(f"[info] WhatsApp Monitor sidecar ensure: {info.get('action')} up={info.get('upstream_up')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] WhatsApp Monitor sidecar ensure skipped: {exc}")
