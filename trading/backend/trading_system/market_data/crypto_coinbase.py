"""Coinbase Exchange public API — crypto spot + OHLCV (no API key).

Official docs: https://docs.cdp.coinbase.com/
Native granularities: 60,300,900,3600,21600,86400.
4h is aggregated from 1h candles (Coinbase has no native 4h).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from ..indicators.calc import aggregate_candles
from .base import MarketDataProvider
from .models import AssetClass, Candle, DataFreshness, MarketSession, Quote

logger = logging.getLogger("trading.market.coinbase")

_TIMEFRAME_TO_GRANULARITY = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
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

    def supports_capability(self, capability: str) -> bool:
        caps = {
            "candles": True,
            "ticker": True,
            "order_book": True,
            "recent_trades": True,
            "live_subscribe": False,
        }
        return bool(caps.get(capability, False))

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

    def get_order_book(self, symbol: str) -> dict | None:
        symbol = symbol.upper()
        if symbol not in _SYMBOL_TO_PRODUCT:
            return None
        product = _SYMBOL_TO_PRODUCT[symbol]
        try:
            book = self._get(f"/products/{product}/book", {"level": 2})
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            best_bid = float(bids[0][0]) if bids else None
            best_ask = float(asks[0][0]) if asks else None
            bid_depth = sum(float(x[1]) for x in bids[:5]) if bids else None
            ask_depth = sum(float(x[1]) for x in asks[:5]) if asks else None
            imbalance = None
            if bid_depth is not None and ask_depth is not None and (bid_depth + ask_depth) > 0:
                imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "top_bid_depth": bid_depth,
                "top_ask_depth": ask_depth,
                "order_book_imbalance": imbalance,
                "provider": self.name,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Coinbase order book failed %s: %s", symbol, exc)
            return None

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict] | None:
        symbol = symbol.upper()
        if symbol not in _SYMBOL_TO_PRODUCT:
            return None
        product = _SYMBOL_TO_PRODUCT[symbol]
        try:
            raw = self._get(f"/products/{product}/trades")
            out = []
            for row in (raw or [])[:limit]:
                out.append(
                    {
                        "ts": row.get("time"),
                        "price": float(row.get("price") or 0),
                        "size": float(row.get("size") or 0),
                        "side": row.get("side"),
                    }
                )
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("Coinbase trades failed %s: %s", symbol, exc)
            return None

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        *,
        before: float | None = None,
    ) -> list[Candle]:
        symbol = symbol.upper()
        product = _SYMBOL_TO_PRODUCT[symbol]
        # 4h: fetch 1h and aggregate.
        if timeframe == "4h":
            need = max(limit * 4, 100)
            hourly = self.get_candles(symbol, "1h", need, before=before)
            agg = aggregate_candles(hourly, 4 * 3600)
            if before is not None:
                agg = [c for c in agg if c.ts < before]
            return agg[-limit:]

        granularity = _TIMEFRAME_TO_GRANULARITY.get(timeframe)
        if granularity is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        # Coinbase returns max 300 candles per request — page if needed.
        end = int(before) if before is not None else int(time.time())
        remaining = max(limit, 1)
        collected: list[Candle] = []
        while remaining > 0:
            batch = min(300, remaining)
            start = end - granularity * batch
            raw = self._get(
                f"/products/{product}/candles",
                {"granularity": granularity, "start": start, "end": end},
            )
            batch_candles: list[Candle] = []
            for row in raw or []:
                batch_candles.append(
                    Candle(
                        ts=float(row[0]),
                        low=float(row[1]),
                        high=float(row[2]),
                        open=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                )
            if not batch_candles:
                break
            batch_candles.sort(key=lambda c: c.ts)
            collected = batch_candles + collected
            # Dedupe by ts
            by_ts = {c.ts: c for c in collected}
            collected = [by_ts[k] for k in sorted(by_ts)]
            remaining = limit - len(collected)
            end = int(batch_candles[0].ts) - 1
            if len(batch_candles) < batch:
                break
        return collected[-limit:]
