"""Basic unit tests for the isolated trading system."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.decision_engine import DecisionEngine
from trading_system.event_engine import EventEngine
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
