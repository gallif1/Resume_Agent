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

from .base import SUPPORTED_TIMEFRAMES, MarketDataProvider
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


def _normalize_epoch(ts: float | int) -> float:
    """Yahoo may return seconds or milliseconds."""
    t = float(ts)
    if t > 1e12:  # ms
        return t / 1000.0
    if t > 1e10:  # µs (rare)
        return t / 1_000_000.0
    return t


class StockMarketProvider(MarketDataProvider):
    """Composite stock provider used by MarketDataService.

    Never routes equity symbols to Coinbase — stocks stay here.
    """

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

    def supports_timeframe(self, timeframe: str) -> bool:
        tf = timeframe.strip().lower()
        return tf in SUPPORTED_TIMEFRAMES and tf in _YAHOO_TF

    def get_market_status(self, symbol: str) -> MarketSession:
        _ = symbol  # US equities share the same session calendar here
        return us_equity_session()

    def get_data_freshness(self, symbol: str) -> DataFreshness:
        status = self.get_market_status(symbol)
        if status == MarketSession.OPEN:
            return DataFreshness.LIVE
        return DataFreshness.STALE

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
        session = self.get_market_status(symbol)
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
        stale_reason = None
        if session != MarketSession.OPEN:
            stale_reason = session.value
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
            stale_reason=stale_reason,
        )

    def get_latest_quote(self, symbol: str) -> Quote:
        return self.get_current_price(symbol)

    def _yahoo_quote(self, symbol: str) -> tuple[float, float, float, float]:
        raw = self._session.get(
            f"{self.yahoo_url}/{symbol}",
            params={"interval": "5m", "range": "1d"},
            timeout=self.timeout,
        )
        raw.raise_for_status()
        chart = raw.json().get("chart") or {}
        if chart.get("error"):
            raise RuntimeError(
                f"Yahoo quote error for {symbol}: {chart.get('error')}"
            )
        result = chart.get("result") or []
        if not result:
            raise RuntimeError(f"Yahoo quote empty for {symbol}")
        meta = result[0].get("meta") or {}
        price = float(meta.get("regularMarketPrice") or meta.get("previousClose") or 0)
        if price <= 0:
            raise RuntimeError(f"Yahoo quote missing price for {symbol}")
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
        timeframe = timeframe.strip().lower()
        if not self.supports_timeframe(timeframe):
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        # Prefer Yahoo — Finnhub free keys often cannot access /stock/candle.
        try:
            candles = self._yahoo_candles(symbol, timeframe, limit, before=before)
            if candles:
                return candles
            raise RuntimeError(
                f"Yahoo returned no candles for {symbol} ({timeframe})"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Yahoo candles failed %s %s: %s", symbol, timeframe, exc)
            yahoo_err = exc

        # Optional Finnhub attempt (paid / elevated keys).
        if self.configured and timeframe not in {"4h", "1d"}:
            try:
                candles = self._finnhub_candles(symbol, timeframe, limit, before=before)
                if candles:
                    return candles
            except Exception as exc:  # noqa: BLE001
                logger.warning("Finnhub candles failed %s: %s", symbol, exc)
        raise RuntimeError(
            f"Stock candles unavailable for {symbol} {timeframe}: {yahoo_err}"
        )

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        *,
        before: float | None = None,
    ) -> list[Candle]:
        return self.get_candles(symbol, timeframe, limit, before=before)

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
            secs = {"1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": 86400}.get(
                interval, 3600
            )
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
        body = resp.json()
        chart = body.get("chart") or {}
        err = chart.get("error")
        if err:
            raise RuntimeError(f"Yahoo chart error for {symbol}: {err}")
        result = chart.get("result") or []
        if not result:
            raise RuntimeError(
                f"Yahoo returned empty chart result for {symbol} ({timeframe})"
            )
        block = result[0]
        ts_list = block.get("timestamp") or []
        if not ts_list:
            raise RuntimeError(
                f"Yahoo returned no timestamps for {symbol} ({timeframe})"
            )
        quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        meta = block.get("meta") or {}
        # Current incomplete bar ends at regularMarketTime when session open.
        market_ts = _normalize_epoch(meta.get("regularMarketTime") or time.time())
        tf_sec = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }.get(timeframe, 300)

        candles: list[Candle] = []
        for i, raw_ts in enumerate(ts_list):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            o = opens[i] if i < len(opens) and opens[i] is not None else c
            h = highs[i] if i < len(highs) and highs[i] is not None else c
            low = lows[i] if i < len(lows) and lows[i] is not None else c
            v = vols[i] if i < len(vols) and vols[i] is not None else 0.0
            ts = _normalize_epoch(raw_ts)
            if before is not None and ts >= before:
                continue
            # Last bar is incomplete until its period fully elapses.
            complete = (ts + tf_sec) <= market_ts + 1.0
            if i < len(ts_list) - 1:
                complete = True
            candles.append(
                Candle(
                    ts=ts,
                    open=float(o),
                    high=float(h),
                    low=float(low),
                    close=float(c),
                    volume=float(v),
                    asset_type=AssetClass.STOCK.value,
                    provider="yahoo",
                    complete=complete,
                )
            )
        candles.sort(key=lambda c: c.ts)
        if not candles:
            raise RuntimeError(
                f"Yahoo OHLCV arrays empty/null for {symbol} ({timeframe})"
            )
        if timeframe == "4h":
            candles = aggregate_candles(candles, 4 * 3600)
            # Re-tag after aggregation.
            for c in candles:
                c.asset_type = AssetClass.STOCK.value
                c.provider = "yahoo"
                c.complete = True
            if candles:
                # Last aggregated bucket may still be forming.
                candles[-1].complete = (candles[-1].ts + 4 * 3600) <= market_ts + 1.0
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
        n = len(raw.get("t") or [])
        for i in range(n):
            ts = float(raw["t"][i])
            if before is not None and ts >= before:
                continue
            complete = i < n - 1
            candles.append(
                Candle(
                    ts=ts,
                    open=float(raw["o"][i]),
                    high=float(raw["h"][i]),
                    low=float(raw["l"][i]),
                    close=float(raw["c"][i]),
                    volume=float(raw["v"][i]) if raw.get("v") else 0.0,
                    asset_type=AssetClass.STOCK.value,
                    provider="finnhub",
                    complete=complete,
                )
            )
        candles.sort(key=lambda c: c.ts)
        return candles[-limit:]


# Back-compat alias used by older imports/tests.
FinnhubStockProvider = StockMarketProvider
