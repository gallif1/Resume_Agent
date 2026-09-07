"""Tests for paper clear-logs and full system reset."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.config import STARTING_CASH
from trading_system.decision_log import DecisionLogBuffer
from trading_system.models import Portfolio, Position, SystemState
from trading_system.outcomes import OutcomeStore
from trading_system.runtime import TradingRuntime


def test_decision_log_buffer_clear():
    buf = DecisionLogBuffer(limit=20, heartbeat_sec=60)
    buf.add(
        {
            "id": "a",
            "symbol": "AAPL",
            "kind": "NEW_DECISION",
            "timestamp": 1.0,
            "decision": {"action": "BUY", "final_confidence": 0.7},
            "execution": {"status": "NOT_FILLED", "reason": "HOLD"},
            "signal": {"agents": []},
            "market": {},
        }
    )
    assert len(buf.recent) == 1
    assert buf.clear() == 1
    assert buf.recent == []


def test_outcome_store_clear(tmp_path):
    path = tmp_path / "out.jsonl"
    store = OutcomeStore(path=path)
    store.record_actionable(
        decision_id="d1",
        symbol="AAPL",
        action="BUY",
        entry_price=100,
        agent_votes=[],
    )
    assert store.get_by_decision("d1") is not None
    assert store.clear() >= 1
    assert store.get_by_decision("d1") is None
    store2 = OutcomeStore(path=path)
    assert store2.get_by_decision("d1") is None


def test_clear_logs_keeps_portfolio():
    rt = TradingRuntime()
    rt.portfolio = Portfolio(cash=50_000, positions={"AAPL": Position("AAPL", 1.0, 100)})
    rt.decision_logs.add(
        {
            "id": "x",
            "symbol": "AAPL",
            "kind": "TRADE_EXECUTED",
            "timestamp": 1.0,
            "decision": {"action": "BUY", "final_confidence": 0.8},
            "execution": {"status": "FILLED"},
            "signal": {"agents": []},
            "market": {},
        }
    )

    async def go():
        return await rt.clear_decision_logs()

    snap = asyncio.run(go())
    assert snap.get("cleared_logs", 0) >= 1
    assert rt.decision_logs.recent == []
    assert rt.portfolio.cash == 50_000
    assert "AAPL" in rt.portfolio.positions


def test_reset_paper_system_starts_fresh(tmp_path, monkeypatch):
    # Isolate persistence files for this runtime instance.
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp_path))
    # Runtime already constructed modules — point paths manually after init.
    rt = TradingRuntime()
    rt._persist_path = tmp_path / "runtime_state.json"  # noqa: SLF001
    rt.outcomes = OutcomeStore(path=tmp_path / "decision_outcomes.jsonl")

    rt.state = SystemState.PAUSED
    rt.tick_count = 99
    rt.portfolio = Portfolio(
        cash=12_000,
        realized_pnl=123.0,
        positions={"SOL-USD": Position("SOL-USD", 2.0, 140)},
    )
    rt._last_fill_ts["SOL-USD"] = 1.0  # noqa: SLF001
    rt.decision_logs.add(
        {
            "id": "y",
            "symbol": "SOL-USD",
            "kind": "NEW_DECISION",
            "timestamp": 1.0,
            "decision": {"action": "BUY", "final_confidence": 0.6},
            "execution": {"status": "NOT_FILLED", "reason": "x"},
            "signal": {"agents": []},
            "market": {},
        }
    )
    rt.outcomes.record_actionable(
        decision_id="y",
        symbol="SOL-USD",
        action="BUY",
        entry_price=140,
        agent_votes=[{"agent_id": "momentum", "side": "BUY"}],
    )

    async def go():
        return await rt.reset_paper_system()

    snap = asyncio.run(go())
    assert rt.state == SystemState.STOPPED
    assert rt.tick_count == 0
    assert rt.portfolio.cash == STARTING_CASH
    assert rt.portfolio.realized_pnl == 0.0
    assert rt.portfolio.positions == {}
    assert rt.decision_logs.recent == []
    assert rt.outcomes.get_by_decision("y") is None
    assert rt._last_fill_ts == {}  # noqa: SLF001
    assert snap.get("paper_trading_only") is True
    assert snap.get("reset", {}).get("cash") == STARTING_CASH
    # Persisted state rewritten to starting cash.
    assert rt._persist_path.is_file()  # noqa: SLF001
    raw = rt._persist_path.read_text(encoding="utf-8")  # noqa: SLF001
    assert str(int(STARTING_CASH)) in raw or f"{STARTING_CASH}" in raw


def test_reset_endpoints_registered():
    from trading_system.api import router

    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/clear-logs" in paths
    assert "/api/reset" in paths
