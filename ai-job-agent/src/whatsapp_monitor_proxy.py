"""Serve the WhatsApp Monitor UI and point clients at a local agent.

WhatsApp Web auth (QR / session / Chromium) runs on the **user's PC**, not on
the Resume Agent server. The cloud/EC2 host only serves the static dashboard.

The browser talks directly to ``http://127.0.0.1:3100`` (local Node agent).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_PATH = (os.getenv("WHATSAPP_MONITOR_BASE_PATH") or "/whatsapp-monitor").rstrip("/") or "/whatsapp-monitor"
LOCAL_AGENT_URL = os.getenv("WHATSAPP_LOCAL_AGENT_URL", "http://127.0.0.1:3100").rstrip("/")

router = APIRouter(tags=["whatsapp-monitor"])


def _frontend_dist_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        Path("/app/whatsapp_monitor/frontend/dist"),
        here.parents[2] / "whatsapp_monitor" / "frontend" / "dist",
    ]


def find_frontend_dist() -> Path | None:
    for d in _frontend_dist_candidates():
        if (d / "index.html").is_file():
            return d
    return None


def _setup_html() -> str:
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>WhatsApp Monitor — הפעלה מקומית</title>
  <style>
    body{{font-family:Heebo,system-ui,sans-serif;margin:0;background:#f8fafc;color:#1e293b}}
    main{{max-width:40rem;margin:2rem auto;padding:1.5rem;background:#fff;border-radius:1rem;
      box-shadow:0 10px 40px rgba(15,23,42,.08);border:1px solid rgba(15,23,42,.08)}}
    h1{{margin-top:0}}
    code,pre{{background:#f1f5f9;border-radius:.5rem}}
    pre{{padding:1rem;overflow:auto;direction:ltr;text-align:left}}
    .muted{{color:#64748b}}
    a{{color:#2563eb}}
  </style>
</head>
<body>
<main>
  <h1>WhatsApp Monitor</h1>
  <p>חיבור הוואטסאפ רץ <b>על המחשב שלך</b> (לא על השרת).</p>
  <p class="muted">בנה את ממשק המודול ואז רענן, או הפעל את הסוכן המקומי:</p>
  <pre>cd whatsapp_monitor/frontend && npm install && npm run build
cd ../backend && npm install && npm start</pre>
  <p>הסוכן המקומי: <code dir="ltr">{LOCAL_AGENT_URL}</code></p>
  <p><a href="/">חזרה ל-Resume Agent</a></p>
</main>
</body>
</html>"""


@router.get("/api/client-mode")
async def whatsapp_client_mode() -> dict:
    """Tell the UI that WhatsApp must run as a local agent on the user PC."""
    return {
        "ok": True,
        "mode": "client_local_agent",
        "local_agent_url": LOCAL_AGENT_URL,
        "local_api_base": f"{LOCAL_AGENT_URL}{BASE_PATH}/api",
        "message": "WhatsApp QR/session runs on the user's PC via the local Node agent.",
    }


@router.get("/")
async def whatsapp_monitor_index():
    dist = find_frontend_dist()
    if dist is None:
        return HTMLResponse(_setup_html(), status_code=503)
    return FileResponse(dist / "index.html")


@router.get("/{full_path:path}")
async def whatsapp_monitor_spa(full_path: str, request: Request):
    # API under this mount is only client-mode metadata — WA APIs live on localhost.
    if full_path.startswith("api/"):
        return JSONResponse(
            {
                "ok": False,
                "error": "whatsapp_apis_are_on_local_agent",
                "local_api_base": f"{LOCAL_AGENT_URL}{BASE_PATH}/api",
                "hint": "Start: cd whatsapp_monitor/backend && npm start",
            },
            status_code=503,
        )
    dist = find_frontend_dist()
    if dist is None:
        return HTMLResponse(_setup_html(), status_code=503)
    candidate = dist / full_path
    if candidate.is_file() and dist in candidate.resolve().parents:
        return FileResponse(candidate)
    # SPA fallback
    return FileResponse(dist / "index.html")


def register_whatsapp_monitor_proxy(app) -> None:
    """Mount static WhatsApp Monitor UI. WhatsApp auth stays on the user PC."""
    dist = find_frontend_dist()
    if dist is not None:
        assets = dist / "assets"
        if assets.is_dir():
            app.mount(
                f"{BASE_PATH}/assets",
                StaticFiles(directory=str(assets)),
                name="whatsapp-monitor-assets",
            )
    app.include_router(router, prefix=BASE_PATH)
    print(
        f"[info] WhatsApp Monitor UI at {BASE_PATH} "
        f"(client-local-agent → {LOCAL_AGENT_URL}; "
        f"frontend={'yes' if dist else 'missing — build whatsapp_monitor/frontend'})"
    )
