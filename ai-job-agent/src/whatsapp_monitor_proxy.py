"""Reverse-proxy the isolated WhatsApp Monitor Node service.

Mounted under ``/whatsapp-monitor`` by the host Resume Agent. Does not import
any WhatsApp/session logic — the Node process owns that.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

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


async def _proxy(request: Request, full_path: str = "") -> Response:
    """Forward every /whatsapp-monitor/* request to the local Node service."""
    suffix = (full_path or "").lstrip("/")
    upstream_url = f"{WHATSAPP_UPSTREAM}{BASE_PATH}"
    if suffix:
        upstream_url = f"{upstream_url}/{suffix}"
    elif (request.url.path or "").endswith("/"):
        # Preserve trailing slash so Express static does not 301-loop via the proxy.
        upstream_url = f"{upstream_url}/"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    body = await request.body()
    headers = _filter_request_headers(request.headers)

    # SSE / streaming endpoints
    accept = (request.headers.get("accept") or "").lower()
    is_events = suffix.endswith("events") or "text/event-stream" in accept

    timeout = httpx.Timeout(None if is_events else 60.0, connect=5.0)

    try:
        if is_events and request.method == "GET":
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
        # Degraded: module not running — keep host app healthy.
        if suffix in ("",) or suffix.endswith("index.html"):
            html = (
                "<!doctype html><html><body style='font-family:sans-serif;padding:2rem'>"
                "<h1>WhatsApp Monitor</h1>"
                "<p>The WhatsApp Monitor service is not running.</p>"
                "<p>Start it with: <code>cd whatsapp_monitor/backend && npm start</code></p>"
                "<p><a href='/'>Back to Resume Agent</a></p>"
                "</body></html>"
            )
            return Response(content=html, media_type="text/html", status_code=503)
        return Response(
            content='{"ok":false,"service":"whatsapp-monitor","status":"unavailable"}',
            media_type="application/json",
            status_code=503,
        )
    except Exception as exc:  # noqa: BLE001
        return Response(
            content=f'{{"ok":false,"error":"{type(exc).__name__}"}}',
            media_type="application/json",
            status_code=502,
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


@router.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_whatsapp_monitor_root(request: Request) -> Response:
    return await _proxy(request, "")


@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_whatsapp_monitor_path(full_path: str, request: Request) -> Response:
    return await _proxy(request, full_path)


def register_whatsapp_monitor_proxy(app) -> None:
    """Attach the proxy router. Safe to call even if the Node service is down."""
    app.include_router(router, prefix=BASE_PATH)
    print(f"[info] WhatsApp Monitor proxy mounted at {BASE_PATH} → {WHATSAPP_UPSTREAM}")
