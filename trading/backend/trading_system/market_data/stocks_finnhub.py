"""Finnhub stock quotes + candles (free tier, requires FINNHUB_API_KEY)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from .base import MarketDataProvider
from .models import AssetClass, Candle, DataFreshness, MarketSession, Quote
from .sessions import us_equity_session

logger = logging.getLogger("trading.market.finnhub")

_TIMEFRAME_TO_RESOLUTION = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
}

_SUPPORTED = frozenset({"AAPL", "NVDA"})


class FinnhubStockProvider(MarketDataProvider):
    name = "finnhub"
    supported_symbols = _SUPPORTED

    def __init__(self, api_key: str | None = None, timeout: float = 12.0):
        self.api_key = (api_key or os.getenv("FINNHUB_API_KEY") or "").strip()
        self.timeout = timeout
        self.base_url = "https://finnhub.io/api/v1"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise RuntimeError("FINNHUB_API_KEY is not configured")
        q = dict(params or {})
        q["token"] = self.api_key
        resp = requests.get(f"{self.base_url}{path}", params=q, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_current_price(self, symbol: str) -> Quote:
        symbol = symbol.upper()
        session = us_equity_session()
        data = self._get("/quote", {"symbol": symbol})
        price = float(data.get("c") or 0.0)
        prev = float(data.get("pc") or price)
        change_pct = ((price - prev) / prev * 100) if prev else float(data.get("dp") or 0.0)
        # Finnhub quote has no volume; candles carry volume — leave 0 here.
        freshness = DataFreshness.LIVE if session == MarketSession.OPEN else DataFreshness.STALE
        return Quote(
            symbol=symbol,
            price=price,
            change_pct=round(change_pct, 4),
            volume=0.0,
            ts=float(data.get("t") or time.time()),
            provider=self.name,
            asset_class=AssetClass.STOCK,
            session=session,
            freshness=freshness,
            stale_reason=None if session == MarketSession.OPEN else "market_closed",
        )

    def get_candles(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        symbol = symbol.upper()
        resolution = _TIMEFRAME_TO_RESOLUTION.get(timeframe)
        if resolution is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        # Free tier: keep window modest (Finnhub truncates long intraday ranges).
        seconds = {"1": 60, "5": 300, "15": 900, "60": 3600}[resolution]
        now = int(time.time())
        span = seconds * max(limit, 1)
        # Cap lookback ~5 trading days for 1m to stay within free limits.
        span = min(span, 5 * 24 * 3600)
        raw = self._get(
            "/stock/candle",
            {
                "symbol": symbol,
                "resolution": resolution,
                "from": now - span,
                "to": now,
            },
        )
        if not isinstance(raw, dict) or raw.get("s") != "ok":
            logger.warning("Finnhub candles unavailable for %s: %s", symbol, raw)
            return []
        candles: list[Candle] = []
        for i in range(len(raw.get("t") or [])):
            candles.append(
                Candle(
                    ts=float(raw["t"][i]),
                    open=float(raw["o"][i]),
                    high=float(raw["h"][i]),
                    low=float(raw["l"][i]),
                    close=float(raw["c"][i]),
                    volume=float(raw["v"][i]) if raw.get("v") else 0.0,
                )
            )
        candles.sort(key=lambda c: c.ts)
        return candles[-limit:]
