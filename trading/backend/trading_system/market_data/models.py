"""Market-data domain models (provider-agnostic)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
import time


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    STOCK = "stock"


class MarketSession(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class DataFreshness(str, Enum):
    LIVE = "live"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass
class Candle:
    ts: float  # candle open/close epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Quote:
    symbol: str
    price: float
    change_pct: float = 0.0
    volume: float = 0.0
    ts: float = field(default_factory=time.time)
    provider: str = ""
    asset_class: AssetClass = AssetClass.CRYPTO
    session: MarketSession = MarketSession.OPEN
    freshness: DataFreshness = DataFreshness.LIVE
    stale_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["asset_class"] = self.asset_class.value
        d["session"] = self.session.value
        d["freshness"] = self.freshness.value
        return d
