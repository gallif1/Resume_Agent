"""Coinbase Exchange public API — crypto spot + OHLCV (no API key)."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .base import MarketDataProvider
from .models import AssetClass, Candle, DataFreshness, MarketSession, Quote

logger = logging.getLogger("trading.market.coinbase")

_TIMEFRAME_TO_GRANULARITY = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}

_SYMBOL_TO_PRODUCT = {
    "BTC-USD": "BTC-USD",
    "ETH-USD": "ETH-USD",
    "SOL-USD": "SOL-USD",
}


class CoinbaseCryptoProvider(MarketDataProvider):
    name = "coinbase"
    supported_symbols = frozenset(_SYMBOL_TO_PRODUCT)

    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout
        self.base_url = "https://api.exchange.coinbase.com"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        resp = requests.get(url, params=params or {}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_current_price(self, symbol: str) -> Quote:
        symbol = symbol.upper()
        product = _SYMBOL_TO_PRODUCT[symbol]
        ticker = self._get(f"/products/{product}/ticker")
        stats = self._get(f"/products/{product}/stats")
        price = float(ticker["price"])
        open_24h = float(stats.get("open") or price)
        change_pct = ((price - open_24h) / open_24h * 100) if open_24h else 0.0
        volume = float(stats.get("volume") or ticker.get("volume") or 0.0)
        return Quote(
            symbol=symbol,
            price=price,
            change_pct=round(change_pct, 4),
            volume=volume,
            ts=time.time(),
            provider=self.name,
            asset_class=AssetClass.CRYPTO,
            session=MarketSession.OPEN,
            freshness=DataFreshness.LIVE,
        )

    def get_quotes(self, symbols):  # type: ignore[override]
        out: list[Quote] = []
        for s in symbols:
            if self.supports(s):
                out.append(self.get_current_price(s))
        return out

    def get_candles(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        symbol = symbol.upper()
        product = _SYMBOL_TO_PRODUCT[symbol]
        granularity = _TIMEFRAME_TO_GRANULARITY.get(timeframe)
        if granularity is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        # Coinbase returns max 300 candles per request.
        end = int(time.time())
        start = end - granularity * max(limit, 1)
        raw = self._get(
            f"/products/{product}/candles",
            {"granularity": granularity, "start": start, "end": end},
        )
        candles: list[Candle] = []
        for row in raw:
            # [time, low, high, open, close, volume]
            candles.append(
                Candle(
                    ts=float(row[0]),
                    low=float(row[1]),
                    high=float(row[2]),
                    open=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        candles.sort(key=lambda c: c.ts)
        return candles[-limit:]
