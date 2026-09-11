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
    ws_runtime_ok = _websocket_runtime_ok()
    try:
        rt = get_runtime()
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
            "last_error": rt.last_error,
        }
    except Exception as exc:  # noqa: BLE001 — never 500 the mount probe
        return {
            "ok": False,
            "service": "ai-trading-system",
            "status": "degraded",
            "ws_runtime_ok": ws_runtime_ok,
            "error": f"{type(exc).__name__}: {exc}"[:300],
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


@router.post("/api/clear-logs")
async def trading_clear_logs() -> dict[str, Any]:
    """Clear recent decision logs (paper portfolio unchanged)."""
    return await get_runtime().clear_decision_logs()


@router.post("/api/reset")
async def trading_reset() -> dict[str, Any]:
    """Reset paper trading: portfolio, logs, outcomes, cooldowns — no broker."""
    return await get_runtime().reset_paper_system()


@router.post("/api/chart-timeframe")
async def trading_set_timeframe(payload: dict[str, Any]) -> dict[str, Any]:
    tf = str((payload or {}).get("timeframe") or "")
    try:
        return get_runtime().set_chart_timeframe(tf)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/candles/{symbol}")
async def trading_candles(
    symbol: str,
    timeframe: str = "5m",
    limit: int = 500,
    before: float | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    rt = get_runtime()
    tf = timeframe.strip().lower()
    if tf not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
        return JSONResponse(
            {
                "ok": False,
                "error": "מסגרת זמן לא חוקית / invalid timeframe",
            },
            status_code=400,
        )
    if rt.use_simulated:
        hist = rt.events.chart_history(symbol.upper()).get(symbol.upper(), [])
        # Convert price points to pseudo-candles for chart.
        candles = []
        for p in hist[-limit:]:
            px = float(p.get("price") or 0)
            candles.append(
                {
                    "ts": float(p.get("ts") or 0),
                    "open": px,
                    "high": px,
                    "low": px,
                    "close": px,
                    "volume": float(p.get("volume") or 0),
                    "asset_type": "crypto" if "-" in symbol.upper() else "stock",
                    "provider": "simulated",
                    "complete": True,
                }
            )
        if before is not None:
            candles = [c for c in candles if c["ts"] < before][-limit:]
        # Fall back to persisted SQLite candles (same store used by markers).
        if not candles:
            from .market_data.candle_store import get_market_db
            from .market_snapshot import indicator_series_for_chart

            rows = get_market_db().get_candles(
                symbol.upper(), tf, limit=limit, before=before
            )
            candles = [
                {
                    "ts": float(c.ts),
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": float(c.volume),
                    "asset_type": getattr(c, "asset_type", "") or (
                        "crypto" if "-" in symbol.upper() else "stock"
                    ),
                    "provider": getattr(c, "provider", "") or "simulated+db",
                    "complete": bool(getattr(c, "complete", True)),
                }
                for c in rows
            ]
            from .market_data.models import Candle

            objs = [
                Candle(
                    ts=c["ts"],
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c["volume"],
                )
                for c in candles
            ]
            return {
                "symbol": symbol.upper(),
                "timeframe": tf,
                "candles": candles,
                "has_more": len(candles) >= limit,
                "source": "simulated",
                "provider": "simulated+db",
                "unavailable": len(candles) == 0,
                "error": (
                    None
                    if candles
                    else "אין נרות / No candles in simulated history or DB"
                ),
                "freshness": "live" if candles else "unavailable",
                "diagnostics": {
                    "provider": "simulated+db",
                    "candle_count": len(candles),
                    "first_ts": candles[0]["ts"] if candles else None,
                    "last_ts": candles[-1]["ts"] if candles else None,
                    "freshness": "live" if candles else "unavailable",
                    "error": None if candles else "empty",
                    "refreshed": False,
                    "from_cache": True,
                },
                "indicators": indicator_series_for_chart(objs) if objs else {},
            }
        return {
            "symbol": symbol.upper(),
            "timeframe": tf,
            "candles": candles,
            "has_more": False,
            "source": "simulated",
            "provider": "simulated",
            "unavailable": len(candles) == 0,
            "error": None if candles else "אין נרות / No simulated candles",
            "freshness": "live" if candles else "unavailable",
            "diagnostics": {
                "provider": "simulated",
                "candle_count": len(candles),
                "first_ts": candles[0]["ts"] if candles else None,
                "last_ts": candles[-1]["ts"] if candles else None,
                "freshness": "live" if candles else "unavailable",
                "error": None,
                "refreshed": False,
                "from_cache": False,
            },
            "indicators": {},
        }
    try:
        payload = rt.market.fetch_candles(
            symbol.upper(),
            tf,
            limit=limit,
            before=before,
            force_refresh=bool(force_refresh),
        )
    except ValueError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=400,
        )
    # Attach indicator series for chart overlays (shared calc). ATR included.
    from .market_data.models import Candle
    from .market_snapshot import indicator_series_for_chart

    raw = payload.get("candles") or []
    candle_objs = [
        Candle(
            ts=float(c["ts"]),
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c.get("volume") or 0),
            asset_type=str(c.get("asset_type") or ""),
            provider=str(c.get("provider") or ""),
            complete=bool(c.get("complete", True)),
        )
        for c in raw
    ]
    payload["indicators"] = indicator_series_for_chart(candle_objs) if candle_objs else {}
    payload["source"] = "real"
    # Ensure ATR is present in the series payload for frontend overlays.
    if payload["indicators"] and "atr_14" not in payload["indicators"]:
        payload["indicators"]["atr_14"] = []
    return payload


@router.get("/api/annotations/{symbol}")
async def list_annotations(symbol: str) -> dict[str, Any]:
    from .market_data.candle_store import get_market_db

    rows = get_market_db().list_annotations(symbol.upper())
    return {"symbol": symbol.upper(), "annotations": rows}


@router.post("/api/annotations")
async def create_annotation(payload: dict[str, Any]) -> dict[str, Any]:
    from .market_data.candle_store import get_market_db

    try:
        row = get_market_db().create_annotation(payload or {})
        return {"ok": True, "annotation": row}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.patch("/api/annotations/{ann_id}")
async def update_annotation(ann_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .market_data.candle_store import get_market_db

    try:
        row = get_market_db().update_annotation(ann_id, payload or {})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True, "annotation": row}


@router.delete("/api/annotations/{ann_id}")
async def delete_annotation(ann_id: str) -> dict[str, Any]:
    from .market_data.candle_store import get_market_db

    ok = get_market_db().delete_annotation(ann_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True}


@router.delete("/api/annotations/symbol/{symbol}")
async def clear_annotations(symbol: str) -> dict[str, Any]:
    from .market_data.candle_store import get_market_db

    n = get_market_db().clear_annotations(symbol.upper())
    return {"ok": True, "cleared": n}


@router.get("/api/market-snapshot/{symbol}")
async def market_snapshot(symbol: str) -> dict[str, Any]:
    rt = get_runtime()
    return rt.build_symbol_snapshot(symbol.upper())


@router.get("/api/chart-markers/{symbol}")
async def chart_markers(
    symbol: str,
    timeframe: str = "5m",
    from_ts: float | None = None,
    to_ts: float | None = None,
) -> dict[str, Any]:
    """Persisted paper fills + agent votes for chart overlay."""
    rt = get_runtime()
    tf = timeframe.strip().lower()
    if tf not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
        return JSONResponse({"ok": False, "error": "invalid timeframe"}, status_code=400)
    return rt.chart_markers(symbol, timeframe=tf, from_ts=from_ts, to_ts=to_ts)


@router.get("/api/unified-decisions/by-id/{decision_id}")
async def get_unified_decision(decision_id: str) -> dict[str, Any]:
    from .market_data.candle_store import get_market_db

    row = get_market_db().get_unified_decision(decision_id)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True, "unified": row}


@router.get("/api/unified-decisions/{symbol}")
async def list_unified_decisions(
    symbol: str,
    from_ts: float | None = None,
    to_ts: float | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    from .market_data.candle_store import get_market_db
    from .time_utils import normalize_symbol

    sym = normalize_symbol(symbol)
    tf = timeframe.strip().lower() if timeframe else None
    if tf and tf not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
        return JSONResponse({"ok": False, "error": "invalid timeframe"}, status_code=400)
    rows = get_market_db().list_unified_decisions(
        sym, from_ts=from_ts, to_ts=to_ts, timeframe=tf
    )
    return {
        "symbol": sym,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "timeframe": tf,
        "unified": rows,
        "count": len(rows),
    }


@router.get("/api/asset-config")
async def get_asset_config() -> dict[str, Any]:
    from . import asset_config as asset_config_mod
    from .config import DEFAULT_SYMBOLS
    from .market_data.candle_store import get_market_db

    rt = get_runtime()
    get_market_db().ensure_default_asset_configs(DEFAULT_SYMBOLS, rt.portfolio.positions)
    configs = get_market_db().list_trading_asset_configs()
    controls = asset_config_mod.load_portfolio_controls()
    return {
        "ok": True,
        "assets": configs,
        "portfolio_controls": controls.to_dict(),
        "modes": ["DISABLED", "MONITOR_ONLY", "TRADE", "CLOSE_ONLY"],
        "paper_trading_only": True,
    }


@router.put("/api/asset-config/{symbol}")
async def put_asset_config(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update per-asset mode/limits. Confirmations are frontend-only."""
    from .market_data.candle_store import get_market_db
    from .time_utils import normalize_symbol

    body = dict(payload or {})
    body["symbol"] = normalize_symbol(symbol)
    try:
        row = get_market_db().set_trading_asset_config(body)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "asset": row}


@router.put("/api/portfolio-controls")
async def put_portfolio_controls(payload: dict[str, Any]) -> dict[str, Any]:
    """Update portfolio-level controls. Confirmations are frontend-only."""
    from . import asset_config as asset_config_mod

    try:
        controls = asset_config_mod.update_portfolio_controls(payload or {})
    except (TypeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "portfolio_controls": controls.to_dict()}


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
        "chart_timeframes": ["1m", "5m", "15m", "1h", "4h", "1d"],
        "default_chart_timeframe": rt.chart_timeframe,
        "market_meta": rt.snapshot().get("market_meta"),
        "ai": rt.ai.status(),
        "paper_trading_only": True,
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
    dist: Path | None = next((p for p in candidates if (p / "index.html").is_file()), None)
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
