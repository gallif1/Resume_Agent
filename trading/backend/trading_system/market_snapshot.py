"""Build compact MarketSnapshot for agents and UI (deterministic, no LLM)."""

from __future__ import annotations

import time
from typing import Any, Sequence

from .indicators import calc as ic
from .indicators.engine import IndicatorEngine
from .market_data.models import AssetClass, Candle, DataFreshness, Quote


def build_market_snapshot(
    *,
    symbol: str,
    quote: Quote | None,
    candles_by_tf: dict[str, Sequence[Candle]],
    human_annotations: list[dict[str, Any]] | None = None,
    portfolio: dict[str, Any] | None = None,
    cooldown_remaining_sec: float | None = None,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Normalized snapshot — compact for agents; no thousands of ticks."""
    primary_tf = "5m" if candles_by_tf.get("5m") else next(iter(candles_by_tf), "5m")
    candles = list(candles_by_tf.get(primary_tf) or [])
    price = float(quote.price) if quote and quote.price > 0 else (candles[-1].close if candles else 0.0)
    now = time.time()
    last_ts = float(quote.ts) if quote else (candles[-1].ts if candles else now)
    age = max(0.0, now - last_ts)

    ind = IndicatorEngine().compute(symbol, candles, price=price)
    closes = ic.closes(candles)
    sma20 = ic.last(ic.sma_series(closes, 20))
    sma50 = ic.last(ic.sma_series(closes, 50))
    ema20 = ic.last(ic.ema_series(closes, 20))
    ema50 = ic.last(ic.ema_series(closes, 50))
    macd, macd_sig, _ = ic.macd_series(closes)
    bb_u, bb_m, bb_l = ic.bollinger_series(closes, 20)
    atr = ic.last(ic.atr_series(candles, 14))
    vwap = ic.last(ic.vwap_series(candles))
    swing_h, swing_l = ic.swing_points(candles)

    bb_pos = None
    if bb_u[-1] is not None and bb_l[-1] is not None and bb_u[-1] != bb_l[-1]:
        bb_pos = (price - bb_l[-1]) / (bb_u[-1] - bb_l[-1])

    # Multi-timeframe trends from available TF closes.
    trends: dict[str, str] = {}
    for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
        series = list(candles_by_tf.get(tf) or [])
        trends[tf] = _trend(ic.closes(series)) if series else "UNKNOWN"

    # Price changes across horizons using primary series timestamps.
    changes = {
        "1m": _change_since(candles, price, 60),
        "5m": _change_since(candles, price, 300),
        "15m": _change_since(candles, price, 900),
        "1h": _change_since(candles, price, 3600),
        "4h": _change_since(candles, price, 14400),
        "24h": _change_since(candles, price, 86400),
    }

    latest = candles[-1].to_dict() if candles else None
    recent = [c.to_dict() for c in candles[-40:]]

    vols = ic.volumes(candles)
    rel_vol = None
    if len(vols) >= 6 and sum(vols[-6:-1]) > 0:
        rel_vol = vols[-1] / (sum(vols[-6:-1]) / 5)

    support_candidates = [{"price": x["price"], "ts": x["ts"], "source": "auto"} for x in swing_l]
    resistance_candidates = [{"price": x["price"], "ts": x["ts"], "source": "auto"} for x in swing_h]

    human_levels = []
    for a in human_annotations or []:
        if a.get("annotation_type") in {"SUPPORT", "RESISTANCE"} and a.get("active", True):
            human_levels.append(
                {
                    "type": a.get("annotation_type"),
                    "price": a.get("price"),
                    "importance": a.get("importance") or "medium",
                    "note": a.get("note") or "",
                    "label": a.get("label") or "",
                    "source": "human",
                }
            )

    dist_high = None
    dist_low = None
    if swing_h:
        dist_high = (price - swing_h[-1]["price"]) / swing_h[-1]["price"] * 100
    if swing_l:
        dist_low = (price - swing_l[-1]["price"]) / swing_l[-1]["price"] * 100

    missing: list[str] = []
    if not candles:
        missing.append("candles")
    if quote is None:
        missing.append("quote")
    if quote and quote.freshness == DataFreshness.UNAVAILABLE:
        missing.append("live_quote")

    freshness = (quote.freshness.value if quote else "unavailable")
    stale = freshness in {"stale", "unavailable"} or age > 120 or bool(missing)

    bid = ask = spread = spread_pct = None
    # Microstructure only when quote carries bid/ask (providers may omit).
    if quote and hasattr(quote, "bid"):
        bid = getattr(quote, "bid", None)
        ask = getattr(quote, "ask", None)
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > 0 and ask > 0:
        spread = ask - bid
        spread_pct = spread / price * 100 if price else None
    else:
        missing.append("order_book")

    snap_conf = 0.9
    if stale:
        snap_conf -= 0.35
    if missing:
        snap_conf -= 0.05 * len(missing)
    snap_conf = max(0.1, min(0.95, snap_conf))

    asset = quote.asset_class.value if quote else (
        "crypto" if "-" in symbol else "stock"
    )
    provider = provider_name or (quote.provider if quote else "unknown")

    return {
        "symbol": symbol,
        "asset_type": asset,
        "source": provider,
        "timestamp": now,
        "current_price": price,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "spread_pct": spread_pct,
        "session_status": quote.session.value if quote else "unknown",
        "data_freshness": freshness,
        "ohlcv": {
            "latest_candle": latest,
            "recent_candles": recent,
            "volume": vols[-1] if vols else None,
            "relative_volume": round(rel_vol, 4) if rel_vol is not None else None,
            "price_change_pct": changes,
        },
        "multi_timeframe": {
            "1m_trend": trends.get("1m"),
            "5m_trend": trends.get("5m"),
            "15m_trend": trends.get("15m"),
            "1h_trend": trends.get("1h"),
            "4h_trend": trends.get("4h"),
            "1d_trend": trends.get("1d"),
        },
        "indicators": {
            "sma_20": _r(sma20),
            "sma_50": _r(sma50),
            "ema_20": _r(ema20),
            "ema_50": _r(ema50),
            "rsi_14": ind.rsi_14,
            "macd": _r(ic.last(macd)),
            "macd_signal": _r(ic.last(macd_sig)),
            "bollinger_position": _r(bb_pos),
            "bollinger_upper": _r(bb_u[-1] if bb_u else None),
            "bollinger_mid": _r(bb_m[-1] if bb_m else None),
            "bollinger_lower": _r(bb_l[-1] if bb_l else None),
            "atr_14": _r(atr),
            "vwap": _r(vwap),
            "realized_volatility": ind.volatility,
            "volume_state": ind.volume_state,
            "short_trend": ind.short_trend,
        },
        "market_structure": {
            "swing_highs": swing_h,
            "swing_lows": swing_l,
            "distance_from_recent_high_pct": _r(dist_high),
            "distance_from_recent_low_pct": _r(dist_low),
            "support_candidates": support_candidates,
            "resistance_candidates": resistance_candidates,
            "human_levels": human_levels,
            "breakout_or_rejection": _breakout_status(price, swing_h, swing_l),
        },
        "microstructure": {
            "supported": "order_book" not in missing and bid is not None,
            "best_bid": bid,
            "best_ask": ask,
            "order_book_imbalance": None,
            "top_bid_depth": None,
            "top_ask_depth": None,
            "recent_buy_volume": None,
            "recent_sell_volume": None,
            "buy_sell_pressure": None,
            "liquidity_warning": None,
            "note": "Order-book fields populated only when provider supports them",
        },
        "portfolio_risk": portfolio or {},
        "cooldown_remaining_sec": cooldown_remaining_sec,
        "data_quality": {
            "source": provider,
            "last_update_time": last_ts,
            "age_seconds": round(age, 1),
            "missing_fields": missing,
            "stale": stale,
            "snapshot_confidence": round(snap_conf, 3),
        },
        "legacy_indicator_snapshot": ind.compact_for_ai(),
    }


def _trend(values: list[float]) -> str:
    if len(values) < 5:
        return "FLAT"
    slope = (values[-1] - values[-5]) / values[-5] * 100 if values[-5] else 0.0
    if slope > 0.15:
        return "UP"
    if slope < -0.15:
        return "DOWN"
    return "FLAT"


def _change_since(candles: Sequence[Candle], price: float, seconds: float) -> float | None:
    if not candles or price <= 0:
        return None
    target = candles[-1].ts - seconds
    prev = None
    for c in candles:
        if c.ts <= target:
            prev = c.close
        else:
            break
    if prev is None or prev == 0:
        return None
    return round((price - prev) / prev * 100, 4)


def _breakout_status(price: float, highs: list[dict], lows: list[dict]) -> str:
    if highs and price > highs[-1]["price"] * 1.001:
        return "breakout_high"
    if lows and price < lows[-1]["price"] * 0.999:
        return "breakdown_low"
    if highs and abs(price - highs[-1]["price"]) / highs[-1]["price"] < 0.002:
        return "rejection_high"
    if lows and abs(price - lows[-1]["price"]) / lows[-1]["price"] < 0.002:
        return "rejection_low"
    return "none"


def _r(v: float | None, n: int = 4) -> float | None:
    return None if v is None else round(float(v), n)


def indicator_series_for_chart(candles: Sequence[Candle]) -> dict[str, Any]:
    """Full series for chart overlays — still no look-ahead."""
    c = list(candles)
    cl = ic.closes(c)
    ts = [int(x.ts) for x in c]
    macd, sig, hist = ic.macd_series(cl)
    bb_u, bb_m, bb_l = ic.bollinger_series(cl)

    def pack(series: list[float | None]) -> list[dict[str, float]]:
        return [{"time": t, "value": float(v)} for t, v in zip(ts, series) if v is not None]

    return {
        "sma_20": pack(ic.sma_series(cl, 20)),
        "sma_50": pack(ic.sma_series(cl, 50)),
        "ema_20": pack(ic.ema_series(cl, 20)),
        "ema_50": pack(ic.ema_series(cl, 50)),
        "rsi_14": pack(ic.rsi_series(cl, 14)),
        "macd": pack(macd),
        "macd_signal": pack(sig),
        "macd_hist": pack(hist),
        "bb_upper": pack(bb_u),
        "bb_mid": pack(bb_m),
        "bb_lower": pack(bb_l),
        "atr_14": pack(ic.atr_series(c, 14)),
        "vwap": pack(ic.vwap_series(c)),
        "volume": [{"time": int(x.ts), "value": float(x.volume), "color": "#3dd6c688" if x.close >= x.open else "#e85d5d88"} for x in c],
    }
