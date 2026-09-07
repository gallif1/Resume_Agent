"""FastAPI router for the AI Trading System.

Mounted by the host Resume Agent under `/trading`, or served alone via
`standalone.py`. Does not import any Resume Agent modules.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import PUBLIC_BASE_PATH, RESUME_AGENT_HOME_URL
from .runtime import get_runtime

router = APIRouter(tags=["trading"])


def _websocket_runtime_ok() -> bool:
    """True when uvicorn can upgrade WebSocket connections (needs websockets or wsproto)."""
    import importlib.util

    return any(importlib.util.find_spec(name) for name in ("websockets", "wsproto"))


@router.get("/api/health")
async def trading_health() -> dict[str, Any]:
    rt = get_runtime()
    ws_runtime_ok = _websocket_runtime_ok()
    snap = rt.snapshot()
    return {
        "ok": True,
        "service": "ai-trading-system",
        "state": rt.state.value,
        "tick_count": rt.tick_count,
        "base_path": PUBLIC_BASE_PATH or "/",
        "resume_agent_home": RESUME_AGENT_HOME_URL,
        "ws_path": f"{PUBLIC_BASE_PATH}/ws" if PUBLIC_BASE_PATH else "/ws",
        "ws_paths": [
            f"{PUBLIC_BASE_PATH}/ws" if PUBLIC_BASE_PATH else "/ws",
            f"{PUBLIC_BASE_PATH}/api/ws" if PUBLIC_BASE_PATH else "/api/ws",
        ],
        "ws_runtime_ok": ws_runtime_ok,
        "data_mode": snap.get("data_mode"),
        "market_meta": snap.get("market_meta"),
        "ai": snap.get("ai"),
    }


@router.get("/api/snapshot")
async def trading_snapshot() -> dict[str, Any]:
    return get_runtime().snapshot()


@router.post("/api/start")
async def trading_start() -> dict[str, Any]:
    return await get_runtime().start()


@router.post("/api/pause")
async def trading_pause() -> dict[str, Any]:
    return await get_runtime().pause()


@router.post("/api/stop")
async def trading_stop() -> dict[str, Any]:
    return await get_runtime().stop()


@router.post("/api/chart-timeframe")
async def trading_set_timeframe(payload: dict[str, Any]) -> dict[str, Any]:
    tf = str((payload or {}).get("timeframe") or "")
    try:
        return get_runtime().set_chart_timeframe(tf)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/candles/{symbol}")
async def trading_candles(symbol: str, timeframe: str = "5m", limit: int = 120) -> dict[str, Any]:
    rt = get_runtime()
    tf = timeframe.strip().lower()
    if tf not in {"1m", "5m", "15m", "1h"}:
        return JSONResponse({"ok": False, "error": "invalid timeframe"}, status_code=400)
    if rt.use_simulated:
        hist = rt.events.chart_history(symbol.upper()).get(symbol.upper(), [])
        return {"symbol": symbol.upper(), "timeframe": tf, "candles": hist, "source": "simulated"}
    candles = rt.market.get_candles(symbol.upper(), tf)[-limit:]
    return {
        "symbol": symbol.upper(),
        "timeframe": tf,
        "candles": [c.to_dict() for c in candles],
        "source": "real",
    }


@router.get("/api/config")
async def trading_config() -> dict[str, Any]:
    ws_paths = [
        f"{PUBLIC_BASE_PATH}/ws" if PUBLIC_BASE_PATH else "/ws",
        f"{PUBLIC_BASE_PATH}/api/ws" if PUBLIC_BASE_PATH else "/api/ws",
    ]
    rt = get_runtime()
    return {
        "base_path": PUBLIC_BASE_PATH or "/",
        "resume_agent_home": RESUME_AGENT_HOME_URL,
        "ws_path": ws_paths[0],
        "ws_paths": ws_paths,
        "api_base": f"{PUBLIC_BASE_PATH}/api" if PUBLIC_BASE_PATH else "/api",
        "data_mode": "simulated" if rt.use_simulated else "real",
        "chart_timeframes": ["1m", "5m", "15m", "1h"],
        "default_chart_timeframe": rt.chart_timeframe,
        "market_meta": rt.snapshot().get("market_meta"),
        "ai": rt.ai.status(),
    }


async def trading_ws_endpoint(websocket: WebSocket) -> None:
    """Shared WebSocket handler (router + app-level registration)."""
    await websocket.accept()
    runtime = get_runtime()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)

    async def push(message: dict[str, Any]) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    runtime.subscribe(push)
    stop = asyncio.Event()

    async def sender() -> None:
        last_ping = asyncio.get_running_loop().time()
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                now = asyncio.get_running_loop().time()
                if now - last_ping >= 20:
                    await websocket.send_json({"type": "ping", "payload": {"ok": True}})
                    last_ping = now
            except Exception:  # noqa: BLE001
                stop.set()
                break

    async def receiver() -> None:
        while not stop.is_set():
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                stop.set()
                break
            except Exception:  # noqa: BLE001
                stop.set()
                break
            cmd = raw.strip().lower()
            if cmd == "start":
                await runtime.start()
            elif cmd == "pause":
                await runtime.pause()
            elif cmd == "stop":
                await runtime.stop()

    try:
        await websocket.send_json({"type": "hello", "payload": runtime.snapshot()})
        sender_task = asyncio.create_task(sender())
        receiver_task = asyncio.create_task(receiver())
        await stop.wait()
        sender_task.cancel()
        receiver_task.cancel()
        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)
    except WebSocketDisconnect:
        stop.set()
    finally:
        stop.set()
        runtime.unsubscribe(push)


@router.websocket("/ws")
async def trading_ws(websocket: WebSocket) -> None:
    await trading_ws_endpoint(websocket)


@router.websocket("/api/ws")
async def trading_ws_api_alias(websocket: WebSocket) -> None:
    """Alias under /api for proxies that only forward /trading/api/*."""
    await trading_ws_endpoint(websocket)


def create_trading_router() -> APIRouter:
    """Return the API/WS router (host mounts it at TRADING_BASE_PATH)."""
    return router


def register_trading_websockets(app: Any, base_path: str | None = None) -> list[str]:
    """Explicitly register WebSocket routes on the host app.

    Some production ASGI / FastAPI combinations fail to expose WebSocket routes
    that only live on an included APIRouter. Registering on the app itself is
    the reliable path. Returns the registered paths.
    """
    base = (base_path if base_path is not None else PUBLIC_BASE_PATH) or ""
    paths = [f"{base}/ws", f"{base}/api/ws"]
    for path in paths:
        app.add_api_websocket_route(path, trading_ws_endpoint)
    return paths


def mount_trading_frontend(app: Any, base_path: str | None = None) -> bool:
    """Serve the built trading SPA under base_path. Returns True if mounted."""
    base = (base_path if base_path is not None else PUBLIC_BASE_PATH) or ""
    # Prefer repo layout, then Docker layout.
    candidates = [
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
        Path("/app/trading/frontend/dist"),
    ]
    dist: Path | None = next((p for p in candidates if p.is_dir()), None)
    if dist is None:
        return False

    assets = dist / "assets"
    mount_root = base or ""
    if assets.is_dir():
        app.mount(
            f"{mount_root}/assets",
            StaticFiles(directory=str(assets)),
            name="trading-assets",
        )

    index = dist / "index.html"

    async def trading_index():
        return FileResponse(index)

    # Explicit index routes (avoid relying only on SPA middleware).
    app.add_api_route(f"{mount_root}", trading_index, methods=["GET"], include_in_schema=False)
    app.add_api_route(f"{mount_root}/", trading_index, methods=["GET"], include_in_schema=False)

    @app.middleware("http")
    async def trading_spa_fallback(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if response.status_code != 404 or request.method != "GET":
            return response
        path = request.url.path or "/"
        prefix = mount_root or ""
        if prefix and not (path == prefix or path.startswith(prefix + "/")):
            return response
        if path.startswith(f"{prefix}/api") or path.startswith(f"{prefix}/ws"):
            return response
        if path.startswith(f"{prefix}/assets"):
            return response
        accept = request.headers.get("accept", "")
        if "text/html" not in accept and "*/*" not in accept:
            return response
        if index.is_file():
            return FileResponse(index)
        return response

    return True
