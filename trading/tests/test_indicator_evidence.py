"""Phase 3 — indicator evidence + incomplete-bar / look-ahead tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.decision_log import build_market_snapshot
from trading_system.indicators import calc as ic
from trading_system.indicators.evidence import (
    build_decision_evidence,
    build_indicator_snapshot,
    interpret_signals,
)
from trading_system.market_data.models import Candle
from trading_system.market_snapshot import indicator_series_for_chart


def _candles(n=80, start=100.0, step=300):
    out = []
    px = start
    for i in range(n):
        o = px
        c = px + (0.25 if i % 2 == 0 else -0.15)
        out.append(
            Candle(
                ts=1_700_000_000 + i * step,
                open=o,
                high=max(o, c) + 0.4,
                low=min(o, c) - 0.4,
                close=c,
                volume=1000 + (i % 7) * 200,
                complete=True,
            )
        )
        px = c
    return out


def test_build_indicator_snapshot_fields():
    candles = _candles()
    snap = build_indicator_snapshot(candles, symbol="AAPL", price=candles[-1].close)
    for key in (
        "price",
        "ema_20",
        "ema_50",
        "rsi_14",
        "macd",
        "macd_signal",
        "relative_volume",
        "atr_14",
        "vwap",
        "nearest_support",
        "nearest_resistance",
    ):
        assert key in snap
    assert snap["ema_20"] is not None
    assert snap["rsi_14"] is not None
    assert snap["atr_14"] is not None


def test_interpret_signals_hebrew():
    snap = build_indicator_snapshot(_candles(), symbol="NVDA")
    signals = interpret_signals(snap)
    assert isinstance(signals, list)
    assert signals
    for s in signals:
        assert "signal" in s and "direction" in s and "strength" in s
        assert "explanation" in s
        # User-facing explanations are Hebrew (contain Hebrew letters)
        assert any("\u0590" <= ch <= "\u05FF" for ch in s["explanation"])


def test_build_decision_evidence_bundle():
    ev = build_decision_evidence(candles=_candles(), symbol="BTC-USD")
    assert "indicators_used" in ev
    assert "interpreted_signals" in ev
    assert ev["indicators_used"]["rsi_14"] is not None


def test_incomplete_candle_dropped_for_evidence():
    candles = _candles(40)
    candles[-1].complete = False
    candles[-1].close = candles[-2].close * 5  # would skew indicators if included
    snap_drop = build_indicator_snapshot(candles, drop_incomplete=True)
    snap_keep = build_indicator_snapshot(candles, drop_incomplete=False)
    assert snap_drop["used_incomplete_last_bar"] is True
    assert snap_drop["bar_count"] == 39
    # Dropping incomplete bar must change price vs keeping it
    assert snap_drop["price"] != snap_keep["price"]


def test_no_lookahead_ema_rsi_for_evidence():
    candles = _candles(50)
    closes = ic.closes(candles)
    ema = ic.ema_series(closes, 20)
    rsi = ic.rsi_series(closes, 14)
    # Mutate last close — earlier series values must stay identical
    closes2 = list(closes)
    closes2[-1] *= 3
    ema2 = ic.ema_series(closes2, 20)
    rsi2 = ic.rsi_series(closes2, 14)
    assert ema[30] == ema2[30]
    assert rsi[30] == rsi2[30]
    # Evidence mid-series snapshot using prefix only
    mid = candles[:40]
    snap = build_indicator_snapshot(mid, symbol="AAPL")
    # Recompute with future bars appended — past-derived fields for same prefix length
    # should not use future: last RSI on prefix equals rsi series at index 39
    assert snap["rsi_14"] is not None
    assert abs(snap["rsi_14"] - float(rsi[39])) < 1e-3


def test_decision_log_market_snapshot_includes_evidence():
    candles = _candles(60)
    from trading_system.indicators.engine import IndicatorEngine

    ind = IndicatorEngine().compute("AAPL", candles, price=candles[-1].close)
    market = build_market_snapshot(
        symbol="AAPL",
        price=candles[-1].close,
        ts=candles[-1].ts,
        volume=candles[-1].volume,
        indicator=ind.to_dict(),
        events=[],
        quote_meta={"provider": "yahoo", "session": "closed", "freshness": "stale"},
        candles=candles,
    )
    assert market.get("indicators_used")
    assert market.get("interpreted_signals")
    assert "atr_14" in market["indicators_used"]


def test_chart_indicator_series_includes_atr():
    series = indicator_series_for_chart(_candles(80))
    assert "atr_14" in series
    assert len(series["atr_14"]) > 0
