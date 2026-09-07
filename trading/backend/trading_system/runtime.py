"""Orchestrates real market cache → events → agents → decisions with START/PAUSE."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Awaitable, Callable

from .agents import default_agents, BaseAgent
from .ai import AIMarketAnalyst
from .config import (
    DATA_DIR,
    DEFAULT_CHART_TIMEFRAME,
    DEFAULT_SYMBOLS,
    FILL_COOLDOWN_SEC,
    STARTING_CASH,
    TICK_INTERVAL_SEC,
    USE_SIMULATED_FEED,
)
from .decision_engine import DecisionEngine
from .decision_log import (
    DecisionLogBuffer,
    build_agent_entries,
    build_decision_log,
    build_execution,
    build_market_snapshot,
    classify_kind,
)
from .event_engine import EventEngine
from .indicators import IndicatorEngine
from .market_data import MarketDataService
from .market_data.models import DataFreshness
from .market_feed import MarketFeed
from .models import AgentVote, Portfolio, SystemState, Tick


BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class TradingRuntime:
    """Singleton-style runtime safe to share across FastAPI requests."""

    def __init__(self) -> None:
        self.state = SystemState.STOPPED
        self.use_simulated = USE_SIMULATED_FEED
        self.feed = MarketFeed(DEFAULT_SYMBOLS)  # offline/tests only
        self.market = MarketDataService(DEFAULT_SYMBOLS)
        self.events = EventEngine()
        self.indicators = IndicatorEngine()
        self.decision_engine = DecisionEngine()
        self.agents: list[BaseAgent] = default_agents()
        self.ai = AIMarketAnalyst()
        self.portfolio = Portfolio(cash=STARTING_CASH)
        self.tick_count = 0
        self.started_at: float | None = None
        self.last_error: str | None = None
        self.chart_timeframe = DEFAULT_CHART_TIMEFRAME
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._subscribers: set[BroadcastFn] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._persist_path = DATA_DIR / "runtime_state.json"
        self._eval_pending: list[dict[str, Any]] = []
        self._last_fill_ts: dict[str, float] = {}
        self.decision_logs = DecisionLogBuffer()
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001 — never block route registration
            self.last_error = f"data_dir: {exc}"[:200]
        self._load_state()
        if not self.use_simulated:
            try:
                self.market.start()
            except Exception as exc:  # noqa: BLE001 — serve UI even if poller fails
                self.last_error = f"market_start: {exc}"[:200]

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

    def _market_ticks(self) -> list[Tick]:
        if self.use_simulated:
            return self.feed.next_ticks()
        raw = self.market.ticks_from_cache()
        ticks: list[Tick] = []
        for row in raw:
            ticks.append(
                Tick(
                    symbol=row["symbol"],
                    price=float(row["price"]),
                    change_pct=float(row["change_pct"]),
                    volume=float(row["volume"]),
                    ts=float(row["ts"]),
                )
            )
        return ticks

    def _price_history(self) -> dict[str, list[dict[str, Any]]]:
        if self.use_simulated:
            return self.events.chart_history()
        hist = self.market.price_history_points(self.chart_timeframe)
        # Fallback: if candles not ready yet, use event chart buffer.
        if not any(hist.values()):
            return self.events.chart_history()
        return hist

    def snapshot(self) -> dict[str, Any]:
        decisions = self.decision_engine.recent
        market_rows = []
        if self.use_simulated:
            market_rows = self.feed.snapshot()
        else:
            for q in self.market.get_quotes():
                market_rows.append(
                    {
                        "symbol": q.symbol,
                        "price": q.price,
                        "change_pct": q.change_pct,
                        "volume": q.volume,
                        "ts": q.ts,
                        "provider": q.provider,
                        "session": q.session.value,
                        "freshness": q.freshness.value,
                        "stale_reason": q.stale_reason,
                        "asset_class": q.asset_class.value,
                    }
                )
        return {
            "state": self.state.value,
            "tick_count": self.tick_count,
            "started_at": self.started_at,
            "last_error": self.last_error,
            "symbols": list(DEFAULT_SYMBOLS),
            "market": market_rows,
            "price_history": self._price_history(),
            "chart_timeframe": self.chart_timeframe,
            "market_meta": (
                {"source": "SIMULATED", "simulated": True}
                if self.use_simulated
                else self.market.market_meta()
            ),
            "events": self.events.recent,
            "decisions": decisions,
            "decision_logs": self.decision_logs.recent,
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
            ]
            + [{"id": "ai_analyst", "name": "AI Market Analyst"}],
            "ai": self.ai.status(),
            "portfolio": self.portfolio.to_dict(),
            "tick_interval_sec": TICK_INTERVAL_SEC,
            "data_mode": "simulated" if self.use_simulated else "real",
        }

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self.state == SystemState.RUNNING:
                return self.snapshot()
            if not self.use_simulated:
                self.market.start()
                # Ensure first candles/quotes exist before UI paints.
                await asyncio.to_thread(self.market.refresh, True)
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

    def set_chart_timeframe(self, timeframe: str) -> dict[str, Any]:
        tf = timeframe.strip().lower()
        if tf not in {"1m", "5m", "15m", "1h"}:
            raise ValueError("timeframe must be one of 1m,5m,15m,1h")
        self.chart_timeframe = tf
        if not self.use_simulated:
            # Warm in the background — blocking poll of all symbols made the UI
            # freeze for several seconds on every 1m/5m/15m/1h click.
            threading.Thread(
                target=self._warm_candles_background,
                name="trading-warm-candles",
                daemon=True,
            ).start()
        return self.snapshot()

    def _warm_candles_background(self) -> None:
        for symbol in DEFAULT_SYMBOLS:
            try:
                self.market._poll_candles(symbol)  # noqa: SLF001 — intentional warm
            except Exception:  # noqa: BLE001
                pass

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
        ticks = self._market_ticks()
        if not ticks:
            # No live quotes yet / market closed with no cache — wait without crashing.
            await self._broadcast(
                {
                    "type": "tick",
                    "payload": {
                        "tick_count": self.tick_count,
                        "state": self.state.value,
                        "market": [],
                        "price_points": [],
                        "events": [],
                        "votes": [],
                        "decisions": [],
                        "trades": [],
                        "portfolio": self.portfolio.to_dict(),
                        "ai": self.ai.status(),
                        "market_meta": self.snapshot()["market_meta"],
                    },
                }
            )
            return

        self.tick_count += 1
        new_events = self.events.process(ticks)
        decisions_out: list[dict[str, Any]] = []
        votes_out: list[dict[str, Any]] = []
        ai_votes_out: list[dict[str, Any]] = []

        for tick in ticks:
            # Prefer real candle closes for heuristics; fall back to short event history.
            candles = []
            if not self.use_simulated:
                candles = self.market.get_candles(tick.symbol, "5m") or self.market.get_candles(
                    tick.symbol, "1m"
                )
            history = [c.close for c in candles] if candles else self.events.history_prices(tick.symbol)
            indicator = self.indicators.compute(tick.symbol, candles, price=tick.price)

            heuristic_votes = [agent.vote(tick, history, new_events) for agent in self.agents]
            votes_out.extend(v.to_dict() for v in heuristic_votes)

            ai_snapshot = {
                **indicator.compact_for_ai(),
                "events": [e.kind for e in new_events if e.symbol == tick.symbol][:5],
                "heuristic_votes": {v.agent_id: v.side.value for v in heuristic_votes},
            }
            ai_vote = self.ai.maybe_vote(tick, ai_snapshot, new_events, heuristic_votes)
            all_votes = list(heuristic_votes)
            if ai_vote is not None:
                all_votes.append(ai_vote)
                ai_votes_out.append(ai_vote.to_dict())
                votes_out.append(ai_vote.to_dict())

            decision = self.decision_engine.decide(tick.symbol, all_votes, tick.price)
            if decision is None:
                continue

            block_reason: str | None = None
            cooldown_remaining: float | None = None
            if decision.executed:
                last_fill = self._last_fill_ts.get(tick.symbol, 0.0)
                elapsed = time.time() - last_fill
                if last_fill and elapsed < FILL_COOLDOWN_SEC:
                    cooldown_remaining = FILL_COOLDOWN_SEC - elapsed
                    decision.executed = False
                    decision.fill_price = None
                    decision.quantity = None
                    block_reason = (
                        f"Cooldown active ({FILL_COOLDOWN_SEC:.0f}s); "
                        f"{cooldown_remaining:.0f}s remaining"
                    )
                    decision.rationale = f"{decision.rationale} · {block_reason}"
                else:
                    self.portfolio = self.decision_engine.apply_fill(self.portfolio, decision)
                    if decision.executed:
                        self._last_fill_ts[tick.symbol] = time.time()
                    else:
                        block_reason = (
                            "Insufficient cash for minimum buy notional"
                            if decision.side.value == "BUY"
                            else "No open position to sell"
                        )
                        decision.rationale = f"{decision.rationale} · {block_reason}"

            quote = None if self.use_simulated else self.market.get_quote(tick.symbol)
            quote_meta = (
                {
                    "provider": quote.provider if quote else "simulated",
                    "session": quote.session.value if quote else "open",
                    "freshness": quote.freshness.value if quote else "live",
                    "stale_reason": quote.stale_reason if quote else None,
                    "asset_class": quote.asset_class.value if quote else "crypto",
                }
                if not self.use_simulated
                else {"provider": "simulated", "session": "open", "freshness": "live"}
            )
            event_dicts = [e.to_dict() for e in new_events]
            market_snap = build_market_snapshot(
                symbol=tick.symbol,
                price=tick.price,
                ts=tick.ts,
                volume=tick.volume,
                indicator=indicator.to_dict(),
                events=event_dicts,
                quote_meta=quote_meta,
            )
            agent_entries = build_agent_entries(
                [v.to_dict() for v in all_votes],
                self.ai._last_by_symbol.get(tick.symbol),  # noqa: SLF001
            )
            execution = build_execution(
                decision=decision,
                block_reason=block_reason,
                cooldown_remaining_sec=cooldown_remaining,
                last_fill_ts=self._last_fill_ts.get(tick.symbol),
            )
            prev_log = self.decision_logs._last_by_symbol.get(tick.symbol)  # noqa: SLF001
            kind = classify_kind(decision=decision, execution=execution, prev=prev_log)
            log_record = build_decision_log(
                decision=decision,
                market=market_snap,
                agents=agent_entries,
                execution=execution,
                kind=kind,
            )
            self.decision_logs.add(log_record)

            decisions_out.append(decision.to_dict())

            # Evaluation log for later "did AI help?" analysis.
            self.ai.log_evaluation_row(
                {
                    "timestamp": time.time(),
                    "symbol": tick.symbol,
                    "market_price": tick.price,
                    "market_snapshot": ai_snapshot,
                    "indicators": indicator.to_dict(),
                    "heuristic_votes": [v.to_dict() for v in heuristic_votes],
                    "ai_vote": None if ai_vote is None else ai_vote.to_dict(),
                    "ai_meta": self.ai._last_by_symbol.get(tick.symbol),  # noqa: SLF001
                    "final_decision": decision.to_dict(),
                    "decision_log": log_record,
                    "trade_executed": decision.executed,
                    "portfolio": self.portfolio.to_dict(),
                    "followup_targets": {
                        "t_plus_5m": time.time() + 300,
                        "t_plus_15m": time.time() + 900,
                        "t_plus_1h": time.time() + 3600,
                    },
                }
            )

        if self.tick_count % 5 == 0:
            self._save_state()

        price_points = [
            {
                "symbol": t.symbol,
                "ts": t.ts,
                "price": t.price,
                "volume": t.volume,
                "change_pct": t.change_pct,
            }
            for t in ticks
        ]

        await self._broadcast(
            {
                "type": "tick",
                "payload": {
                    "tick_count": self.tick_count,
                    "state": self.state.value,
                    "market": [
                        {
                            **t.to_dict(),
                            **(
                                {}
                                if self.use_simulated
                                else {
                                    "provider": (self.market.get_quote(t.symbol).provider if self.market.get_quote(t.symbol) else ""),
                                    "session": (
                                        self.market.get_quote(t.symbol).session.value
                                        if self.market.get_quote(t.symbol)
                                        else "unknown"
                                    ),
                                    "freshness": (
                                        self.market.get_quote(t.symbol).freshness.value
                                        if self.market.get_quote(t.symbol)
                                        else DataFreshness.UNAVAILABLE.value
                                    ),
                                }
                            ),
                        }
                        for t in ticks
                    ],
                    "price_points": price_points,
                    "price_history": self._price_history(),
                    "chart_timeframe": self.chart_timeframe,
                    "events": [e.to_dict() for e in new_events],
                    "votes": votes_out,
                    "ai_votes": ai_votes_out,
                    "decisions": decisions_out,
                    "decision_logs": self.decision_logs.recent,
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
                    "ai": self.ai.status(),
                    "market_meta": self.snapshot()["market_meta"],
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
