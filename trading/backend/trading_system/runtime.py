"""Orchestrates feed → events → agents → decisions with START/PAUSE."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .agents import BaseAgent, default_agents
from .config import DATA_DIR, DEFAULT_SYMBOLS, STARTING_CASH, TICK_INTERVAL_SEC
from .decision_engine import DecisionEngine
from .event_engine import EventEngine
from .market_feed import MarketFeed
from .models import Portfolio, SystemState


BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class TradingRuntime:
    """Singleton-style runtime safe to share across FastAPI requests."""

    def __init__(self) -> None:
        self.state = SystemState.STOPPED
        self.feed = MarketFeed(DEFAULT_SYMBOLS)
        self.events = EventEngine()
        self.decision_engine = DecisionEngine()
        self.agents: list[BaseAgent] = default_agents()
        self.portfolio = Portfolio(cash=STARTING_CASH)
        self.tick_count = 0
        self.started_at: float | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._subscribers: set[BroadcastFn] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._persist_path = DATA_DIR / "runtime_state.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()

    # -- persistence -----------------------------------------------------

    def _load_state(self) -> None:
        if not self._persist_path.is_file():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self.portfolio.cash = float(raw.get("cash", STARTING_CASH))
            self.portfolio.realized_pnl = float(raw.get("realized_pnl", 0.0))
            positions = raw.get("positions") or {}
            from .models import Position

            self.portfolio.positions = {
                sym: Position(sym, float(p["quantity"]), float(p["avg_price"]))
                for sym, p in positions.items()
            }
        except Exception:  # noqa: BLE001
            pass

    def _save_state(self) -> None:
        try:
            payload = {
                "cash": self.portfolio.cash,
                "realized_pnl": self.portfolio.realized_pnl,
                "positions": {
                    sym: {"quantity": p.quantity, "avg_price": p.avg_price}
                    for sym, p in self.portfolio.positions.items()
                },
                "saved_at": time.time(),
            }
            self._persist_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # -- pub/sub ---------------------------------------------------------

    def subscribe(self, fn: BroadcastFn) -> None:
        self._subscribers.add(fn)

    def unsubscribe(self, fn: BroadcastFn) -> None:
        self._subscribers.discard(fn)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        dead: list[BroadcastFn] = []
        for fn in list(self._subscribers):
            try:
                await fn(message)
            except Exception:  # noqa: BLE001
                dead.append(fn)
        for fn in dead:
            self._subscribers.discard(fn)

    # -- control ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        decisions = self.decision_engine.recent
        return {
            "state": self.state.value,
            "tick_count": self.tick_count,
            "started_at": self.started_at,
            "last_error": self.last_error,
            "symbols": list(self.feed.symbols),
            "market": self.feed.snapshot(),
            "price_history": self.events.chart_history(),
            "events": self.events.recent,
            "decisions": decisions,
            "trades": [
                {
                    "id": d["id"],
                    "symbol": d["symbol"],
                    "side": d["side"],
                    "price": d.get("fill_price"),
                    "quantity": d.get("quantity"),
                    "ts": d["ts"],
                    "confidence": d.get("confidence"),
                    "agents": [
                        {
                            "id": v.get("agent_id"),
                            "name": v.get("agent_name"),
                            "side": v.get("side"),
                            "confidence": v.get("confidence"),
                        }
                        for v in (d.get("votes") or [])
                        if v.get("side") in ("BUY", "SELL")
                    ],
                }
                for d in decisions
                if d.get("executed") and d.get("side") in ("BUY", "SELL")
            ],
            "agents": [
                {"id": a.agent_id, "name": a.agent_name} for a in self.agents
            ],
            "portfolio": self.portfolio.to_dict(),
            "tick_interval_sec": TICK_INTERVAL_SEC,
        }

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self.state == SystemState.RUNNING:
                return self.snapshot()
            if self.state == SystemState.PAUSED:
                self.state = SystemState.RUNNING
                await self._broadcast({"type": "state", "payload": self.snapshot()})
                return self.snapshot()
            self.state = SystemState.RUNNING
            self.started_at = time.time()
            self.last_error = None
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._run_loop(), name="trading-runtime")
            await self._broadcast({"type": "state", "payload": self.snapshot()})
            return self.snapshot()

    async def pause(self) -> dict[str, Any]:
        async with self._lock:
            if self.state == SystemState.RUNNING:
                self.state = SystemState.PAUSED
                self._save_state()
                await self._broadcast({"type": "state", "payload": self.snapshot()})
            return self.snapshot()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            self.state = SystemState.STOPPED
            task = self._task
            self._task = None
            self._save_state()
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._broadcast({"type": "state", "payload": self.snapshot()})
        return self.snapshot()

    async def _run_loop(self) -> None:
        try:
            while True:
                if self.state == SystemState.STOPPED:
                    break
                if self.state == SystemState.PAUSED:
                    await asyncio.sleep(0.2)
                    continue
                await self._tick_once()
                await asyncio.sleep(TICK_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.state = SystemState.STOPPED
            await self._broadcast({"type": "error", "payload": {"message": str(exc)}})

    async def _tick_once(self) -> None:
        ticks = self.feed.next_ticks()
        self.tick_count += 1
        new_events = self.events.process(ticks)
        decisions_out = []
        votes_out = []
        for tick in ticks:
            history = self.events.history_prices(tick.symbol)
            votes = [agent.vote(tick, history, new_events) for agent in self.agents]
            votes_out.extend(v.to_dict() for v in votes)
            decision = self.decision_engine.decide(tick.symbol, votes, tick.price)
            if decision is None:
                continue
            if decision.executed:
                self.portfolio = self.decision_engine.apply_fill(self.portfolio, decision)
            decisions_out.append(decision.to_dict())

        if self.tick_count % 5 == 0:
            self._save_state()

        await self._broadcast(
            {
                "type": "tick",
                "payload": {
                    "tick_count": self.tick_count,
                    "state": self.state.value,
                    "market": [t.to_dict() for t in ticks],
                    "price_points": [
                        {
                            "symbol": t.symbol,
                            "ts": t.ts,
                            "price": t.price,
                            "volume": t.volume,
                            "change_pct": t.change_pct,
                        }
                        for t in ticks
                    ],
                    "events": [e.to_dict() for e in new_events],
                    "votes": votes_out,
                    "decisions": decisions_out,
                    "trades": [
                        {
                            "id": d["id"],
                            "symbol": d["symbol"],
                            "side": d["side"],
                            "price": d.get("fill_price"),
                            "quantity": d.get("quantity"),
                            "ts": d["ts"],
                            "confidence": d.get("confidence"),
                            "agents": [
                                {
                                    "id": v.get("agent_id"),
                                    "name": v.get("agent_name"),
                                    "side": v.get("side"),
                                    "confidence": v.get("confidence"),
                                }
                                for v in (d.get("votes") or [])
                                if v.get("side") in ("BUY", "SELL")
                            ],
                        }
                        for d in decisions_out
                        if d.get("executed") and d.get("side") in ("BUY", "SELL")
                    ],
                    "portfolio": self.portfolio.to_dict(),
                },
            }
        )


_runtime: TradingRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> TradingRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = TradingRuntime()
        return _runtime
