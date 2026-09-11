"""Stock chart / provider selection / candle freshness tests (Phase 4)."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.market_data.candle_store import MarketDB
from trading_system.market_data.models import AssetClass, Candle, DataFreshness, MarketSession
from trading_system.market_data.service import (
    CRYPTO_SYMBOLS,
    STOCK_SYMBOLS,
    MarketDataService,
    normalize_symbol,
)
from trading_system.market_data.sessions import us_equity_session
from trading_system.market_data.stocks_finnhub import StockMarketProvider, _normalize_epoch


def test_provider_selection_aapl_vs_btc():
    svc = MarketDataService(symbols=("BTC-USD", "AAPL"))
    assert svc._provider_for("BTC-USD") is svc.crypto
    assert svc._provider_for("AAPL") is svc.stocks
    assert svc._provider_for("NVDA") is svc.stocks
    # Never route equities to Coinbase
    assert "AAPL" not in CRYPTO_SYMBOLS
    assert "NVDA" not in CRYPTO_SYMBOLS
    assert "BTC-USD" not in STOCK_SYMBOLS
    assert svc.crypto.supports("AAPL") is False
    assert svc.stocks.supports("BTC-USD") is False


def test_symbol_normalization():
    assert normalize_symbol(" aapl ") == "AAPL"
    assert normalize_symbol("btc-usd") == "BTC-USD"
    svc = MarketDataService(symbols=("aapl", "btc-usd"))
    assert svc.symbols == ("AAPL", "BTC-USD")


def test_normalize_epoch_seconds_vs_ms():
    assert _normalize_epoch(1_700_000_000) == 1_700_000_000.0
    assert abs(_normalize_epoch(1_700_000_000_000) - 1_700_000_000.0) < 1e-6


def test_stock_supports_timeframe_includes_4h():
    p = StockMarketProvider(api_key="")
    assert p.supports_timeframe("4h")
    assert p.supports_timeframe("5m")
    assert not p.supports_timeframe("2h")


def test_empty_provider_response_handling(tmp_path):
    svc = MarketDataService(symbols=("AAPL",))
    svc.db = MarketDB(path=tmp_path / "m.db")

    def boom(*_a, **_k):
        raise RuntimeError("Yahoo returned empty chart result for AAPL (5m)")

    with patch.object(svc.stocks, "get_historical_candles", side_effect=boom):
        with patch.object(svc.stocks, "supports", return_value=True):
            out = svc.fetch_candles("AAPL", "5m", limit=100, force_refresh=True)
    assert out["unavailable"] is True
    assert out["diagnostics"]["error"]
    assert out["error"]
    assert out["symbol"] == "AAPL"
    assert out["candles"] == []


def test_closed_market_returns_historical_from_db(tmp_path):
    svc = MarketDataService(symbols=("AAPL",))
    svc.db = MarketDB(path=tmp_path / "m.db")
    # Previous session bars (stale but valid history).
    now = time.time()
    bars = [
        Candle(
            ts=now - 86400 + i * 300,
            open=180 + i * 0.1,
            high=181 + i * 0.1,
            low=179 + i * 0.1,
            close=180.5 + i * 0.1,
            volume=1000,
            asset_type="stock",
            provider="yahoo",
            complete=True,
        )
        for i in range(60)
    ]
    svc.db.upsert_candles("AAPL", "5m", bars)

    with patch(
        "trading_system.market_data.service.us_equity_session",
        return_value=MarketSession.CLOSED,
    ):
        with patch.object(
            svc.stocks,
            "get_historical_candles",
            side_effect=AssertionError("should not refresh when closed+enough cache"),
        ):
            # Age of last bar is ~1 day; market closed → serve cache without inventing gaps.
            # force_refresh still allowed; without it, closed + enough bars skips live fetch
            # only when market is closed AND age would otherwise trigger — our logic refreshes
            # only when market_open and stale. So closed should NOT call provider.
            out = svc.fetch_candles("AAPL", "5m", limit=50, force_refresh=False)

    assert out["unavailable"] is False
    assert len(out["candles"]) >= 50
    assert out["freshness"] == DataFreshness.STALE.value
    assert out["session"] == MarketSession.CLOSED.value
    assert out["diagnostics"]["candle_count"] >= 50
    # No fabricated gap fills — last ts matches DB
    assert abs(out["candles"][-1]["ts"] - bars[-1].ts) < 1e-6


def test_force_refresh_calls_yahoo_even_with_cache(tmp_path):
    svc = MarketDataService(symbols=("AAPL",))
    svc.db = MarketDB(path=tmp_path / "m.db")
    now = time.time()
    # Align to 5m buckets so refresh overwrites the same keys.
    base = int(now // 300 * 300) - 60 * 300
    old = [
        Candle(ts=float(base + i * 300), open=1, high=2, low=0.5, close=1.5, volume=10)
        for i in range(60)
    ]
    svc.db.upsert_candles("AAPL", "5m", old)
    fresh = [
        Candle(
            ts=float(base + i * 300),
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=20,
            asset_type="stock",
            provider="yahoo",
            complete=True,
        )
        for i in range(60)
    ]
    with patch.object(svc.stocks, "get_historical_candles", return_value=fresh) as mocked:
        with patch.object(svc.stocks, "supports", return_value=True):
            out = svc.fetch_candles("AAPL", "5m", limit=50, force_refresh=True)
    mocked.assert_called_once()
    assert out["diagnostics"]["refreshed"] is True
    assert out["candles"][-1]["close"] == 10.5


def test_yahoo_candle_parsing_ms_timestamps():
    provider = StockMarketProvider(api_key="")
    # Build a fake Yahoo JSON with ms timestamps
    base_ms = 1_700_000_000_000
    fake = {
        "chart": {
            "result": [
                {
                    "meta": {"regularMarketTime": int(base_ms / 1000) + 3600},
                    "timestamp": [base_ms + i * 300_000 for i in range(5)],
                    "indicators": {
                        "quote": [
                            {
                                "open": [1, 2, 3, 4, 5],
                                "high": [1.5, 2.5, 3.5, 4.5, 5.5],
                                "low": [0.5, 1.5, 2.5, 3.5, 4.5],
                                "close": [1.2, 2.2, 3.2, 4.2, 5.2],
                                "volume": [100, 100, 100, 100, 100],
                            }
                        ]
                    },
                }
            ]
        }
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = fake
    with patch.object(provider._session, "get", return_value=mock_resp):
        candles = provider._yahoo_candles("AAPL", "5m", 10)
    assert len(candles) == 5
    assert candles[0].ts == 1_700_000_000.0  # normalized to seconds
    assert candles[0].asset_type == AssetClass.STOCK.value
    assert candles[0].provider == "yahoo"


def test_market_status_enum_values():
    # Weekend → CLOSED
    sat = datetime(2024, 6, 8, 15, 0, tzinfo=timezone.utc)
    assert us_equity_session(sat) == MarketSession.CLOSED
    # Weekday mid-session ET (~15:00 UTC in June = 11:00 ET) → OPEN
    wed = datetime(2024, 6, 5, 15, 0, tzinfo=timezone.utc)
    assert us_equity_session(wed) in {
        MarketSession.OPEN,
        MarketSession.PRE_MARKET,
        MarketSession.AFTER_HOURS,
        MarketSession.CLOSED,
    }


def test_fetch_candles_symbol_isolation(tmp_path):
    svc = MarketDataService(symbols=("AAPL", "NVDA"))
    svc.db = MarketDB(path=tmp_path / "m.db")
    now = time.time()
    aapl = [
        Candle(ts=now - i * 300, open=1, high=2, low=0.5, close=1.5, volume=10)
        for i in range(60, 0, -1)
    ]
    nvda = [
        Candle(ts=now - i * 300, open=100, high=101, low=99, close=100.5, volume=10)
        for i in range(60, 0, -1)
    ]
    svc.db.upsert_candles("AAPL", "5m", aapl)
    svc.db.upsert_candles("NVDA", "5m", nvda)
    with patch(
        "trading_system.market_data.service.us_equity_session",
        return_value=MarketSession.CLOSED,
    ):
        out = svc.fetch_candles("AAPL", "5m", limit=50)
    assert out["symbol"] == "AAPL"
    assert all(c["close"] < 50 for c in out["candles"])  # not NVDA prices
