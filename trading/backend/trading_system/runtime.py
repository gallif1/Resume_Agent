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
    OUTCOME_RESOLVE_INTERVAL_SEC,
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
    build_market_snapshot as build_log_market_snapshot,
    classify_kind,
)
from .event_engine import EventEngine
from .indicators import IndicatorEngine
from .market_data import MarketDataService
from .market_data.models import DataFreshness
from .market_feed import MarketFeed
from .models import AgentVote, Portfolio, Side, SystemState, Tick
from .outcomes import OutcomeStore
from .market_data.candle_store import get_market_db
from .market_snapshot import build_market_snapshot


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
        self.outcomes = OutcomeStore()
        self._last_outcome_resolve = 0.0
        self._sim_price_history: dict[str, list[tuple[float, float]]] = {}
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
            "performance": self.outcomes.performance_stats(),
            "tick_interval_sec": TICK_INTERVAL_SEC,
            "data_mode": "simulated" if self.use_simulated else "real",
            "paper_trading_only": True,
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

    async def clear_decision_logs(self) -> dict[str, Any]:
        """Delete recent structured decision logs only (paper portfolio untouched)."""
        async with self._lock:
            cleared = self.decision_logs.clear()
            self.last_error = None
        snap = self.snapshot()
        snap["cleared_logs"] = cleared
        await self._broadcast({"type": "state", "payload": snap})
        return snap

    async def reset_paper_system(self) -> dict[str, Any]:
        """Full paper-trading reset: stop, wipe portfolio/logs/outcomes, start clean."""
        # Stop outside the lock path used by stop() — avoid nested lock.
        await self.stop()
        async with self._lock:
            self.portfolio = Portfolio(cash=STARTING_CASH)
            self.tick_count = 0
            self.started_at = None
            self.last_error = None
            self._last_fill_ts.clear()
            self._eval_pending.clear()
            self._sim_price_history.clear()
            self._last_outcome_resolve = 0.0
            logs_cleared = self.decision_logs.clear()
            decisions_cleared = self.decision_engine.clear()
            outcomes_cleared = self.outcomes.clear()
            self.events.clear_session()
            self.ai.clear_session()
            self._save_state()
        snap = self.snapshot()
        snap["reset"] = {
            "cash": STARTING_CASH,
            "logs_cleared": logs_cleared,
            "decisions_cleared": decisions_cleared,
            "outcomes_cleared": outcomes_cleared,
            "paper_trading_only": True,
        }
        await self._broadcast({"type": "state", "payload": snap})
        return snap

    def set_chart_timeframe(self, timeframe: str) -> dict[str, Any]:
        tf = timeframe.strip().lower()
        if tf not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
            raise ValueError("timeframe must be one of 1m,5m,15m,1h,4h,1d")
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

    def build_symbol_snapshot(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        quote = None if self.use_simulated else self.market.get_quote(symbol)
        candles_by_tf: dict[str, list] = {}
        if self.use_simulated:
            hist = self.events.history_prices(symbol)
            from .market_data.models import Candle

            # Synthetic 1-bar candles from closes for sim mode.
            sim = [
                Candle(ts=time.time() - (len(hist) - i), open=p, high=p, low=p, close=p, volume=0)
                for i, p in enumerate(hist)
            ]
            candles_by_tf["5m"] = sim
        else:
            for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
                candles_by_tf[tf] = self.market.get_candles(symbol, tf)
        anns = get_market_db().list_annotations(symbol)
        pos = self.portfolio.positions.get(symbol)
        last_fill = self._last_fill_ts.get(symbol, 0.0)
        cooldown = None
        if last_fill:
            rem = FILL_COOLDOWN_SEC - (time.time() - last_fill)
            if rem > 0:
                cooldown = rem
        prices = {}
        if not self.use_simulated:
            for q in self.market.get_quotes():
                prices[q.symbol] = q.price
        else:
            for t in self.feed.snapshot():
                prices[t["symbol"]] = t["price"]
        equity = self.portfolio.equity(prices) if hasattr(self.portfolio, "equity") else (
            self.portfolio.cash
            + sum(
                p.quantity * prices.get(sym, p.avg_price)
                for sym, p in self.portfolio.positions.items()
            )
        )
        unrealized = 0.0
        if pos and symbol in prices:
            unrealized = (prices[symbol] - pos.avg_price) * pos.quantity
        portfolio_risk = {
            "available_cash": self.portfolio.cash,
            "current_position": None
            if pos is None
            else {"quantity": pos.quantity, "avg_entry": pos.avg_price},
            "average_entry": None if pos is None else pos.avg_price,
            "unrealized_pnl": unrealized,
            "realized_pnl": self.portfolio.realized_pnl,
            "current_exposure": abs(pos.quantity * prices.get(symbol, pos.avg_price)) if pos else 0.0,
            "position_size": None if pos is None else pos.quantity,
            "stop_loss_level": None,
            "take_profit_level": None,
            "maximum_allowed_risk": None,
            "cooldown_status": "active" if cooldown else "clear",
            "daily_drawdown": None,
            "remaining_daily_loss_limit": None,
            "equity": equity,
        }
        provider = (
            "simulated"
            if self.use_simulated
            else self.market.provider_for_symbol(symbol)
        )
        return build_market_snapshot(
            symbol=symbol,
            quote=quote,
            candles_by_tf=candles_by_tf,
            human_annotations=anns,
            portfolio=portfolio_risk,
            cooldown_remaining_sec=cooldown,
            provider_name=provider,
        )

    def _apply_snapshot_to_vote(self, vote: AgentVote, snap: dict[str, Any]) -> AgentVote:
        """Attach snapshot inputs; reduce confidence / force HOLD when stale."""
        quality = snap.get("data_quality") or {}
        stale = bool(quality.get("stale"))
        missing = quality.get("missing_fields") or []
        human = (snap.get("market_structure") or {}).get("human_levels") or []
        inputs = dict(vote.inputs or {})
        inputs["market_snapshot_keys"] = [
            "multi_timeframe",
            "indicators",
            "market_structure",
            "portfolio_risk",
            "data_quality",
        ]
        inputs["timeframe_signals"] = snap.get("multi_timeframe")
        inputs["indicators_used"] = {
            k: (snap.get("indicators") or {}).get(k)
            for k in ("rsi_14", "sma_20", "sma_50", "macd", "atr_14", "short_trend")
        }
        inputs["human_levels"] = human
        inputs["data_quality"] = quality
        inputs["portfolio_risk"] = {
            "available_cash": (snap.get("portfolio_risk") or {}).get("available_cash"),
            "position_size": (snap.get("portfolio_risk") or {}).get("position_size"),
            "cooldown_status": (snap.get("portfolio_risk") or {}).get("cooldown_status"),
        }
        if stale or "candles" in missing or "live_quote" in missing:
            vote.confidence = min(float(vote.confidence), 0.35)
            if vote.side != Side.HOLD and (stale or "candles" in missing):
                vote.side = Side.HOLD
                vote.rationale = (
                    f"HOLD because market data is stale/missing "
                    f"(age={quality.get('age_seconds')}s, missing={missing}). "
                    f"Original signal suppressed."
                )
                inputs["stale_override"] = True
            else:
                inputs["confidence_reduced_for_stale"] = True
        vote.inputs = inputs
        return vote

    def _remember_sim_price(self, symbol: str, ts: float, price: float) -> None:
        hist = self._sim_price_history.setdefault(symbol, [])
        hist.append((ts, price))
        if len(hist) > 5000:
            self._sim_price_history[symbol] = hist[-5000:]

    def _price_near(self, symbol: str, target_ts: float) -> tuple[float | None, str]:
        if self.use_simulated:
            hist = self._sim_price_history.get(symbol) or []
            if not hist:
                return None, "no_history"
            best = min(hist, key=lambda p: abs(p[0] - target_ts))
            if abs(best[0] - target_ts) > 180:
                return None, "unavailable"
            return best[1], "ok"
        return self.market.price_near(symbol, target_ts)

    def _resolve_outcomes_if_due(self) -> None:
        now = time.time()
        if now - self._last_outcome_resolve < OUTCOME_RESOLVE_INTERVAL_SEC:
            return
        self._last_outcome_resolve = now
        written = self.outcomes.resolve_due(self._price_near, now=now)
        if written:
            # Patch in-memory logs with updated outcome summaries.
            for row in list(self.outcomes._by_id.values()):  # noqa: SLF001
                did = str(row.get("decision_id") or "")
                if not did:
                    continue
                summary = self.outcomes.attach_outcome_summary(did)
                if summary:
                    self.decision_logs.patch_outcome(did, summary)

    def _maybe_pretrade_ai(
        self,
        tick: Tick,
        ai_snapshot: dict[str, Any],
        heuristic_votes: list[AgentVote],
        all_votes: list[AgentVote],
        decision,
    ):
        """If candidate BUY/SELL is about to fill, ensure fresh AI and re-decide."""
        pretrade_meta: dict[str, Any] | None = None
        if decision.side == Side.HOLD or not decision.executed:
            return all_votes, decision, pretrade_meta

        # Skip pretrade when cooldown would block anyway (save API calls).
        last_fill = self._last_fill_ts.get(tick.symbol, 0.0)
        if last_fill and (time.time() - last_fill) < FILL_COOLDOWN_SEC:
            return all_votes, decision, pretrade_meta

        try:
            ai_vote, meta = self.ai.pretrade_vote(tick, ai_snapshot)
            pretrade_meta = meta
        except Exception as exc:  # noqa: BLE001 — never crash trading loop
            pretrade_meta = {
                "source": "PRETRADE_UNAVAILABLE",
                "skip_reason": "exception",
                "reason": str(exc)[:200],
            }
            return all_votes, decision, pretrade_meta

        if ai_vote is None:
            return all_votes, decision, pretrade_meta

        # Replace any prior AI vote, then re-aggregate.
        without_ai = [v for v in all_votes if v.agent_id != "ai_analyst"]
        refreshed = without_ai + [ai_vote]
        new_decision = self.decision_engine.decide(tick.symbol, refreshed, tick.price)
        if new_decision is None:
            return refreshed, decision, pretrade_meta
        return refreshed, new_decision, pretrade_meta

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
                        "performance": self.outcomes.performance_stats(),
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
            if self.use_simulated:
                self._remember_sim_price(tick.symbol, tick.ts or time.time(), tick.price)

            # Prefer real candle closes for heuristics; fall back to short event history.
            candles = []
            if not self.use_simulated:
                candles = self.market.get_candles(tick.symbol, "5m") or self.market.get_candles(
                    tick.symbol, "1m"
                )
                # Update forming candle in store from live tick (no fabricated history).
                try:
                    from .market_data.models import Candle as MCandle

                    tf = self.chart_timeframe if self.chart_timeframe in {"1m", "5m", "15m", "1h"} else "5m"
                    secs = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}[tf]
                    bucket = int(tick.ts // secs * secs)
                    existing = self.market.get_candles(tick.symbol, tf)
                    last = existing[-1] if existing else None
                    if last and int(last.ts) == bucket:
                        updated = MCandle(
                            ts=float(bucket),
                            open=last.open,
                            high=max(last.high, tick.price),
                            low=min(last.low, tick.price),
                            close=tick.price,
                            volume=last.volume,
                        )
                    else:
                        updated = MCandle(
                            ts=float(bucket),
                            open=tick.price,
                            high=tick.price,
                            low=tick.price,
                            close=tick.price,
                            volume=tick.volume,
                        )
                    self.market.db.upsert_candles(tick.symbol, tf, [updated])
                    with self.market._lock:  # noqa: SLF001
                        cur = list(self.market._candles.get((tick.symbol, tf), []))
                        if cur and int(cur[-1].ts) == bucket:
                            cur[-1] = updated
                        else:
                            cur.append(updated)
                        self.market._candles[(tick.symbol, tf)] = cur[-500:]
                except Exception:  # noqa: BLE001
                    pass
            history = [c.close for c in candles] if candles else self.events.history_prices(tick.symbol)
            indicator = self.indicators.compute(tick.symbol, candles, price=tick.price)

            # Rich MarketSnapshot for agents (deterministic; no raw tick flood).
            try:
                agent_snap = self.build_symbol_snapshot(tick.symbol)
            except Exception:  # noqa: BLE001
                agent_snap = {"data_quality": {"stale": True, "missing_fields": ["snapshot_error"]}}

            heuristic_votes = []
            for agent in self.agents:
                v = agent.vote(tick, history, new_events)
                heuristic_votes.append(self._apply_snapshot_to_vote(v, agent_snap))
            votes_out.extend(v.to_dict() for v in heuristic_votes)

            ai_snapshot = {
                **indicator.compact_for_ai(),
                "events": [e.kind for e in new_events if e.symbol == tick.symbol][:5],
                "heuristic_votes": {v.agent_id: v.side.value for v in heuristic_votes},
                "multi_timeframe": agent_snap.get("multi_timeframe"),
                "human_levels": (agent_snap.get("market_structure") or {}).get("human_levels"),
                "data_quality": agent_snap.get("data_quality"),
                "market_structure": {
                    "swing_highs": ((agent_snap.get("market_structure") or {}).get("swing_highs") or [])[-3:],
                    "swing_lows": ((agent_snap.get("market_structure") or {}).get("swing_lows") or [])[-3:],
                    "breakout_or_rejection": (agent_snap.get("market_structure") or {}).get(
                        "breakout_or_rejection"
                    ),
                },
            }
            ai_vote = self.ai.maybe_vote(tick, ai_snapshot, new_events, heuristic_votes)
            all_votes = list(heuristic_votes)
            if ai_vote is not None:
                ai_vote = self._apply_snapshot_to_vote(ai_vote, agent_snap)
                all_votes.append(ai_vote)
                ai_votes_out.append(ai_vote.to_dict())
                votes_out.append(ai_vote.to_dict())

            decision = self.decision_engine.decide(tick.symbol, all_votes, tick.price)
            if decision is None:
                continue

            # Candidate BUY/SELL → pretrade AI validation + re-decide before fill.
            all_votes, decision, pretrade_meta = self._maybe_pretrade_ai(
                tick, ai_snapshot, heuristic_votes, all_votes, decision
            )
            if pretrade_meta and any(v.agent_id == "ai_analyst" for v in all_votes):
                ai_v = next(v for v in all_votes if v.agent_id == "ai_analyst")
                if not any(x.get("agent_id") == "ai_analyst" for x in ai_votes_out):
                    ai_votes_out.append(ai_v.to_dict())
                    votes_out.append(ai_v.to_dict())

            block_reason: str | None = None
            cooldown_remaining: float | None = None
            cash_before = self.portfolio.cash
            pos_before = None
            if decision.symbol in self.portfolio.positions:
                p = self.portfolio.positions[decision.symbol]
                pos_before = {"quantity": p.quantity, "avg_price": p.avg_price}

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
            market_snap = build_log_market_snapshot(
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
            kind = classify_kind(
                decision=decision,
                execution=execution,
                prev=prev_log,
                pretrade=pretrade_meta,
            )

            trade_payload = None
            if decision.executed and decision.side != Side.HOLD:
                pos_after = None
                if decision.symbol in self.portfolio.positions:
                    p = self.portfolio.positions[decision.symbol]
                    pos_after = {"quantity": p.quantity, "avg_price": p.avg_price}
                trade_payload = {
                    "fill_price": decision.fill_price,
                    "quantity": decision.quantity,
                    "position_before": pos_before,
                    "position_after": pos_after,
                    "cash_before": cash_before,
                    "cash_after": self.portfolio.cash,
                    "realized_pnl": self.portfolio.realized_pnl,
                }

            outcome_summary = None
            if decision.side in {Side.BUY, Side.SELL}:
                # Track signal quality for new actionable decisions / fills.
                should_track = kind in {
                    "NEW_DECISION",
                    "TRADE_EXECUTED",
                    "EXECUTION_BLOCKED",
                } or bool(pretrade_meta)
                if should_track:
                    self.outcomes.record_actionable(
                        decision_id=decision.id,
                        symbol=decision.symbol,
                        action=decision.side.value,
                        entry_price=tick.price,
                        entry_ts=time.time(),
                        final_confidence=decision.confidence,
                        agent_votes=[v.to_dict() for v in all_votes],
                        executed=bool(decision.executed),
                        trade=trade_payload,
                        kind=kind,
                    )
                    outcome_summary = self.outcomes.attach_outcome_summary(decision.id)
                else:
                    existing = self.outcomes.get_by_decision(
                        (prev_log or {}).get("id") or ""
                    )
                    if existing:
                        outcome_summary = self.outcomes.attach_outcome_summary(
                            str(existing.get("decision_id"))
                        )

            log_record = build_decision_log(
                decision=decision,
                market=market_snap,
                agents=agent_entries,
                execution=execution,
                kind=kind,
                pretrade=pretrade_meta,
                outcome=outcome_summary,
            )
            stored = self.decision_logs.add(log_record)
            if stored is None and outcome_summary and prev_log:
                self.decision_logs.patch_outcome(str(prev_log.get("id")), outcome_summary)

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
                    "pretrade": pretrade_meta,
                    "final_decision": decision.to_dict(),
                    "decision_log": log_record if stored is not None else None,
                    "trade_executed": decision.executed,
                    "portfolio": self.portfolio.to_dict(),
                    "followup_targets": {
                        "t_plus_5m": time.time() + 300,
                        "t_plus_15m": time.time() + 900,
                        "t_plus_1h": time.time() + 3600,
                    },
                }
            )

        self._resolve_outcomes_if_due()

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

        candle_updates: list[dict[str, Any]] = []
        if not self.use_simulated:
            tf = self.chart_timeframe
            for t in ticks:
                cs = self.market.get_candles(t.symbol, tf)
                if cs:
                    candle_updates.append(
                        {
                            "symbol": t.symbol,
                            "timeframe": tf,
                            "candle": cs[-1].to_dict(),
                            "provider": self.market.provider_for_symbol(t.symbol),
                        }
                    )

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
                    "candle_updates": candle_updates,
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
                    "performance": self.outcomes.performance_stats(),
                    "market_meta": self.snapshot()["market_meta"],
                    "paper_trading_only": True,
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
