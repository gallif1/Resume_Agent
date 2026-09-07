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
    def get_candles(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        """timeframe in {1m,5m,15m,1h}."""
        raise NotImplementedError

    def get_volume(self, symbol: str) -> float:
        return float(self.get_current_price(symbol).volume)

    def supports(self, symbol: str) -> bool:
        return symbol.upper() in self.supported_symbols

    def get_quotes(self, symbols: Iterable[str]) -> list[Quote]:
        return [self.get_current_price(s) for s in symbols if self.supports(s)]
