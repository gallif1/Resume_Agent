"""US equity session helpers (paper-trading approximation)."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from .models import MarketSession

# Prefer IANA tz (needs system tzdata or the PyPI `tzdata` package). Fall back to
# a fixed US Eastern offset so importing trading_system never hard-crashes the
# host API when the container image lacks zoneinfo files.
try:
    from zoneinfo import ZoneInfo

    try:
        _ET = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError without tzdata
        try:
            import tzdata  # noqa: F401

            _ET = ZoneInfo("America/New_York")
        except Exception:  # noqa: BLE001
            _ET = timezone(timedelta(hours=-4))  # approx EDT; session bounds still usable
except Exception:  # noqa: BLE001
    _ET = timezone(timedelta(hours=-4))

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
