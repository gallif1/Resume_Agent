"""Shared indicator series calculations (no look-ahead bias).

Each index i uses only candles[0..i] inclusive. Same module powers chart + agents.
"""

from __future__ import annotations

from typing import Sequence

from ..market_data.models import Candle


def closes(candles: Sequence[Candle]) -> list[float]:
    return [float(c.close) for c in candles]


def highs(candles: Sequence[Candle]) -> list[float]:
    return [float(c.high) for c in candles]


def lows(candles: Sequence[Candle]) -> list[float]:
    return [float(c.low) for c in candles]


def volumes(candles: Sequence[Candle]) -> list[float]:
    return [float(c.volume) for c in candles]


def sma_series(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    if window <= 0:
        return [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= window:
            s -= values[i - window]
        if i + 1 >= window:
            out.append(s / window)
        else:
            out.append(None)
    return out


def ema_series(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    if window <= 0 or not values:
        return [None] * len(values)
    k = 2 / (window + 1)
    e: float | None = None
    for i, v in enumerate(values):
        if i + 1 < window:
            out.append(None)
            continue
        if e is None:
            e = sum(values[i + 1 - window : i + 1]) / window
        else:
            e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi_series(values: Sequence[float], window: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < window + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, window + 1):
        d = values[i] - values[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / window
    avg_loss = losses / window
    if avg_loss == 0:
        out[window] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[window] = 100 - (100 / (1 + rs))
    for i in range(window + 1, len(values)):
        d = values[i] - values[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (window - 1) + gain) / window
        avg_loss = (avg_loss * (window - 1) + loss) / window
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100 - (100 / (1 + rs))
    return out


def macd_series(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema_fast = ema_series(values, fast)
    ema_slow = ema_series(values, slow)
    macd: list[float | None] = []
    for a, b in zip(ema_fast, ema_slow):
        if a is None or b is None:
            macd.append(None)
        else:
            macd.append(a - b)
    # Signal EMA over available MACD values (no look-ahead: only past MACD).
    signal_line: list[float | None] = [None] * len(macd)
    hist: list[float | None] = [None] * len(macd)
    k = 2 / (signal + 1)
    e: float | None = None
    buf: list[float] = []
    for i, m in enumerate(macd):
        if m is None:
            continue
        buf.append(m)
        if len(buf) < signal:
            continue
        if e is None:
            e = sum(buf[-signal:]) / signal
        else:
            e = m * k + e * (1 - k)
        signal_line[i] = e
        hist[i] = m - e
    return macd, signal_line, hist


def bollinger_series(
    values: Sequence[float], window: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = sma_series(values, window)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i, m in enumerate(mid):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        chunk = values[i + 1 - window : i + 1]
        mean = m
        var = sum((x - mean) ** 2 for x in chunk) / window
        std = var**0.5
        upper.append(mean + num_std * std)
        lower.append(mean - num_std * std)
    return upper, mid, lower


def atr_series(candles: Sequence[Candle], window: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    if len(candles) < 2:
        return out
    trs: list[float] = [0.0]
    for i in range(1, len(candles)):
        h = candles[i].high
        low = candles[i].low
        prev_c = candles[i - 1].close
        tr = max(h - low, abs(h - prev_c), abs(low - prev_c))
        trs.append(tr)
    if len(trs) < window:
        return out
    atr = sum(trs[1 : window + 1]) / window
    out[window] = atr
    for i in range(window + 1, len(trs)):
        atr = (atr * (window - 1) + trs[i]) / window
        out[i] = atr
    return out


def vwap_series(candles: Sequence[Candle]) -> list[float | None]:
    out: list[float | None] = []
    cum_pv = 0.0
    cum_v = 0.0
    for c in candles:
        typical = (c.high + c.low + c.close) / 3.0
        cum_pv += typical * c.volume
        cum_v += c.volume
        out.append(cum_pv / cum_v if cum_v > 0 else None)
    return out


def last(series: Sequence[float | None]) -> float | None:
    for v in reversed(series):
        if v is not None:
            return float(v)
    return None


def swing_points(
    candles: Sequence[Candle], lookback: int = 3, count: int = 5
) -> tuple[list[dict], list[dict]]:
    """Recent swing highs/lows using only past + current bar (no future bars)."""
    highs_out: list[dict] = []
    lows_out: list[dict] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        # Confirmed swing requires lookback bars on each side — at index i those
        # "future" bars have already occurred relative to later indices. For the
        # latest incomplete region we stop at n-lookback so we never use unseen bars.
        h = candles[i].high
        low = candles[i].low
        if all(h >= candles[j].high for j in range(i - lookback, i + lookback + 1) if j != i):
            highs_out.append({"ts": candles[i].ts, "price": h})
        if all(low <= candles[j].low for j in range(i - lookback, i + lookback + 1) if j != i):
            lows_out.append({"ts": candles[i].ts, "price": low})
    return highs_out[-count:], lows_out[-count:]


def aggregate_candles(candles: Sequence[Candle], bucket_sec: int) -> list[Candle]:
    """Aggregate finer candles into larger buckets (e.g. 1h → 4h)."""
    if bucket_sec <= 0 or not candles:
        return list(candles)
    buckets: dict[int, list[Candle]] = {}
    for c in candles:
        key = int(c.ts) // bucket_sec * bucket_sec
        buckets.setdefault(key, []).append(c)
    out: list[Candle] = []
    for key in sorted(buckets):
        group = buckets[key]
        out.append(
            Candle(
                ts=float(key),
                open=group[0].open,
                high=max(x.high for x in group),
                low=min(x.low for x in group),
                close=group[-1].close,
                volume=sum(x.volume for x in group),
            )
        )
    return out
