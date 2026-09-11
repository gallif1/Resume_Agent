"""Market data service: providers → cache → consumers (no per-tick HTTP)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..config import (
    CRYPTO_POLL_INTERVAL_SECONDS,
    DEFAULT_SYMBOLS,
    STOCK_POLL_INTERVAL_SECONDS,
    STALE_AFTER_SECONDS,
)
from .candle_store import get_market_db
from .crypto_coinbase import CoinbaseCryptoProvider
from .models import AssetClass, Candle, DataFreshness, MarketSession, Quote
from .sessions import us_equity_session
from .stocks_finnhub import FinnhubStockProvider, StockMarketProvider

logger = logging.getLogger("trading.market.service")

CRYPTO_SYMBOLS = frozenset({"BTC-USD", "ETH-USD", "SOL-USD"})
STOCK_SYMBOLS = frozenset({"AAPL", "NVDA"})
SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
CHART_CANDLE_LIMIT = 500


class MarketDataService:
    """Single entry point for quotes/candles. Polls providers on a schedule."""

    def __init__(self, symbols: tuple[str, ...] | None = None):
        self.symbols = tuple(s.upper() for s in (symbols or DEFAULT_SYMBOLS))
        self.crypto = CoinbaseCryptoProvider()
        self.stocks = FinnhubStockProvider()
        self._quotes: dict[str, Quote] = {}
        self._candles: dict[tuple[str, str], list[Candle]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_crypto_poll = 0.0
        self._last_stock_poll = 0.0
        self._last_candle_poll: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self.default_timeframe = "5m"
        self.db = get_market_db()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.refresh(force=True)
        self._thread = threading.Thread(target=self._loop, name="market-data-poller", daemon=True)
        self._thread.start()
        logger.info(
            "MarketDataService started (crypto every %ss, stocks every %ss)",
            CRYPTO_POLL_INTERVAL_SECONDS,
            STOCK_POLL_INTERVAL_SECONDS,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh(force=False)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Market poll failed: %s", exc)
            self._stop.wait(1.0)

    def refresh(self, force: bool = False) -> None:
        now = time.time()
        crypto_due = force or (now - self._last_crypto_poll) >= CRYPTO_POLL_INTERVAL_SECONDS
        stock_due = force or (now - self._last_stock_poll) >= STOCK_POLL_INTERVAL_SECONDS
        if crypto_due:
            self._poll_crypto()
            self._last_crypto_poll = time.time()
        if stock_due:
            self._poll_stocks()
            self._last_stock_poll = time.time()
        # Candles: refresh less often (every 2 crypto/stock intervals).
        for symbol in self.symbols:
            last = self._last_candle_poll.get(symbol, 0.0)
            interval = (
                CRYPTO_POLL_INTERVAL_SECONDS * 2
                if symbol in CRYPTO_SYMBOLS
                else STOCK_POLL_INTERVAL_SECONDS * 2
            )
            if force or (time.time() - last) >= interval:
                self._poll_candles(symbol)
                self._last_candle_poll[symbol] = time.time()

    def _poll_crypto(self) -> None:
        for symbol in self.symbols:
            if symbol not in CRYPTO_SYMBOLS:
                continue
            try:
                quote = self.crypto.get_current_price(symbol)
                with self._lock:
                    self._quotes[symbol] = quote
                    self._errors.pop(symbol, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Crypto quote failed %s: %s", symbol, exc)
                self._mark_stale(symbol, str(exc), AssetClass.CRYPTO, MarketSession.OPEN)

    def _poll_stocks(self) -> None:
        session = us_equity_session()
        for symbol in self.symbols:
            if symbol not in STOCK_SYMBOLS:
                continue
            if session == MarketSession.CLOSED and symbol in self._quotes:
                existing = self._quotes.get(symbol)
                if existing and existing.price > 0:
                    # Keep last quote; mark closed/stale — do not invent prices.
                    with self._lock:
                        q = self._quotes[symbol]
                        q.session = MarketSession.CLOSED
                        q.freshness = DataFreshness.STALE
                        q.stale_reason = "market_closed"
                    continue
            try:
                # Quotes: Finnhub when keyed, otherwise Yahoo (paper-trading friendly).
                quote = self.stocks.get_current_price(symbol)
                with self._lock:
                    self._quotes[symbol] = quote
                    self._errors.pop(symbol, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Stock quote failed %s: %s", symbol, exc)
                self._mark_unavailable(
                    symbol,
                    f"stock quote failed: {exc}"[:160],
                    AssetClass.STOCK,
                    session,
                )

    def _poll_candles(self, symbol: str) -> None:
        provider = self.crypto if symbol in CRYPTO_SYMBOLS else self.stocks
        # Always attempt candles — Yahoo stock candles need no Finnhub key.
        # Warm chart TFs with enough history for the UI (500); keep lighter for rare TFs.
        limits = {
            "1m": 500,
            "5m": 500,
            "15m": 500,
            "1h": 500,
            "4h": 300,
            "1d": 400,
        }
        for tf in SUPPORTED_TIMEFRAMES:
            try:
                candles = provider.get_candles(symbol, tf, limit=limits.get(tf, 200))
                if candles:
                    self.db.upsert_candles(symbol, tf, candles)
                with self._lock:
                    # Prefer merged DB history when available.
                    merged = self.db.get_candles(symbol, tf, limit=limits.get(tf, 500))
                    self._candles[(symbol, tf)] = merged or candles
            except Exception as exc:  # noqa: BLE001
                logger.warning("Candles failed %s %s: %s", symbol, tf, exc)

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 500,
        before: float | None = None,
    ) -> dict[str, Any]:
        """Historical candles with paging. Never fabricates data."""
        symbol = symbol.upper()
        timeframe = timeframe.strip().lower()
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {SUPPORTED_TIMEFRAMES}")
        limit = max(1, min(int(limit), 2000))

        # Serve from DB first when enough rows exist.
        cached = self.db.get_candles(symbol, timeframe, limit=limit, before=before)
        has_more = len(cached) >= limit
        if len(cached) >= min(limit, 50) or before is not None:
            # If paging older history and DB thin, hit provider.
            if before is not None and len(cached) < limit:
                provider = self.crypto if symbol in CRYPTO_SYMBOLS else self.stocks
                if not provider.supports(symbol):
                    return {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "candles": [],
                        "has_more": False,
                        "provider": "none",
                        "unavailable": True,
                        "reason": "symbol_not_supported_by_provider",
                    }
                try:
                    fetched = provider.get_candles(symbol, timeframe, limit, before=before)
                    if fetched:
                        self.db.upsert_candles(symbol, timeframe, fetched)
                        cached = self.db.get_candles(
                            symbol, timeframe, limit=limit, before=before
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("fetch_candles provider failed: %s", exc)
            provider_name = (
                self.crypto.name if symbol in CRYPTO_SYMBOLS else self.stocks.name
            )
            with self._lock:
                if before is None and cached:
                    self._candles[(symbol, timeframe)] = cached[-CHART_CANDLE_LIMIT:]
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": [c.to_dict() for c in cached],
                "has_more": has_more or (len(cached) >= limit),
                "provider": provider_name,
                "unavailable": len(cached) == 0,
            }

        provider = self.crypto if symbol in CRYPTO_SYMBOLS else self.stocks
        if not provider.supports(symbol):
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": [],
                "has_more": False,
                "provider": "none",
                "unavailable": True,
                "reason": "symbol_not_supported_by_provider",
            }
        try:
            fetched = provider.get_candles(symbol, timeframe, limit, before=before)
        except Exception as exc:  # noqa: BLE001
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": [],
                "has_more": False,
                "provider": provider.name,
                "unavailable": True,
                "reason": str(exc)[:200],
            }
        if fetched:
            self.db.upsert_candles(symbol, timeframe, fetched)
        with self._lock:
            if before is None:
                self._candles[(symbol, timeframe)] = fetched[-CHART_CANDLE_LIMIT:]
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [c.to_dict() for c in fetched],
            "has_more": len(fetched) >= limit,
            "provider": provider.name,
            "unavailable": len(fetched) == 0,
        }

    def provider_for_symbol(self, symbol: str) -> str:
        symbol = symbol.upper()
        if symbol in CRYPTO_SYMBOLS:
            return self.crypto.name
        if symbol in STOCK_SYMBOLS:
            return self.stocks.name if self.stocks.configured else "yahoo"
        return "unknown"

    def _mark_stale(
        self,
        symbol: str,
        reason: str,
        asset_class: AssetClass,
        session: MarketSession,
    ) -> None:
        with self._lock:
            self._errors[symbol] = reason
            existing = self._quotes.get(symbol)
            if existing:
                existing.freshness = DataFreshness.STALE
                existing.stale_reason = reason
                existing.session = session
            else:
                self._quotes[symbol] = Quote(
                    symbol=symbol,
                    price=0.0,
                    change_pct=0.0,
                    volume=0.0,
                    provider="none",
                    asset_class=asset_class,
                    session=session,
                    freshness=DataFreshness.UNAVAILABLE,
                    stale_reason=reason,
                )

    def _mark_unavailable(
        self,
        symbol: str,
        reason: str,
        asset_class: AssetClass,
        session: MarketSession,
    ) -> None:
        with self._lock:
            self._errors[symbol] = reason
            self._quotes[symbol] = Quote(
                symbol=symbol,
                price=0.0,
                change_pct=0.0,
                volume=0.0,
                provider="finnhub",
                asset_class=asset_class,
                session=session,
                freshness=DataFreshness.UNAVAILABLE,
                stale_reason=reason,
            )

    def get_quote(self, symbol: str) -> Quote | None:
        with self._lock:
            q = self._quotes.get(symbol.upper())
            if not q:
                return None
            age = time.time() - q.ts
            if age > STALE_AFTER_SECONDS and q.freshness == DataFreshness.LIVE:
                q.freshness = DataFreshness.STALE
                q.stale_reason = q.stale_reason or "cache_age"
            return q

    def get_quotes(self) -> list[Quote]:
        return [q for s in self.symbols if (q := self.get_quote(s)) is not None]

    def get_candles(self, symbol: str, timeframe: str = "5m") -> list[Candle]:
        with self._lock:
            return list(self._candles.get((symbol.upper(), timeframe), []))

    def price_history_points(self, timeframe: str = "5m") -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for symbol in self.symbols:
            candles = self.get_candles(symbol, timeframe)
            out[symbol] = [
                {
                    "ts": c.ts,
                    "price": c.close,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "volume": c.volume,
                }
                for c in candles
            ]
        return out

    def price_near(
        self,
        symbol: str,
        target_ts: float,
        *,
        max_skew_sec: float = 180.0,
    ) -> tuple[float | None, str]:
        """Best available close near target_ts from cached candles.

        Returns (price, status) where status is ok | no_history | unavailable.
        Does not fabricate prices.
        """
        symbol = symbol.upper()
        best: tuple[float, float] | None = None  # (abs_skew, price)
        for tf in ("1m", "5m", "15m", "1h"):
            candles = self.get_candles(symbol, tf)
            for c in candles:
                skew = abs(float(c.ts) - target_ts)
                if skew > max_skew_sec and tf != "1h":
                    # Allow slightly looser match on coarser TFs.
                    continue
                allow = max_skew_sec if tf in {"1m", "5m"} else max_skew_sec * 2
                if tf == "1h":
                    allow = max(max_skew_sec * 4, 900.0)
                if skew > allow:
                    continue
                if best is None or skew < best[0]:
                    best = (skew, float(c.close))
        if best is not None:
            return best[1], "ok"
        # Fall back to live quote only when target is recent.
        q = self.get_quote(symbol)
        if q and q.price > 0 and abs(time.time() - target_ts) <= max_skew_sec:
            return float(q.price), "ok"
        if not self.get_candles(symbol, "1m") and not self.get_candles(symbol, "5m"):
            return None, "no_history"
        return None, "unavailable"

    def market_meta(self) -> dict[str, Any]:
        quotes = self.get_quotes()
        return {
            "source": "REAL MARKET DATA",
            "simulated": False,
            "crypto_provider": self.crypto.name,
            "stock_provider": (
                self.stocks.name if self.stocks.configured else "yahoo(no-finnhub-key)"
            ),
            "stock_provider_configured": self.stocks.configured,
            "stock_quotes": "finnhub+yahoo-fallback" if self.stocks.configured else "yahoo",
            "stock_candles": "yahoo",
            "crypto_poll_interval_sec": CRYPTO_POLL_INTERVAL_SECONDS,
            "stock_poll_interval_sec": STOCK_POLL_INTERVAL_SECONDS,
            "timeframes": list(SUPPORTED_TIMEFRAMES),
            "default_timeframe": self.default_timeframe,
            "chart_candle_limit": CHART_CANDLE_LIMIT,
            "us_equity_session": us_equity_session().value,
            "symbols": {
                q.symbol: {
                    "provider": q.provider,
                    "asset_class": q.asset_class.value,
                    "session": q.session.value,
                    "freshness": q.freshness.value,
                    "stale_reason": q.stale_reason,
                    "last_update_ts": q.ts,
                    "price": q.price,
                    "change_pct": q.change_pct,
                }
                for q in quotes
            },
            "errors": dict(self._errors),
        }

    def ticks_from_cache(self) -> list[dict[str, Any]]:
        """Shape compatible with legacy Tick.to_dict for agents/UI."""
        ticks = []
        for q in self.get_quotes():
            if q.freshness == DataFreshness.UNAVAILABLE or q.price <= 0:
                continue
            ticks.append(
                {
                    "symbol": q.symbol,
                    "price": q.price,
                    "change_pct": q.change_pct,
                    "volume": q.volume,
                    "ts": q.ts,
                    "provider": q.provider,
                    "session": q.session.value,
                    "freshness": q.freshness.value,
                }
            )
        return ticks
