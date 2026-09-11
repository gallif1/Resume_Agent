"""Stock market data: Finnhub quotes + Yahoo candles (free-tier friendly).

Finnhub free keys often allow /quote but deny /stock/candle. Yahoo chart API
supplies historical OHLCV without a key so charts still work for paper trading.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from .base import MarketDataProvider
from .models import AssetClass, Candle, DataFreshness, MarketSession, Quote
from .sessions import us_equity_session

logger = logging.getLogger("trading.market.stocks")

_SUPPORTED = frozenset({"AAPL", "NVDA"})

# Yahoo interval + range for each UI timeframe.
_YAHOO_TF = {
    "1m": ("1m", "5d"),
    "5m": ("5m", "1mo"),
    "15m": ("15m", "1mo"),
    "1h": ("60m", "3mo"),
    "4h": ("60m", "6mo"),  # aggregated from 1h
    "1d": ("1d", "2y"),
}


class StockMarketProvider(MarketDataProvider):
    """Composite stock provider used by MarketDataService."""

    name = "finnhub+yahoo"
    supported_symbols = _SUPPORTED

    def __init__(self, api_key: str | None = None, timeout: float = 12.0):
        self.api_key = (api_key or os.getenv("FINNHUB_API_KEY") or "").strip()
        self.timeout = timeout
        self.finnhub_url = "https://finnhub.io/api/v1"
        self.yahoo_url = "https://query1.finance.yahoo.com/v8/finance/chart"
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "ResumeAgentTrading/1.0 (paper-trading; +https://github.com)",
                "Accept": "application/json",
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _finnhub_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise RuntimeError("FINNHUB_API_KEY is not configured")
        q = dict(params or {})
        q["token"] = self.api_key
        resp = self._session.get(
            f"{self.finnhub_url}{path}", params=q, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def get_current_price(self, symbol: str) -> Quote:
        symbol = symbol.upper()
        session = us_equity_session()
        price = 0.0
        change_pct = 0.0
        volume = 0.0
        ts = time.time()
        provider = "yahoo"

        if self.configured:
            try:
                data = self._finnhub_get("/quote", {"symbol": symbol})
                price = float(data.get("c") or 0.0)
                if price > 0:
                    prev = float(data.get("pc") or price)
                    change_pct = (
                        ((price - prev) / prev * 100) if prev else float(data.get("dp") or 0.0)
                    )
                    volume = 0.0
                    ts = float(data.get("t") or time.time())
                    provider = self.name
            except Exception as exc:  # noqa: BLE001
                logger.warning("Finnhub quote failed %s: %s — trying Yahoo", symbol, exc)

        if price <= 0:
            price, change_pct, volume, ts = self._yahoo_quote(symbol)
            provider = "yahoo" if not self.configured else "yahoo-fallback"

        freshness = DataFreshness.LIVE if session == MarketSession.OPEN else DataFreshness.STALE
        return Quote(
            symbol=symbol,
            price=price,
            change_pct=round(change_pct, 4),
            volume=volume,
            ts=ts,
            provider=provider,
            asset_class=AssetClass.STOCK,
            session=session,
            freshness=freshness,
            stale_reason=None if session == MarketSession.OPEN else "market_closed",
        )

    def _yahoo_quote(self, symbol: str) -> tuple[float, float, float, float]:
        raw = self._session.get(
            f"{self.yahoo_url}/{symbol}",
            params={"interval": "5m", "range": "1d"},
            timeout=self.timeout,
        )
        raw.raise_for_status()
        result = (raw.json().get("chart") or {}).get("result") or []
        if not result:
            raise RuntimeError(f"Yahoo quote empty for {symbol}")
        meta = result[0].get("meta") or {}
        price = float(meta.get("regularMarketPrice") or meta.get("previousClose") or 0)
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        change = ((price - prev) / prev * 100) if prev else 0.0
        return price, change, 0.0, time.time()

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        *,
        before: float | None = None,
    ) -> list[Candle]:
        symbol = symbol.upper()
        # Prefer Yahoo — Finnhub free keys often cannot access /stock/candle.
        try:
            candles = self._yahoo_candles(symbol, timeframe, limit, before=before)
            if candles:
                return candles
        except Exception as exc:  # noqa: BLE001
            logger.warning("Yahoo candles failed %s %s: %s", symbol, timeframe, exc)

        # Optional Finnhub attempt (paid / elevated keys).
        if self.configured and timeframe not in {"4h", "1d"}:
            try:
                return self._finnhub_candles(symbol, timeframe, limit, before=before)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Finnhub candles failed %s: %s", symbol, exc)
        return []

    def _yahoo_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        *,
        before: float | None = None,
    ) -> list[Candle]:
        from ..indicators.calc import aggregate_candles

        pair = _YAHOO_TF.get(timeframe)
        if pair is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        interval, range_ = pair
        params: dict[str, Any] = {
            "interval": interval,
            "range": range_,
            "includePrePost": "false",
        }
        if before is not None:
            # period2 = before; period1 estimated from limit * seconds
            secs = {"1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": 86400}.get(interval, 3600)
            period2 = int(before)
            period1 = period2 - secs * max(limit * (4 if timeframe == "4h" else 1), 50)
            params = {
                "interval": interval,
                "period1": period1,
                "period2": period2,
                "includePrePost": "false",
            }
        resp = self._session.get(
            f"{self.yahoo_url}/{symbol}",
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        result = (resp.json().get("chart") or {}).get("result") or []
        if not result:
            return []
        block = result[0]
        ts_list = block.get("timestamp") or []
        quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        candles: list[Candle] = []
        for i, ts in enumerate(ts_list):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            o = opens[i] if i < len(opens) and opens[i] is not None else c
            h = highs[i] if i < len(highs) and highs[i] is not None else c
            low = lows[i] if i < len(lows) and lows[i] is not None else c
            v = vols[i] if i < len(vols) and vols[i] is not None else 0.0
            if before is not None and float(ts) >= before:
                continue
            candles.append(
                Candle(
                    ts=float(ts),
                    open=float(o),
                    high=float(h),
                    low=float(low),
                    close=float(c),
                    volume=float(v),
                )
            )
        candles.sort(key=lambda c: c.ts)
        if timeframe == "4h":
            candles = aggregate_candles(candles, 4 * 3600)
        return candles[-limit:]

    def _finnhub_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        *,
        before: float | None = None,
    ) -> list[Candle]:
        resolution = {"1m": "1", "5m": "5", "15m": "15", "1h": "60"}.get(timeframe)
        if resolution is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        seconds = {"1": 60, "5": 300, "15": 900, "60": 3600}[resolution]
        now = int(before) if before is not None else int(time.time())
        span = min(seconds * max(limit, 1), 5 * 24 * 3600)
        raw = self._finnhub_get(
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
            ts = float(raw["t"][i])
            if before is not None and ts >= before:
                continue
            candles.append(
                Candle(
                    ts=ts,
                    open=float(raw["o"][i]),
                    high=float(raw["h"][i]),
                    low=float(raw["l"][i]),
                    close=float(raw["c"][i]),
                    volume=float(raw["v"][i]) if raw.get("v") else 0.0,
                )
            )
        candles.sort(key=lambda c: c.ts)
        return candles[-limit:]


# Back-compat alias used by older imports/tests.
FinnhubStockProvider = StockMarketProvider
