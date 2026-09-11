"""Local technical indicators from candles (shared calc — no look-ahead)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from ..market_data.models import Candle
from . import calc as ic


@dataclass
class IndicatorSnapshot:
    symbol: str
    price: float
    change_1m_pct: float | None
    change_5m_pct: float | None
    change_15m_pct: float | None
    change_1h_pct: float | None
    sma_fast: float | None
    sma_slow: float | None
    ema_fast: float | None
    rsi_14: float | None
    volatility: float | None
    volume_state: str
    short_trend: str
    extras: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact_for_ai(self) -> dict[str, Any]:
        """Tiny payload for LLM — no candle arrays."""
        return {
            "symbol": self.symbol,
            "price": round(self.price, 6),
            "change_1m_pct": self.change_1m_pct,
            "change_5m_pct": self.change_5m_pct,
            "change_15m_pct": self.change_15m_pct,
            "short_trend": self.short_trend,
            "volume_state": self.volume_state,
            "volatility": self.volatility,
            "rsi_14": self.rsi_14,
            "sma_fast": self.sma_fast,
            "sma_slow": self.sma_slow,
            "sma_20": self.extras.get("sma_20"),
            "sma_50": self.extras.get("sma_50"),
            "ema_20": self.extras.get("ema_20"),
            "ema_50": self.extras.get("ema_50"),
            "macd": self.extras.get("macd"),
            "macd_signal": self.extras.get("macd_signal"),
            "atr_14": self.extras.get("atr_14"),
            "vwap": self.extras.get("vwap"),
            "bollinger_position": self.extras.get("bollinger_position"),
        }


# Re-export primitives used by older tests
sma = lambda values, window: ic.last(ic.sma_series(values, window))  # noqa: E731
ema = lambda values, window: ic.last(ic.ema_series(values, window))  # noqa: E731


def rsi(values: Sequence[float], window: int = 14) -> float | None:
    return ic.last(ic.rsi_series(list(values), window))


def pct_change(values: Sequence[float], lookback: int) -> float | None:
    if len(values) <= lookback:
        return None
    prev = values[-(lookback + 1)]
    if prev == 0:
        return None
    return (values[-1] - prev) / prev * 100


def volatility(values: Sequence[float], window: int = 20) -> float | None:
    if len(values) < max(3, window // 2):
        return None
    chunk = values[-window:] if len(values) >= window else list(values)
    if len(chunk) < 2:
        return None
    rets = [(chunk[i] - chunk[i - 1]) / chunk[i - 1] for i in range(1, len(chunk)) if chunk[i - 1]]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return (var ** 0.5) * 100


def short_trend(values: Sequence[float]) -> str:
    if len(values) < 5:
        return "FLAT"
    slope = (values[-1] - values[-5]) / values[-5] * 100 if values[-5] else 0.0
    if slope > 0.15:
        return "UP"
    if slope < -0.15:
        return "DOWN"
    return "FLAT"


def volume_state(candles: Sequence[Candle]) -> str:
    if len(candles) < 6:
        return "UNKNOWN"
    vols = [c.volume for c in candles]
    recent = vols[-1]
    avg = sum(vols[-6:-1]) / 5
    if avg <= 0:
        return "UNKNOWN"
    if recent >= avg * 1.6:
        return "HIGH"
    if recent <= avg * 0.6:
        return "LOW"
    return "NORMAL"


class IndicatorEngine:
    """Compute indicator snapshots from shared series (no look-ahead)."""

    def compute(self, symbol: str, candles: Sequence[Candle], price: float | None = None) -> IndicatorSnapshot:
        closes = ic.closes(candles)
        px = float(price if price is not None else (closes[-1] if closes else 0.0))
        sma20 = ic.last(ic.sma_series(closes, 20))
        sma50 = ic.last(ic.sma_series(closes, 50))
        ema20 = ic.last(ic.ema_series(closes, 20))
        ema50 = ic.last(ic.ema_series(closes, 50))
        macd, macd_sig, _ = ic.macd_series(closes)
        bb_u, bb_m, bb_l = ic.bollinger_series(closes)
        atr = ic.last(ic.atr_series(candles, 14))
        vwap = ic.last(ic.vwap_series(candles))
        bb_pos = None
        if bb_u and bb_u[-1] is not None and bb_l and bb_l[-1] is not None and bb_u[-1] != bb_l[-1]:
            bb_pos = (px - bb_l[-1]) / (bb_u[-1] - bb_l[-1])

        return IndicatorSnapshot(
            symbol=symbol,
            price=px,
            change_1m_pct=_round(pct_change(closes, 1)),
            change_5m_pct=_round(pct_change(closes, 5)),
            change_15m_pct=_round(pct_change(closes, 15)),
            change_1h_pct=_round(pct_change(closes, 60)),
            sma_fast=_round(ic.last(ic.sma_series(closes, 10))),
            sma_slow=_round(ic.last(ic.sma_series(closes, 30))),
            ema_fast=_round(ic.last(ic.ema_series(closes, 12))),
            rsi_14=_round(rsi(closes, 14)),
            volatility=_round(volatility(closes, 20)),
            volume_state=volume_state(candles),
            short_trend=short_trend(closes),
            extras={
                "sma_20": _round(sma20),
                "sma_50": _round(sma50),
                "ema_20": _round(ema20),
                "ema_50": _round(ema50),
                "macd": _round(ic.last(macd)),
                "macd_signal": _round(ic.last(macd_sig)),
                "atr_14": _round(atr),
                "vwap": _round(vwap),
                "bollinger_position": _round(bb_pos),
                "bollinger_upper": _round(bb_u[-1] if bb_u else None),
                "bollinger_mid": _round(bb_m[-1] if bb_m else None),
                "bollinger_lower": _round(bb_l[-1] if bb_l else None),
            },
        )


def _round(v: float | None, n: int = 4) -> float | None:
    return None if v is None else round(float(v), n)
