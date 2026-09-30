"""Shared FeatureSnapshot — single source of indicator inputs for agents.

All agents in one decision cycle must receive the SAME symbol / timeframe /
candle timestamp / feature values. Displayed chart indicators alone are not
sufficient; this snapshot is what agents use for decisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from ..market_data.models import Candle
from . import calc as ic
from .engine import IndicatorEngine, IndicatorSnapshot, pct_change, short_trend, volume_state, volatility


@dataclass
class FeatureSnapshot:
    """Normalized features for one symbol + analysis timeframe + candle."""

    symbol: str
    timeframe: str
    candle_timestamp: float
    current_price: float

    ema_20: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None

    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None

    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_position: float | None = None
    bb_width: float | None = None

    vwap: float | None = None
    atr_14: float | None = None
    atr_pct: float | None = None

    volume: float | None = None
    volume_avg: float | None = None
    relative_volume: float | None = None
    volume_state: str = "UNKNOWN"
    volume_spike: bool = False

    change_1m_pct: float | None = None
    change_5m_pct: float | None = None
    change_15m_pct: float | None = None
    price_slope_pct: float | None = None  # short momentum window
    realized_volatility: float | None = None
    short_trend: str = "FLAT"

    multi_timeframe: dict[str, Any] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)
    candle_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact_for_ai(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candle_timestamp": self.candle_timestamp,
            "price": round(self.current_price, 6),
            "change_1m_pct": self.change_1m_pct,
            "change_5m_pct": self.change_5m_pct,
            "change_15m_pct": self.change_15m_pct,
            "price_slope_pct": self.price_slope_pct,
            "short_trend": self.short_trend,
            "volume_state": self.volume_state,
            "relative_volume": self.relative_volume,
            "realized_volatility": self.realized_volatility,
            "rsi_14": self.rsi_14,
            "sma_20": self.sma_20,
            "sma_50": self.sma_50,
            "sma_200": self.sma_200,
            "ema_20": self.ema_20,
            "ema_50": self.ema_50,
            "ema_200": self.ema_200,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_hist": self.macd_hist,
            "atr_14": self.atr_14,
            "atr_pct": self.atr_pct,
            "vwap": self.vwap,
            "bollinger_position": self.bb_position,
            "bollinger_width": self.bb_width,
            "data_quality": self.data_quality,
            "multi_timeframe": self.multi_timeframe,
        }


def build_feature_snapshot(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Candle],
    price: float | None = None,
    multi_timeframe: dict[str, Any] | None = None,
    data_quality: dict[str, Any] | None = None,
    momentum_window: int = 5,
) -> FeatureSnapshot:
    """Build FeatureSnapshot from candles using shared IndicatorEngine/calc."""
    candles = list(candles)
    closes = ic.closes(candles)
    px = float(price if price is not None else (closes[-1] if closes else 0.0))
    candle_ts = float(candles[-1].ts) if candles else 0.0

    eng = IndicatorEngine()
    snap: IndicatorSnapshot = eng.compute(symbol, candles, price=px)
    extras = snap.extras or {}

    ema20 = extras.get("ema_20")
    ema50 = extras.get("ema_50")
    sma20 = extras.get("sma_20")
    sma50 = extras.get("sma_50")
    ema200 = ic.last(ic.ema_series(closes, 200)) if len(closes) >= 200 else None
    sma200 = ic.last(ic.sma_series(closes, 200)) if len(closes) >= 200 else None

    macd_s, macd_sig_s, macd_h_s = ic.macd_series(closes)
    macd = ic.last(macd_s)
    macd_sig = ic.last(macd_sig_s)
    macd_hist = ic.last(macd_h_s)

    bb_u = extras.get("bollinger_upper")
    bb_m = extras.get("bollinger_mid")
    bb_l = extras.get("bollinger_lower")
    bb_pos = extras.get("bollinger_position")
    bb_width = None
    if bb_u is not None and bb_l is not None and px > 0:
        bb_width = (float(bb_u) - float(bb_l)) / px * 100.0

    atr = extras.get("atr_14")
    atr_pct = (float(atr) / px * 100.0) if atr is not None and px > 0 else None

    vols = ic.volumes(candles)
    volume = float(vols[-1]) if vols else None
    volume_avg = None
    relative_volume = None
    if len(vols) >= 6:
        avg = sum(vols[-6:-1]) / 5.0
        volume_avg = avg if avg > 0 else None
        if volume_avg and volume is not None:
            relative_volume = volume / volume_avg
    vol_state = volume_state(candles)
    volume_spike = bool(relative_volume is not None and relative_volume >= 1.6) or vol_state == "HIGH"

    slope = None
    if len(closes) >= momentum_window and closes[-momentum_window]:
        slope = (closes[-1] - closes[-momentum_window]) / closes[-momentum_window] * 100.0

    return FeatureSnapshot(
        symbol=symbol.upper(),
        timeframe=timeframe,
        candle_timestamp=candle_ts,
        current_price=px,
        ema_20=_r(ema20),
        ema_50=_r(ema50),
        ema_200=_r(ema200),
        sma_20=_r(sma20),
        sma_50=_r(sma50),
        sma_200=_r(sma200),
        rsi_14=_r(snap.rsi_14),
        macd=_r(macd),
        macd_signal=_r(macd_sig),
        macd_hist=_r(macd_hist),
        bb_upper=_r(bb_u),
        bb_middle=_r(bb_m),
        bb_lower=_r(bb_l),
        bb_position=_r(bb_pos),
        bb_width=_r(bb_width),
        vwap=_r(extras.get("vwap")),
        atr_14=_r(atr),
        atr_pct=_r(atr_pct),
        volume=_r(volume),
        volume_avg=_r(volume_avg),
        relative_volume=_r(relative_volume),
        volume_state=vol_state,
        volume_spike=volume_spike,
        change_1m_pct=_r(snap.change_1m_pct),
        change_5m_pct=_r(snap.change_5m_pct),
        change_15m_pct=_r(snap.change_15m_pct),
        price_slope_pct=_r(slope),
        realized_volatility=_r(snap.volatility),
        short_trend=snap.short_trend or short_trend(closes),
        multi_timeframe=dict(multi_timeframe or {}),
        data_quality=dict(data_quality or {}),
        candle_count=len(candles),
    )


def _r(v: float | None, n: int = 4) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None
