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
from .sessions import session_allows_live_refresh, us_equity_session
from .stocks_finnhub import FinnhubStockProvider, StockMarketProvider

logger = logging.getLogger("trading.market.service")

CRYPTO_SYMBOLS = frozenset({"BTC-USD", "ETH-USD", "SOL-USD"})
STOCK_SYMBOLS = frozenset({"AAPL", "NVDA"})
SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
CHART_CANDLE_LIMIT = 500

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def align_candles_to_timeframe(candles: list[Candle], timeframe: str) -> list[Candle]:
    """Bucket + merge candles onto timeframe opens (UTC). Drops invalid bars."""
    secs = TIMEFRAME_SECONDS.get(timeframe, 300)
    merged: dict[int, Candle] = {}
    for c in candles:
        try:
            ts = int(float(c.ts) // secs * secs)
            o, h, lo, cl = float(c.open), float(c.high), float(c.low), float(c.close)
            vol = float(c.volume or 0)
        except (TypeError, ValueError):
            continue
        if not all(map(lambda x: x == x and abs(x) != float("inf"), (o, h, lo, cl))):
            continue
        hi = max(h, o, cl, lo)
        low = min(lo, o, cl, h)
        prev = merged.get(ts)
        if prev is None:
            merged[ts] = Candle(
                ts=float(ts),
                open=o,
                high=hi,
                low=low,
                close=cl,
                volume=vol,
                asset_type=getattr(c, "asset_type", "") or "",
                provider=getattr(c, "provider", "") or "",
                complete=bool(getattr(c, "complete", True)),
            )
        else:
            prev.high = max(prev.high, hi)
            prev.low = min(prev.low, low)
            prev.close = cl
            prev.volume = float(prev.volume or 0) + vol
            prev.complete = bool(getattr(c, "complete", True))
    return [merged[k] for k in sorted(merged)]


def _candle_diagnostics(
    candles: list[Candle],
    *,
    provider: str,
    freshness: str,
    error: str | None = None,
    refreshed: bool = False,
    from_cache: bool = False,
) -> dict[str, Any]:
    first_ts = float(candles[0].ts) if candles else None
    last_ts = float(candles[-1].ts) if candles else None
    age = (time.time() - last_ts) if last_ts is not None else None
    return {
        "provider": provider,
        "candle_count": len(candles),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "last_age_sec": round(age, 1) if age is not None else None,
        "freshness": freshness,
        "error": error,
        "refreshed": refreshed,
        "from_cache": from_cache,
    }


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
                        q.stale_reason = "closed"
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
                    candles = align_candles_to_timeframe(candles, tf)
                    self.db.upsert_candles(symbol, tf, candles)
                with self._lock:
                    # Prefer merged DB history when available.
                    merged = self.db.get_candles(symbol, tf, limit=limits.get(tf, 500))
                    self._candles[(symbol, tf)] = merged or candles
            except Exception as exc:  # noqa: BLE001
                logger.warning("Candles failed %s %s: %s", symbol, tf, exc)

    def _provider_for(self, symbol: str):
        """Route by asset class — never send equities to Coinbase."""
        symbol = normalize_symbol(symbol)
        if symbol in CRYPTO_SYMBOLS:
            return self.crypto
        if symbol in STOCK_SYMBOLS:
            return self.stocks
        # Fallback: hyphenated → crypto product style; else stocks.
        if "-" in symbol:
            return self.crypto
        return self.stocks

    def _is_stock(self, symbol: str) -> bool:
        return normalize_symbol(symbol) in STOCK_SYMBOLS

    def _is_crypto(self, symbol: str) -> bool:
        return normalize_symbol(symbol) in CRYPTO_SYMBOLS

    def _market_open_for(self, symbol: str) -> bool:
        symbol = normalize_symbol(symbol)
        if self._is_crypto(symbol):
            return True
        return session_allows_live_refresh(us_equity_session())

    def _session_for(self, symbol: str) -> MarketSession:
        symbol = normalize_symbol(symbol)
        if self._is_crypto(symbol):
            return MarketSession.OPEN
        return us_equity_session()

    def _cache_needs_refresh(
        self,
        symbol: str,
        timeframe: str,
        cached: list[Candle],
        *,
        force_refresh: bool,
        before: float | None,
    ) -> bool:
        """Decide whether to hit the live provider instead of serving DB cache."""
        if force_refresh and before is None:
            return True
        if before is not None:
            # Paging older history: refresh only when DB is thin.
            return len(cached) < min(50, 2000)
        if len(cached) < min(50, 500):
            return True
        last_ts = float(cached[-1].ts)
        age = time.time() - last_ts
        tf_sec = TIMEFRAME_SECONDS.get(timeframe, 300)
        market_open = self._market_open_for(symbol)
        # Crypto / open equity: refresh when last bar older than 2× timeframe.
        if market_open and age > tf_sec * 2:
            return True
        # Stocks: always attempt Yahoo when opening chart if open+stale.
        if self._is_stock(symbol) and market_open and age > tf_sec * 2:
            return True
        return False

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 500,
        before: float | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Historical candles with paging. Never fabricates gap candles."""
        symbol = normalize_symbol(symbol)
        timeframe = timeframe.strip().lower()
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {SUPPORTED_TIMEFRAMES}")
        limit = max(1, min(int(limit), 2000))

        provider = self._provider_for(symbol)
        provider_name = provider.name
        session = self._session_for(symbol)
        session_value = session.value

        if not provider.supports(symbol):
            diag = _candle_diagnostics(
                [],
                provider="none",
                freshness=DataFreshness.UNAVAILABLE.value,
                error="symbol_not_supported_by_provider",
            )
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": [],
                "has_more": False,
                "provider": "none",
                "unavailable": True,
                "reason": "symbol_not_supported_by_provider",
                "error": "סמל לא נתמך / Symbol not supported by provider",
                "session": session_value,
                "freshness": DataFreshness.UNAVAILABLE.value,
                "diagnostics": diag,
            }

        cached = self.db.get_candles(symbol, timeframe, limit=limit, before=before)
        error: str | None = None
        refreshed = False
        need_refresh = self._cache_needs_refresh(
            symbol,
            timeframe,
            cached,
            force_refresh=force_refresh,
            before=before,
        )

        if need_refresh:
            try:
                fetched = provider.get_historical_candles(
                    symbol, timeframe, limit, before=before
                )
                if fetched:
                    # Ensure returned rows belong only to this symbol's request.
                    fetched = align_candles_to_timeframe(fetched, timeframe)
                    self.db.upsert_candles(symbol, timeframe, fetched)
                    cached = self.db.get_candles(
                        symbol, timeframe, limit=limit, before=before
                    )
                    refreshed = True
                else:
                    error = f"empty provider response for {symbol} {timeframe}"
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:240]
                logger.warning("fetch_candles provider failed %s %s: %s", symbol, timeframe, exc)

        # Closed / extended hours: historical cache is OK (STALE), never invent gaps.
        market_open = self._market_open_for(symbol)
        if cached:
            last_age = time.time() - float(cached[-1].ts)
            tf_sec = TIMEFRAME_SECONDS.get(timeframe, 300)
            if market_open and last_age <= tf_sec * 2:
                freshness = DataFreshness.LIVE.value
            elif not market_open:
                freshness = DataFreshness.STALE.value
            else:
                freshness = DataFreshness.STALE.value
        else:
            freshness = DataFreshness.UNAVAILABLE.value

        with self._lock:
            if before is None and cached:
                self._candles[(symbol, timeframe)] = cached[-CHART_CANDLE_LIMIT:]

        # Always return timeframe-aligned bars to the chart (fixes Yahoo/live mix).
        cached = align_candles_to_timeframe(cached, timeframe)[-limit:]

        candles_out = [c.to_dict() for c in cached]
        # Normalize schema: strip fields frontend may ignore but keep diagnostics rich.
        for row in candles_out:
            row.setdefault("asset_type", "stock" if self._is_stock(symbol) else "crypto")
            row.setdefault("provider", provider_name)
            row.setdefault("complete", True)

        unavailable = len(cached) == 0
        reason = None
        user_error = None
        if unavailable:
            reason = error or "no_candles_available"
            if self._is_stock(symbol) and not market_open:
                user_error = (
                    "אין נרות זמינים לשוק סגור / No candles available "
                    "(market closed; Yahoo/cache empty)"
                )
            else:
                user_error = (
                    f"נכשל בטעינת נרות / Failed to load candles: {reason}"
                )
        elif error and not refreshed:
            reason = f"serving_cache_after_error:{error}"
            user_error = None  # have data; UI can show STALE via diagnostics

        diag = _candle_diagnostics(
            cached,
            provider=provider_name,
            freshness=freshness,
            error=error,
            refreshed=refreshed,
            from_cache=not refreshed and len(cached) > 0,
        )
        diag["session"] = session_value
        diag["force_refresh"] = bool(force_refresh)
        diag["market_open"] = market_open

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": candles_out,
            "has_more": len(cached) >= limit,
            "provider": provider_name,
            "unavailable": unavailable,
            "reason": reason,
            "error": user_error,
            "session": session_value,
            "freshness": freshness,
            "diagnostics": diag,
        }

    def provider_for_symbol(self, symbol: str) -> str:
        symbol = normalize_symbol(symbol)
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
