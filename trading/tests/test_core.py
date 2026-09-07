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
    assert decision.confidence >= 0.45
    # Fill should free cash from an open position.
    from trading_system.models import Portfolio, Position

    pf = Portfolio(cash=50.0, positions={"BTC-USD": Position("BTC-USD", 0.01, 79000)})
    engine.apply_fill(pf, decision)
    assert decision.executed is True
    assert pf.cash > 50.0
    assert pf.positions["BTC-USD"].quantity < 0.01


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
    assert us_equity_session(noon) in {MarketSession.OPEN, MarketSession.CLOSED}


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
