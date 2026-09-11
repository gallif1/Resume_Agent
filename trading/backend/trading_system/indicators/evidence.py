"""Compact indicator evidence for UnifiedDecision / decision logs.

Shared helpers — import from here to avoid mid-flight edit conflicts on
runtime / unified_decision modules.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..market_data.models import Candle
from . import calc as ic
from .engine import IndicatorEngine, IndicatorSnapshot


def _r(v: float | None, n: int = 4) -> float | None:
    return None if v is None else round(float(v), n)


def _complete_candles(
    candles: Sequence[Candle],
    *,
    drop_incomplete: bool = True,
) -> list[Candle]:
    """Exclude the forming bar when flagged incomplete (no look-ahead)."""
    rows = list(candles)
    if not rows:
        return rows
    if drop_incomplete and hasattr(rows[-1], "complete") and rows[-1].complete is False:
        return rows[:-1] if len(rows) > 1 else rows
    return rows


def build_indicator_snapshot(
    candles: Sequence[Candle] | None = None,
    *,
    indicator: IndicatorSnapshot | dict[str, Any] | None = None,
    price: float | None = None,
    symbol: str = "",
    drop_incomplete: bool = True,
) -> dict[str, Any]:
    """Compact indicator snapshot for decisions (no candle arrays)."""
    rows = _complete_candles(candles or [], drop_incomplete=drop_incomplete)
    snap: IndicatorSnapshot | None = None
    extras: dict[str, Any] = {}

    if isinstance(indicator, IndicatorSnapshot):
        snap = indicator
        extras = dict(snap.extras or {})
        symbol = symbol or snap.symbol
        price = price if price is not None else snap.price
    elif isinstance(indicator, dict):
        extras = dict(indicator.get("extras") or {})
        # Flatten common keys that may already be top-level.
        for k in (
            "sma_20",
            "sma_50",
            "ema_20",
            "ema_50",
            "macd",
            "macd_signal",
            "atr_14",
            "vwap",
            "rsi_14",
            "price",
            "short_trend",
            "volume_state",
        ):
            if k in indicator and indicator[k] is not None and k not in extras:
                extras[k] = indicator[k]
        symbol = symbol or str(indicator.get("symbol") or "")
        if price is None:
            price = indicator.get("price")

    if rows and (snap is None or not extras.get("ema_20")):
        computed = IndicatorEngine().compute(
            symbol or (snap.symbol if snap else ""),
            rows,
            price=price,
        )
        snap = computed
        extras = dict(computed.extras or {})
        price = computed.price
        symbol = computed.symbol

    closes = ic.closes(rows) if rows else []
    vols = ic.volumes(rows) if rows else []
    rel_vol = None
    if len(vols) >= 6 and sum(vols[-6:-1]) > 0:
        rel_vol = vols[-1] / (sum(vols[-6:-1]) / 5)

    swing_h, swing_l = ic.swing_points(rows) if rows else ([], [])
    px = float(price if price is not None else (closes[-1] if closes else 0.0))

    nearest_support = swing_l[-1]["price"] if swing_l else None
    nearest_resistance = swing_h[-1]["price"] if swing_h else None
    # Prefer levels at or below/above price when available.
    below = [x["price"] for x in swing_l if x["price"] <= px]
    above = [x["price"] for x in swing_h if x["price"] >= px]
    if below:
        nearest_support = max(below)
    if above:
        nearest_resistance = min(above)

    rsi_val = _r(extras.get("rsi_14") if extras.get("rsi_14") is not None else (snap.rsi_14 if snap else None))
    return {
        "symbol": symbol,
        "price": _r(px, 6),
        "ema_20": _r(extras.get("ema_20")),
        "ema_50": _r(extras.get("ema_50")),
        "rsi_14": rsi_val,
        "macd": _r(extras.get("macd")),
        "macd_signal": _r(extras.get("macd_signal")),
        "relative_volume": _r(rel_vol),
        "atr_14": _r(extras.get("atr_14")),
        "vwap": _r(extras.get("vwap")),
        "nearest_support": _r(nearest_support, 6),
        "nearest_resistance": _r(nearest_resistance, 6),
        "sma_20": _r(extras.get("sma_20")),
        "sma_50": _r(extras.get("sma_50")),
        "short_trend": (snap.short_trend if snap else extras.get("short_trend")),
        "volume_state": (snap.volume_state if snap else extras.get("volume_state")),
        "used_incomplete_last_bar": bool(
            candles
            and len(list(candles)) > len(rows)
        ),
        "bar_count": len(rows),
    }


def interpret_signals(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Human-facing signal list (Hebrew explanations)."""
    signals: list[dict[str, Any]] = []
    price = snapshot.get("price")
    ema20 = snapshot.get("ema_20")
    ema50 = snapshot.get("ema_50")
    rsi = snapshot.get("rsi_14")
    macd = snapshot.get("macd")
    macd_sig = snapshot.get("macd_signal")
    rel_vol = snapshot.get("relative_volume")
    vwap = snapshot.get("vwap")
    support = snapshot.get("nearest_support")
    resistance = snapshot.get("nearest_resistance")
    trend = snapshot.get("short_trend")

    if ema20 is not None and ema50 is not None:
        if ema20 > ema50:
            signals.append(
                {
                    "signal": "ema_stack",
                    "direction": "bullish",
                    "strength": min(1.0, abs(ema20 - ema50) / max(abs(ema50), 1e-9) * 50),
                    "explanation": "EMA20 מעל EMA50 — מומנטום עולה",
                }
            )
        elif ema20 < ema50:
            signals.append(
                {
                    "signal": "ema_stack",
                    "direction": "bearish",
                    "strength": min(1.0, abs(ema20 - ema50) / max(abs(ema50), 1e-9) * 50),
                    "explanation": "EMA20 מתחת ל-EMA50 — מומנטום יורד",
                }
            )

    if rsi is not None:
        if rsi >= 70:
            signals.append(
                {
                    "signal": "rsi_14",
                    "direction": "bearish",
                    "strength": min(1.0, (rsi - 70) / 30),
                    "explanation": f"RSI גבוה ({rsi:.1f}) — אזור קנייה יתר / לחץ מכירות אפשרי",
                }
            )
        elif rsi <= 30:
            signals.append(
                {
                    "signal": "rsi_14",
                    "direction": "bullish",
                    "strength": min(1.0, (30 - rsi) / 30),
                    "explanation": f"RSI נמוך ({rsi:.1f}) — אזור מכירת יתר / לחץ קניות אפשרי",
                }
            )
        else:
            signals.append(
                {
                    "signal": "rsi_14",
                    "direction": "neutral",
                    "strength": 0.3,
                    "explanation": f"RSI ניטרלי ({rsi:.1f})",
                }
            )

    if macd is not None and macd_sig is not None:
        if macd > macd_sig:
            signals.append(
                {
                    "signal": "macd",
                    "direction": "bullish",
                    "strength": min(1.0, abs(macd - macd_sig) / max(abs(macd_sig), 1e-6) * 5),
                    "explanation": "MACD מעל קו האות — מומנטום חיובי",
                }
            )
        elif macd < macd_sig:
            signals.append(
                {
                    "signal": "macd",
                    "direction": "bearish",
                    "strength": min(1.0, abs(macd - macd_sig) / max(abs(macd_sig), 1e-6) * 5),
                    "explanation": "MACD מתחת לקו האות — מומנטום שלילי",
                }
            )

    if rel_vol is not None:
        if rel_vol >= 1.6:
            signals.append(
                {
                    "signal": "relative_volume",
                    "direction": "bullish" if (trend == "UP") else ("bearish" if trend == "DOWN" else "neutral"),
                    "strength": min(1.0, (rel_vol - 1.0) / 2),
                    "explanation": f"נפח יחסי גבוה ({rel_vol:.2f}×) — אישור מהלך אפשרי",
                }
            )
        elif rel_vol <= 0.6:
            signals.append(
                {
                    "signal": "relative_volume",
                    "direction": "neutral",
                    "strength": 0.4,
                    "explanation": f"נפח יחסי נמוך ({rel_vol:.2f}×) — מהלך חלש",
                }
            )

    if price is not None and vwap is not None and vwap > 0:
        dist = (price - vwap) / vwap * 100
        if dist > 0.15:
            signals.append(
                {
                    "signal": "vwap",
                    "direction": "bullish",
                    "strength": min(1.0, abs(dist) / 2),
                    "explanation": f"מחיר מעל VWAP ({dist:+.2f}%)",
                }
            )
        elif dist < -0.15:
            signals.append(
                {
                    "signal": "vwap",
                    "direction": "bearish",
                    "strength": min(1.0, abs(dist) / 2),
                    "explanation": f"מחיר מתחת ל-VWAP ({dist:+.2f}%)",
                }
            )

    if price is not None and support is not None and support > 0:
        dist_s = (price - support) / support * 100
        if 0 <= dist_s <= 1.0:
            signals.append(
                {
                    "signal": "support",
                    "direction": "bullish",
                    "strength": max(0.3, 1.0 - dist_s),
                    "explanation": f"קרוב לתמיכה ({support:.4g}, {dist_s:.2f}% מעל)",
                }
            )
    if price is not None and resistance is not None and resistance > 0:
        dist_r = (resistance - price) / resistance * 100
        if 0 <= dist_r <= 1.0:
            signals.append(
                {
                    "signal": "resistance",
                    "direction": "bearish",
                    "strength": max(0.3, 1.0 - dist_r),
                    "explanation": f"קרוב להתנגדות ({resistance:.4g}, {dist_r:.2f}% מתחת)",
                }
            )

    if trend in {"UP", "DOWN", "FLAT"}:
        signals.append(
            {
                "signal": "short_trend",
                "direction": {"UP": "bullish", "DOWN": "bearish", "FLAT": "neutral"}[trend],
                "strength": 0.5 if trend != "FLAT" else 0.25,
                "explanation": {
                    "UP": "מגמה קצרה עולה",
                    "DOWN": "מגמה קצרה יורדת",
                    "FLAT": "מגמה קצרה שטוחה",
                }[trend],
            }
        )

    for s in signals:
        s["strength"] = round(float(s["strength"]), 3)
    return signals


def build_decision_evidence(
    *,
    candles: Sequence[Candle] | None = None,
    indicator: IndicatorSnapshot | dict[str, Any] | None = None,
    price: float | None = None,
    symbol: str = "",
    drop_incomplete: bool = True,
) -> dict[str, Any]:
    """Bundle for UnifiedDecision: indicators_used + interpreted_signals."""
    snapshot = build_indicator_snapshot(
        candles,
        indicator=indicator,
        price=price,
        symbol=symbol,
        drop_incomplete=drop_incomplete,
    )
    signals = interpret_signals(snapshot)
    indicators_used = {
        k: snapshot.get(k)
        for k in (
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
        )
    }
    return {
        "indicators_used": indicators_used,
        "interpreted_signals": signals,
        "indicator_snapshot": snapshot,
    }
