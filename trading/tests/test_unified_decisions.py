"""UnifiedDecision correlation, explanations, and persistence."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.market_data.candle_store import MarketDB
from trading_system.models import AgentVote, Decision, Side
from trading_system.unified_decision import (
    MISSING_EXPLANATION_HE,
    build_unified_decision,
    group_historical_events_without_decision_id,
)


def test_decision_id_correlation_on_paper_events(tmp_path):
    db = MarketDB(path=tmp_path / "m.db")
    did = "dec-abc123"
    db.upsert_paper_event(
        {
            "event_type": "fill",
            "event_id": did,
            "decision_id": did,
            "symbol": "SOL-USD",
            "side": "BUY",
            "ts": 1_725_000_000,
            "price": 100.0,
            "quantity": 10.0,
            "confidence": 0.7,
            "status": "FILLED",
            "payload": {"decision_id": did, "fill_price": 100.0},
        }
    )
    db.upsert_paper_event(
        {
            "event_type": "agent_vote",
            "event_id": f"{did}:momentum",
            "decision_id": did,
            "symbol": "SOL-USD",
            "side": "BUY",
            "ts": 1_725_000_001,
            "confidence": 0.7,
            "status": "VOTE",
            "payload": {"decision_id": did, "agent_id": "momentum"},
        }
    )
    rows = db.list_paper_events("SOL-USD")
    assert len(rows) == 2
    assert all(r["decision_id"] == did for r in rows)
    assert all(r["payload"].get("decision_id") == did for r in rows)


def test_unified_decision_upsert_and_list(tmp_path):
    db = MarketDB(path=tmp_path / "m.db")
    decision = Decision(
        id="ud1",
        symbol="BTC-USD",
        side=Side.BUY,
        confidence=0.66,
        votes=[],
        rationale="test",
        executed=True,
        fill_price=50000.0,
        quantity=0.01,
        engine={"explanation": "ציון BUY עבר סף."},
    )
    votes = [
        AgentVote("momentum", "מומנטום", "BTC-USD", Side.BUY, 0.7, "עולה"),
        AgentVote("mean", "ממוצע", "BTC-USD", Side.HOLD, 0.4, "נייטרלי"),
    ]
    unified = build_unified_decision(
        decision=decision,
        votes=votes,
        execution={"status": "FILLED", "fill_price": 50000.0, "quantity": 0.01},
        market_snapshot={"rsi_14": 55.0, "trend": "up"},
        indicator_evidence={"rsi_14": 55.0},
        filled=True,
        timeframe="5m",
    )
    assert unified["decision_id"] == "ud1"
    assert unified["status"] == "FILLED"
    assert 2 <= len(unified["primary_reasons"]) <= 4
    assert MISSING_EXPLANATION_HE not in unified["primary_reasons"]

    saved = db.upsert_unified_decision(unified)
    assert saved["decision_id"] == "ud1"
    got = db.get_unified_decision("ud1")
    assert got is not None
    assert got["final_action"] == "BUY"
    listed = db.list_unified_decisions("BTC-USD")
    assert len(listed) == 1
    assert listed[0]["decision_id"] == "ud1"


def test_no_fabricated_explanations_when_missing_data():
    unified = build_unified_decision(
        decision={
            "decision_id": "hist-1",
            "symbol": "AAPL",
            "side": "BUY",
            "confidence": 0.5,
            "decision_time": time.time(),
        },
        votes=None,
        execution=None,
        market_snapshot=None,
        indicator_evidence=None,
    )
    assert unified["primary_reasons"] == [MISSING_EXPLANATION_HE]
    assert unified["risk_factors"] == [MISSING_EXPLANATION_HE]
    assert MISSING_EXPLANATION_HE in unified["summary"]


def test_blocked_status_persisted(tmp_path):
    db = MarketDB(path=tmp_path / "m.db")
    decision = Decision(
        id="blk1",
        symbol="ETH-USD",
        side=Side.BUY,
        confidence=0.8,
        votes=[],
        rationale="signal",
        executed=False,
    )
    unified = build_unified_decision(
        decision=decision,
        votes=[AgentVote("a", "A", "ETH-USD", Side.BUY, 0.8, "up")],
        execution={"status": "BLOCKED", "reason": "מעקב בלבד"},
        filled=False,
        block_reason="ETH-USD במצב מעקב בלבד — ביצוע הזמנות חסום.",
    )
    assert unified["status"] == "BLOCKED"
    db.upsert_unified_decision(unified)
    assert db.get_unified_decision("blk1")["status"] == "BLOCKED"


def test_historical_grouping_helper_documented():
    events = [
        {
            "event_type": "fill",
            "event_id": "legacy-1",
            "symbol": "SOLUSD",
            "ts": 1_725_000_100,
            "payload": {"order_id": "ord-9"},
        },
        {
            "event_type": "agent_vote",
            "event_id": "legacy-v",
            "symbol": "SOL/USD",
            "ts": 1_725_000_120,
            "payload": {"order_id": "ord-9"},
        },
        {
            "event_type": "fill",
            "event_id": "with-id",
            "decision_id": "keep-me",
            "symbol": "SOL-USD",
            "ts": 1_725_000_200,
            "payload": {"decision_id": "keep-me"},
        },
    ]
    groups = group_historical_events_without_decision_id(events, timeframe="5m")
    # Events that already have decision_id are excluded from fallback groups.
    flat = [e for g in groups for e in g]
    assert all(e.get("decision_id") is None for e in flat)
    assert any(len(g) == 2 for g in groups)


def test_paper_events_migration_adds_decision_id(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE paper_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            event_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            ts REAL NOT NULL,
            price REAL,
            quantity REAL,
            confidence REAL,
            status TEXT,
            payload TEXT NOT NULL DEFAULT '{}',
            UNIQUE(event_type, event_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO paper_events(id,event_type,event_id,symbol,side,ts,payload) "
        "VALUES(?,?,?,?,?,?,?)",
        ("fill:old", "fill", "old", "BTC-USD", "BUY", 1.0, "{}"),
    )
    conn.commit()
    conn.close()

    db = MarketDB(path=path)
    # Migration should have added the column without destroying data.
    row = db.get_paper_event("fill:old")
    assert row is not None
    assert row["symbol"] == "BTC-USD"
    assert "decision_id" in row
