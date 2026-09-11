"""Tests for candlestick upgrade: indicators, annotations, markers, aggregation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.indicators import calc as ic
from trading_system.indicators.engine import IndicatorEngine
from trading_system.market_data.candle_store import MarketDB, validate_annotation_payload
from trading_system.market_data.models import Candle
from trading_system.market_snapshot import build_market_snapshot, indicator_series_for_chart


def _candles(n=60, start=100.0):
    out = []
    px = start
    for i in range(n):
        o = px
        c = px + (0.2 if i % 3 else -0.1)
        out.append(
            Candle(
                ts=1_700_000_000 + i * 300,
                open=o,
                high=max(o, c) + 0.3,
                low=min(o, c) - 0.3,
                close=c,
                volume=1000 + i,
            )
        )
        px = c
    return out


def test_no_lookahead_sma_uses_only_past():
    candles = _candles(30)
    closes = ic.closes(candles)
    series = ic.sma_series(closes, 5)
    # Index 4 is first defined; value equals mean of closes[0:5]
    assert abs(float(series[4]) - (sum(closes[:5]) / 5)) < 1e-9
    # Changing a future close must not affect earlier SMA
    closes2 = list(closes)
    closes2[-1] = closes2[-1] * 10
    series2 = ic.sma_series(closes2, 5)
    assert series2[4] == series[4]
    assert series2[10] == series[10]


def test_rsi_macd_bollinger_atr_vwap():
    candles = _candles(80)
    closes = ic.closes(candles)
    assert ic.last(ic.rsi_series(closes, 14)) is not None
    macd, sig, hist = ic.macd_series(closes)
    assert ic.last(macd) is not None
    assert ic.last(sig) is not None
    u, m, l = ic.bollinger_series(closes)
    assert u[-1] is not None and l[-1] is not None and u[-1] >= l[-1]
    assert ic.last(ic.atr_series(candles, 14)) is not None
    assert ic.last(ic.vwap_series(candles)) is not None


def test_aggregate_1h_to_4h():
    # Align to exact 4h bucket boundary
    base = 1_700_000_000
    base = base - (base % (4 * 3600))
    hourly = []
    px = 200.0
    for i in range(16):
        o = px
        c = px + 0.1
        hourly.append(
            Candle(
                ts=float(base + i * 3600),
                open=o,
                high=c + 0.2,
                low=o - 0.2,
                close=c,
                volume=1000,
            )
        )
        px = c
    agg = ic.aggregate_candles(hourly, 4 * 3600)
    assert len(agg) == 4
    assert agg[0].open == hourly[0].open
    assert agg[0].close == hourly[3].close
    assert abs(agg[0].high - max(x.high for x in hourly[:4])) < 1e-9


def test_candle_store_dedupes(tmp_path):
    db = MarketDB(path=tmp_path / "m.db")
    c1 = Candle(ts=100, open=1, high=2, low=0.5, close=1.5, volume=10)
    c2 = Candle(ts=100, open=1.1, high=2.1, low=0.6, close=1.6, volume=12)
    db.upsert_candles("BTC-USD", "5m", [c1])
    db.upsert_candles("BTC-USD", "5m", [c2])
    rows = db.get_candles("BTC-USD", "5m", limit=10)
    assert len(rows) == 1
    assert rows[0].close == 1.6


def test_annotation_crud_and_validation(tmp_path):
    db = MarketDB(path=tmp_path / "m.db")
    row = db.create_annotation(
        {
            "symbol": "AAPL",
            "annotation_type": "RESISTANCE",
            "price": 200.5,
            "label": "Reject zone",
            "importance": "high",
            "note": "User marked repeated rejection",
        }
    )
    assert row["id"]
    listed = db.list_annotations("AAPL")
    assert len(listed) == 1
    updated = db.update_annotation(row["id"], {"label": "R1", "price": 201})
    assert updated["label"] == "R1"
    assert db.delete_annotation(row["id"]) is True
    assert db.list_annotations("AAPL") == []

    try:
        validate_annotation_payload(
            {"symbol": "AAPL", "annotation_type": "SUPPORT", "note": "<script>x</script>"},
            partial=False,
        )
        assert False, "should reject script"
    except ValueError:
        pass


def test_market_snapshot_contains_required_blocks():
    candles = _candles(80)
    snap = build_market_snapshot(
        symbol="BTC-USD",
        quote=None,
        candles_by_tf={"5m": candles, "1h": candles[::3]},
        human_annotations=[
            {
                "annotation_type": "RESISTANCE",
                "price": 120,
                "importance": "high",
                "note": "manual",
                "active": True,
            }
        ],
        portfolio={"available_cash": 100000},
        provider_name="coinbase",
    )
    assert snap["symbol"] == "BTC-USD"
    assert "indicators" in snap and "rsi_14" in snap["indicators"]
    assert "multi_timeframe" in snap
    assert "market_structure" in snap
    assert snap["market_structure"]["human_levels"][0]["source"] == "human"
    assert "data_quality" in snap
    assert snap["source"] == "coinbase"
    series = indicator_series_for_chart(candles)
    assert "sma_20" in series and "volume" in series


def test_indicator_engine_extras():
    eng = IndicatorEngine()
    snap = eng.compute("ETH-USD", _candles(60))
    assert snap.extras.get("sma_20") is not None or len(_candles(60)) < 20
    compact = snap.compact_for_ai()
    assert "rsi_14" in compact


def test_group_markers_logic():
    # Frontend helper mirrored for sanity via simple python equivalent
    def bucket(ts, secs=300):
        return int(ts // secs * secs)

    items = [
        {"ts": 1000, "kind": "vote_buy"},
        {"ts": 1001, "kind": "fill_buy"},
        {"ts": 2000, "kind": "vote_sell"},
    ]
    groups = {}
    for it in items:
        b = bucket(it["ts"])
        groups.setdefault(b, []).append(it)
    assert len(groups[bucket(1000)]) == 2
    assert len(groups[bucket(2000)]) == 1


def test_paper_trading_flag_still_set():
    from trading_system.runtime import TradingRuntime

    rt = TradingRuntime()
    assert rt.snapshot().get("paper_trading_only") is True
