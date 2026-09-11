"""Abstract market-data provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from .models import Candle, Quote


class MarketDataProvider(ABC):
    """Provider-specific HTTP lives only behind this interface."""

    name: str
    supported_symbols: frozenset[str]

    @abstractmethod
    def get_current_price(self, symbol: str) -> Quote:
        raise NotImplementedError

    @abstractmethod
    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        *,
        before: float | None = None,
    ) -> list[Candle]:
        """timeframe in {1m,5m,15m,1h,4h,1d}. Optional before=unix for paging."""
        raise NotImplementedError

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        *,
        before: float | None = None,
    ) -> list[Candle]:
        return self.get_candles(symbol, timeframe, limit, before=before)

    def get_ticker(self, symbol: str) -> Quote:
        return self.get_current_price(symbol)

    def get_order_book(self, symbol: str) -> dict | None:
        """Return None when unsupported — never fabricate."""
        return None

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict] | None:
        return None

    def supports_capability(self, capability: str) -> bool:
        caps = {
            "candles": True,
            "ticker": True,
            "order_book": False,
            "recent_trades": False,
            "live_subscribe": False,
        }
        return bool(caps.get(capability, False))

    def get_volume(self, symbol: str) -> float:
        return float(self.get_current_price(symbol).volume)

    def supports(self, symbol: str) -> bool:
        return symbol.upper() in self.supported_symbols

    def get_quotes(self, symbols: Iterable[str]) -> list[Quote]:
        return [self.get_current_price(s) for s in symbols if self.supports(s)]
