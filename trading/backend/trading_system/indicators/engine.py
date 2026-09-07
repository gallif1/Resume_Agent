"""Local technical indicators from candle closes (extensible)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from ..market_data.models import Candle


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
        }


def _closes(candles: Sequence[Candle]) -> list[float]:
    return [c.close for c in candles]


def sma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window or window <= 0:
        return None
    chunk = values[-window:]
    return sum(chunk) / window


def ema(values: Sequence[float], window: int) -> float | None:
    if len(values) < window or window <= 0:
        return None
    k = 2 / (window + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: Sequence[float], window: int = 14) -> float | None:
    if len(values) < window + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-window, 0):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses += -delta
    avg_gain = gains / window
    avg_loss = losses / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


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
    """Compute indicator snapshots; ready for MACD/Bollinger later via extras."""

    def compute(self, symbol: str, candles: Sequence[Candle], price: float | None = None) -> IndicatorSnapshot:
        closes = _closes(candles)
        px = float(price if price is not None else (closes[-1] if closes else 0.0))
        return IndicatorSnapshot(
            symbol=symbol,
            price=px,
            change_1m_pct=_round(pct_change(closes, 1)),
            change_5m_pct=_round(pct_change(closes, 5)),
            change_15m_pct=_round(pct_change(closes, 15)),
            change_1h_pct=_round(pct_change(closes, 60)),
            sma_fast=_round(sma(closes, 10)),
            sma_slow=_round(sma(closes, 30)),
            ema_fast=_round(ema(closes, 12)),
            rsi_14=_round(rsi(closes, 14)),
            volatility=_round(volatility(closes, 20)),
            volume_state=volume_state(candles),
            short_trend=short_trend(closes),
            extras={},
        )


def _round(v: float | None, n: int = 4) -> float | None:
    return None if v is None else round(float(v), n)
