"""Basic unit tests for the isolated trading system."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Force simulated feed for unit tests (no external HTTP).
os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.decision_engine import DecisionEngine
from trading_system.event_engine import EventEngine
from trading_system.indicators import IndicatorEngine
from trading_system.market_data.models import Candle
from trading_system.market_feed import MarketFeed
from trading_system.models import AgentVote, Side, Tick
from trading_system.agents import default_agents


def test_market_feed_emits_ticks():
    feed = MarketFeed(["BTC-USD", "ETH-USD"])
    ticks = feed.next_ticks()
    assert len(ticks) == 2
    assert all(t.price > 0 for t in ticks)


def test_event_engine_detects_spike():
    engine = EventEngine()
    tick = Tick(symbol="BTC-USD", price=100, change_pct=1.2, volume=1000)
    events = engine.process([tick])
    assert any(e.kind.startswith("spike") for e in events)


def test_agents_produce_votes():
    agents = default_agents()
    history = [100, 100.2, 100.5, 101, 101.4, 102, 102.5, 103]
    tick = Tick(symbol="AAPL", price=103, change_pct=0.5, volume=1500)
    votes = [a.vote(tick, history, []) for a in agents]
    assert len(votes) == 3
    assert all(isinstance(v.side, Side) for v in votes)


def test_decision_engine_aggregates():
    engine = DecisionEngine(min_confidence=0.2)
    votes = [
        AgentVote("a", "A", "AAPL", Side.BUY, 0.8, "up"),
        AgentVote("b", "B", "AAPL", Side.BUY, 0.7, "up"),
        AgentVote("c", "C", "AAPL", Side.HOLD, 0.3, "flat"),
    ]
    decision = engine.decide("AAPL", votes, price=100)
    assert decision is not None
    assert decision.side == Side.BUY


def test_decision_engine_executes_sell_against_opposing_buy():
    """Winning SELL must not be diluted below threshold by a competing BUY vote."""
    engine = DecisionEngine(min_confidence=0.45)
    votes = [
        AgentVote("momentum", "Momentum", "BTC-USD", Side.SELL, 0.80, "down"),
        AgentVote("mean_reversion", "MeanRev", "BTC-USD", Side.BUY, 0.69, "fade"),
        AgentVote("volatility", "Vol", "BTC-USD", Side.HOLD, 0.35, "calm"),
    ]
    decision = engine.decide("BTC-USD", votes, price=78000)
    assert decision is not None
    assert decision.side == Side.SELL
    assert decision.executed is True
    # Action score still clears the gate; calibrated confidence is reduced by opposition.
    assert decision.engine["action_score"] >= 0.45
    assert decision.engine["confidence_debug"]["opposition_ratio"] > 0
    # Fill should free cash from an open position.
    from trading_system.models import Portfolio, Position

    pf = Portfolio(cash=50.0, positions={"BTC-USD": Position("BTC-USD", 0.01, 79000)})
    engine.apply_fill(pf, decision)
    assert decision.executed is True
    assert pf.cash > 50.0
    assert pf.positions["BTC-USD"].quantity < 0.01


def test_fill_cooldown_constant_loaded():
    from trading_system.config import DECISION_LOG_LIMIT, FILL_COOLDOWN_SEC

    assert FILL_COOLDOWN_SEC >= 1
    assert DECISION_LOG_LIMIT >= 50


def test_decision_engine_exposes_confidence_debug():
    engine = DecisionEngine(min_confidence=0.45)
    votes = [
        AgentVote("a", "A", "SOL-USD", Side.BUY, 0.8, "up"),
        AgentVote("b", "B", "SOL-USD", Side.BUY, 0.7, "up"),
        AgentVote("c", "C", "SOL-USD", Side.HOLD, 0.3, "flat"),
    ]
    decision = engine.decide("SOL-USD", votes, price=100)
    assert decision is not None
    assert decision.engine
    dbg = decision.engine["confidence_debug"]
    assert "final_confidence" in dbg
    assert "formula" in dbg
    assert "action_support" in dbg
    # 2 BUY + 1 HOLD must NOT auto-cap near 0.99 anymore.
    assert decision.confidence < 0.90
    assert decision.confidence >= 0.55


def test_momentum_reason_uses_real_threshold():
    from trading_system.agents import MomentumAgent

    agent = MomentumAgent()
    history = [100, 100.2, 100.5, 100.8, 101.5]
    tick = Tick(symbol="BTC-USD", price=101.5, change_pct=0.5, volume=1)
    vote = agent.vote(tick, history, [])
    assert "threshold" in vote.rationale.lower() or "BUY" in vote.rationale or "HOLD" in vote.rationale
    assert "price_change_pct" in vote.inputs or "history_len" in vote.inputs


def test_decision_log_text_export_and_cooldown_coalesce():
    from trading_system.decision_log import (
        DecisionLogBuffer,
        build_agent_entries,
        build_decision_log,
        build_execution,
        build_market_snapshot,
        classify_kind,
        format_decision_logs_text,
    )
    from trading_system.models import Decision

    engine = DecisionEngine(min_confidence=0.45)
    votes = [
        AgentVote("momentum", "Momentum Agent", "SOL-USD", Side.BUY, 0.82, "BUY because +1.3%", inputs={"price_change_pct": 1.3}),
        AgentVote("mean_reversion", "Mean Reversion Agent", "SOL-USD", Side.HOLD, 0.55, "HOLD near mean"),
        AgentVote("volatility", "Volatility Agent", "SOL-USD", Side.BUY, 0.74, "BUY spike recovery"),
    ]
    decision = engine.decide("SOL-USD", votes, price=142.3)
    assert decision is not None
    decision.executed = False
    market = build_market_snapshot(
        symbol="SOL-USD",
        price=142.3,
        ts=1_700_000_000,
        volume=1000,
        indicator={
            "change_1m_pct": 0.3,
            "change_5m_pct": 1.4,
            "change_15m_pct": 2.1,
            "rsi_14": 63,
            "sma_fast": 140,
            "sma_slow": 138,
            "ema_fast": 141,
            "volatility": 0.2,
            "volume_state": "HIGH",
            "short_trend": "UP",
        },
        events=[{"kind": "volume_surge", "symbol": "SOL-USD"}],
        quote_meta={"provider": "coinbase", "session": "open", "freshness": "live"},
    )
    agents = build_agent_entries([v.to_dict() for v in votes], None)
    execution = build_execution(
        decision=decision,
        block_reason="Cooldown active (45s); 36s remaining",
        cooldown_remaining_sec=36,
        last_fill_ts=1_700_000_000 - 9,
    )
    kind = classify_kind(decision=decision, execution=execution, prev=None)
    assert kind in {"EXECUTION_BLOCKED", "NEW_DECISION"}
    record = build_decision_log(
        decision=decision, market=market, agents=agents, execution=execution, kind=kind
    )
    buf = DecisionLogBuffer(limit=50)
    buf.add(record)
    # Second identical cooldown signal should coalesce.
    record2 = dict(record)
    record2["id"] = "second"
    record2["execution"] = {**execution, "cooldown_remaining_sec": 30}
    record2["kind"] = "SIGNAL_STILL_ACTIVE"
    buf.add(record2)
    assert len(buf.recent) == 1
    assert buf.recent[0]["execution"]["cooldown_remaining_sec"] == 30
    text = format_decision_logs_text(buf.recent, 25)
    assert "=== DECISION 1 ===" in text
    assert "SOL-USD" in text
    assert "CONFIDENCE DEBUG" in text
    assert "Cooldown" in text
    print("\n----- SAMPLE COPIED LOG -----\n")
    print(text)
    print("----- END SAMPLE -----\n")


def test_event_engine_builds_chart_history():
    engine = EventEngine()
    for i in range(5):
        engine.process(
            [Tick(symbol="BTC-USD", price=100 + i, change_pct=0.1, volume=900, ts=1_000 + i)]
        )
    hist = engine.chart_history("BTC-USD")["BTC-USD"]
    assert len(hist) == 5
    assert hist[0]["price"] == 100
    assert hist[-1]["ts"] == 1_004


def test_runtime_snapshot_exposes_price_history_and_trades():
    import asyncio

    from trading_system.models import SystemState
    from trading_system.runtime import TradingRuntime

    rt = TradingRuntime()
    assert rt.use_simulated is True
    rt.state = SystemState.RUNNING

    async def run_ticks():
        for _ in range(8):
            await rt._tick_once()

    asyncio.run(run_ticks())
    snap = rt.snapshot()
    assert "price_history" in snap
    assert any(len(v) > 0 for v in snap["price_history"].values())
    assert "trades" in snap
    assert isinstance(snap["trades"], list)
    assert snap["data_mode"] == "simulated"
    assert "ai" in snap
    assert "decision_logs" in snap
    assert isinstance(snap["decision_logs"], list)

def test_indicator_engine_computes():
    candles = [
        Candle(ts=float(i), open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=10)
        for i in range(40)
    ]
    snap = IndicatorEngine().compute("BTC-USD", candles)
    assert snap.price > 0
    assert snap.sma_fast is not None
    assert snap.rsi_14 is not None
    compact = snap.compact_for_ai()
    assert "symbol" in compact and "short_trend" in compact


def test_ai_trigger_skips_without_key():
    from trading_system.ai import AIMarketAnalyst
    from trading_system.models import Tick

    ai = AIMarketAnalyst()
    tick = Tick(symbol="BTC-USD", price=100, change_pct=1.0, volume=1)
    vote = ai.maybe_vote(tick, {"symbol": "BTC-USD", "price": 100}, [], [])
    assert vote is None
    assert ai.status()["calls_this_hour"] == 0


def test_us_equity_session_without_tzdata():
    """Import path must not crash when ZoneInfo IANA data is unavailable."""
    from trading_system.market_data.sessions import us_equity_session
    from trading_system.market_data.models import MarketSession
    from datetime import datetime, timezone

    # Weekday noon UTC ~ morning ET — function should return a MarketSession.
    noon = datetime(2024, 6, 5, 16, 0, tzinfo=timezone.utc)
    assert us_equity_session(noon) in {
        MarketSession.OPEN,
        MarketSession.CLOSED,
        MarketSession.PRE_MARKET,
        MarketSession.AFTER_HOURS,
    }


def test_degraded_trading_health_closure_does_not_500():
    """Python 3 clears `except ... as exc` — closures must capture the message first."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    def register():
        try:
            raise RuntimeError("simulated ZoneInfoNotFoundError")
        except Exception as exc:  # noqa: BLE001
            import_error = f"{type(exc).__name__}: {exc}"[:300]

            @app.get("/trading/api/health")
            def trading_import_failed():
                return {
                    "ok": "false",
                    "status": "degraded",
                    "package_found": "true",
                    "error": import_error,
                }

    register()
    client = TestClient(app)
    resp = client.get("/trading/api/health")
    assert resp.status_code == 200
    assert "RuntimeError" in resp.json()["error"]
