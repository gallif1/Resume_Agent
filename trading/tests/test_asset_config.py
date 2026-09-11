"""Asset config modes and portfolio-control enforcement (paper only)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import trading_system.asset_config as asset_config
from trading_system.market_data.candle_store import MarketDB
from trading_system.models import Portfolio, Position


def _isolate(tmp_path, monkeypatch):
    db = MarketDB(path=tmp_path / "m.db")
    monkeypatch.setattr(asset_config, "get_market_db", lambda: db)
    monkeypatch.setattr(asset_config, "_CONTROLS_PATH", tmp_path / "controls.json")
    monkeypatch.setattr(asset_config, "_controls", None)
    monkeypatch.setattr(asset_config, "DATA_DIR", tmp_path)
    import trading_system.market_data.candle_store as cs

    monkeypatch.setattr(cs, "_db", db)
    return db


def test_defaults_monitor_only(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    rows = db.ensure_default_asset_configs(["BTC-USD", "AAPL"], positions={})
    assert len(rows) == 2
    assert all(r["mode"] == "MONITOR_ONLY" for r in rows)
    assert asset_config.get_asset_mode("BTC-USD") == asset_config.TradingAssetMode.MONITOR_ONLY


def test_open_position_defaults_close_only(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    rows = db.ensure_default_asset_configs(
        ["ETH-USD", "SOL-USD"],
        positions={"ETH-USD": {"quantity": 1.5, "avg_price": 2000}},
    )
    by_sym = {r["symbol"]: r["mode"] for r in rows}
    assert by_sym["ETH-USD"] == "CLOSE_ONLY"
    assert by_sym["SOL-USD"] == "MONITOR_ONLY"


def test_monitor_only_blocks_orders(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    db.set_trading_asset_config({"symbol": "BTC-USD", "mode": "MONITOR_ONLY"})
    portfolio = Portfolio(cash=100_000)
    ok, reason = asset_config.can_open_order(
        "BTC-USD", "BUY", 0.01, 50000, 0.8, portfolio
    )
    assert ok is False
    assert "מעקב" in reason or "חסום" in reason


def test_trade_allows_orders(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    db.set_trading_asset_config({"symbol": "BTC-USD", "mode": "TRADE"})
    portfolio = Portfolio(cash=100_000)
    ok, reason = asset_config.can_open_order(
        "BTC-USD", "BUY", 0.01, 50000, 0.8, portfolio
    )
    assert ok is True
    assert reason == ""


def test_close_only_blocks_increase(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    db.set_trading_asset_config({"symbol": "SOL-USD", "mode": "CLOSE_ONLY"})
    portfolio = Portfolio(
        cash=50_000,
        positions={"SOL-USD": Position("SOL-USD", 10.0, 100.0)},
    )
    ok_buy, reason_buy = asset_config.can_open_order(
        "SOL-USD", "BUY", 1.0, 100.0, 0.9, portfolio
    )
    assert ok_buy is False
    assert "סגירה" in reason_buy

    ok_sell, reason_sell = asset_config.can_open_order(
        "SOL-USD", "SELL", 5.0, 100.0, 0.9, portfolio
    )
    assert ok_sell is True
    assert reason_sell == ""


def test_close_only_blocks_sell_without_position(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    db.set_trading_asset_config({"symbol": "AAPL", "mode": "CLOSE_ONLY"})
    portfolio = Portfolio(cash=10_000)
    ok, reason = asset_config.can_open_order("AAPL", "SELL", 1.0, 200.0, 0.7, portfolio)
    assert ok is False
    assert "אין פוזיציה" in reason


def test_disabled_cannot_analyze(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    db.set_trading_asset_config({"symbol": "NVDA", "mode": "DISABLED"})
    assert asset_config.can_analyze("NVDA") is False
    assert asset_config.can_analyze("AAPL") is True  # default MONITOR_ONLY


def test_portfolio_controls_pause_new_entries(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    db.set_trading_asset_config({"symbol": "BTC-USD", "mode": "TRADE"})
    asset_config.update_portfolio_controls({"pause_new_entries": True})
    portfolio = Portfolio(cash=100_000)
    ok, reason = asset_config.can_open_order(
        "BTC-USD", "BUY", 0.01, 50000, 0.8, portfolio
    )
    assert ok is False
    assert "מושהות" in reason


def test_ensure_defaults_never_trade(tmp_path, monkeypatch):
    db = _isolate(tmp_path, monkeypatch)
    rows = db.ensure_default_asset_configs(
        ["BTC-USD"],
        positions={"BTC-USD": Position("BTC-USD", 0.5, 40000)},
    )
    assert rows[0]["mode"] == "CLOSE_ONLY"
    # Re-ensure must not upgrade existing to TRADE
    again = db.ensure_default_asset_configs(["BTC-USD"], positions={})
    assert again[0]["mode"] == "CLOSE_ONLY"
