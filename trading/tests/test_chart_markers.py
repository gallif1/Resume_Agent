"""Chart marker persistence, symbol/time bucketing, and API coverage."""

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
from trading_system.time_utils import (
    candle_bucket_ts,
    marker_key,
    normalize_symbol,
    to_unix_seconds,
)


def test_normalize_symbol_sol_variants():
    assert normalize_symbol("SOL-USD") == "SOL-USD"
    assert normalize_symbol("sol/usd") == "SOL-USD"
    assert normalize_symbol("SOLUSD") == "SOL-USD"
    assert normalize_symbol(" sol-usd ") == "SOL-USD"
    assert normalize_symbol("BTCUSD") == "BTC-USD"
    assert normalize_symbol("AAPL") == "AAPL"


def test_to_unix_seconds_ms_and_sec():
    assert to_unix_seconds(1_700_000_000) == 1_700_000_000
    assert abs(to_unix_seconds(1_700_000_000_000) - 1_700_000_000) < 1e-9
    assert to_unix_seconds(1_725_000_000.5) == 1_725_000_000.5


def test_candle_bucket_mapping_1m_5m_15m_1h():
    import calendar
    from datetime import datetime, timezone

    ts = calendar.timegm(datetime(2026, 9, 11, 8, 44, 26, tzinfo=timezone.utc).timetuple())
    assert candle_bucket_ts(ts, "1m") == calendar.timegm(
        datetime(2026, 9, 11, 8, 44, 0, tzinfo=timezone.utc).timetuple()
    )
    assert candle_bucket_ts(ts, "5m") == calendar.timegm(
        datetime(2026, 9, 11, 8, 40, 0, tzinfo=timezone.utc).timetuple()
    )
    assert candle_bucket_ts(ts, "15m") == calendar.timegm(
        datetime(2026, 9, 11, 8, 30, 0, tzinfo=timezone.utc).timetuple()
    )
    assert candle_bucket_ts(ts, "1h") == calendar.timegm(
        datetime(2026, 9, 11, 8, 0, 0, tzinfo=timezone.utc).timetuple()
    )
    assert candle_bucket_ts(ts * 1000, "1m") == candle_bucket_ts(ts, "1m")


def test_marker_key_stable_unique():
    assert marker_key("fill", "abc") == "fill:abc"
    assert marker_key("agent_vote", "d1:a1") == "agent_vote:d1:a1"


def test_paper_events_persist_and_list(tmp_path):
    db = MarketDB(path=tmp_path / "m.db")
    db.upsert_paper_event(
        {
            "event_type": "fill",
            "event_id": "fill-sol-1",
            "symbol": "SOLUSD",
            "side": "BUY",
            "ts": 1_725_000_000,
            "price": 99.89,
            "quantity": 18.8446,
            "confidence": 0.69,
            "status": "FILLED",
            "payload": {"fill_price": 99.89, "order_id": "ord-1"},
        }
    )
    db.upsert_paper_event(
        {
            "event_type": "agent_vote",
            "event_id": "dec1:mean",
            "symbol": "SOL/USD",
            "side": "BUY",
            "ts": 1_725_000_010,
            "confidence": 0.69,
            "status": "VOTE",
            "payload": {"agent_name": "Mean Reversion"},
        }
    )
    db.upsert_paper_event(
        {
            "event_type": "fill",
            "event_id": "fill-sol-1",
            "symbol": "SOL-USD",
            "side": "BUY",
            "ts": 1_725_000_000,
            "price": 99.89,
            "quantity": 18.8446,
            "status": "FILLED",
            "payload": {"fill_price": 99.89},
        }
    )
    rows = db.list_paper_events("SOL-USD")
    assert len(rows) == 2
    fills = [r for r in rows if r["event_type"] == "fill"]
    assert len(fills) == 1
    assert fills[0]["symbol"] == "SOL-USD"
    assert fills[0]["id"] == "fill:fill-sol-1"
    assert fills[0]["quantity"] == 18.8446

    ranged = db.list_paper_events("SOL-USD", from_ts=1_725_000_005, to_ts=1_725_000_020)
    assert len(ranged) == 1
    assert ranged[0]["event_type"] == "agent_vote"


def test_runtime_chart_markers_backfill(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp_path))
    from trading_system.market_data import candle_store as cs

    cs._db = None
    from trading_system.runtime import TradingRuntime

    rt = TradingRuntime()
    db = cs.get_market_db()
    ts = time.time()
    rt.decision_logs.add(
        {
            "id": "log-sol-1",
            "timestamp": ts,
            "symbol": "SOL-USD",
            "kind": "TRADE_EXECUTED",
            "signal": {
                "agents": [
                    {
                        "agent_id": "mean",
                        "agent_name": "Mean Reversion",
                        "action": "BUY",
                        "confidence": 0.69,
                    }
                ]
            },
            "decision": {"action": "BUY", "final_confidence": 0.69, "rationale": "below mean"},
            "execution": {
                "status": "FILLED",
                "fill_price": 99.89,
                "quantity": 18.8446,
            },
            "outcome": {},
            "market": {},
            "pretrade": {},
        },
        force=True,
    )
    out = rt.chart_markers("SOLUSD", timeframe="1m")
    assert out["count"] >= 1
    fills = [m for m in out["markers"] if m["event_type"] == "fill"]
    assert fills
    assert fills[0]["symbol"] == "SOL-USD"
    assert "candle_ts" in fills[0]

    db.upsert_paper_event(
        {
            "event_type": "fill",
            "event_id": "extra-fill",
            "symbol": "sol/usd",
            "side": "BUY",
            "ts": ts - 60,
            "price": 100.0,
            "quantity": 1.0,
            "status": "FILLED",
            "payload": {},
        }
    )
    out2 = rt.chart_markers("SOL-USD", timeframe="5m")
    assert out2["count"] >= 2

    btc = rt.chart_markers("BTC-USD", timeframe="1m")
    assert all(m["symbol"] != "SOL-USD" for m in btc["markers"]) or btc["count"] == 0


def test_group_events_same_candle():
    ts = 1_725_000_026
    b1 = candle_bucket_ts(ts, "1m")
    b5 = candle_bucket_ts(ts, "5m")
    events = [
        {"ts": ts, "kind": "fill"},
        {"ts": ts + 10, "kind": "vote"},
    ]
    g1 = {}
    for e in events:
        g1.setdefault(candle_bucket_ts(e["ts"], "1m"), []).append(e)
    assert len(g1[b1]) == 2
    g5 = {}
    for e in events:
        g5.setdefault(candle_bucket_ts(e["ts"], "5m"), []).append(e)
    assert len(g5[b5]) == 2


def test_chart_markers_api_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp_path))
    from trading_system.market_data import candle_store as cs

    cs._db = None
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from trading_system.api import create_trading_router

    db = cs.get_market_db()
    db.upsert_paper_event(
        {
            "event_type": "fill",
            "event_id": "api-fill-1",
            "symbol": "SOL-USD",
            "side": "BUY",
            "ts": time.time(),
            "price": 99.89,
            "quantity": 10,
            "status": "FILLED",
            "payload": {},
        }
    )
    app = FastAPI()
    app.include_router(create_trading_router())
    client = TestClient(app)
    res = client.get("/api/chart-markers/SOL-USD", params={"timeframe": "5m"})
    assert res.status_code == 200
    body = res.json()
    assert body["count"] >= 1
    assert any(m["event_type"] == "fill" for m in body["markers"])
