"""US equity session helpers (paper-trading approximation)."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .models import MarketSession

_ET = ZoneInfo("America/New_York")
_OPEN = time(9, 30)
_CLOSE = time(16, 0)


def us_equity_session(now: datetime | None = None) -> MarketSession:
    """Return OPEN/CLOSED for NYSE/NASDAQ regular hours (no holiday calendar)."""
    dt = now.astimezone(_ET) if now else datetime.now(_ET)
    if dt.weekday() >= 5:
        return MarketSession.CLOSED
    t = dt.time()
    if _OPEN <= t < _CLOSE:
        return MarketSession.OPEN
    return MarketSession.CLOSED


def is_crypto_always_open() -> bool:
    return True
