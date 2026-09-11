"""UTC time helpers shared by chart markers and candle bucketing."""

from __future__ import annotations

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def normalize_symbol(symbol: str) -> str:
    """Normalize SOL/USD, SOLUSD, sol-usd → SOL-USD (known patterns)."""
    s = (symbol or "").strip().upper().replace(" ", "")
    if not s:
        return ""
    if "/" in s:
        s = s.replace("/", "-")
    if "-" not in s:
        # SOLUSD → SOL-USD, BTCUSD → BTC-USD, AAPL stays AAPL
        for quote in ("USDT", "USD", "EUR"):
            if s.endswith(quote) and len(s) > len(quote):
                base = s[: -len(quote)]
                if quote == "USDT":
                    return f"{base}-USDT"
                return f"{base}-{quote}"
    return s


def to_unix_seconds(ts: float | int) -> float:
    """Accept seconds or milliseconds; return UTC unix seconds."""
    t = float(ts)
    # Heuristic: ms timestamps are >= ~1e12 (year 2001 in ms)
    if t >= 1_000_000_000_000:
        return t / 1000.0
    return t


def candle_bucket_ts(ts: float | int, timeframe: str) -> int:
    """Map an event time to the open timestamp of its containing candle (UTC)."""
    secs = TIMEFRAME_SECONDS.get(timeframe, 300)
    t = int(to_unix_seconds(ts))
    return (t // secs) * secs


def marker_key(event_type: str, event_id: str) -> str:
    return f"{event_type}:{event_id}"
