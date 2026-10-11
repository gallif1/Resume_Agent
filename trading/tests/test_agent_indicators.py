"""Tests for FeatureSnapshot-driven agents, decision gates, AI cache, and risk."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"
os.environ["TRADING_MIN_EXECUTION_CONFIDENCE"] = "0.55"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.agents import MeanReversionAgent, MomentumAgent, VolatilityAgent
from trading_system.ai.analyst import AIMarketAnalyst, AIResult
from trading_system.decision_engine import DecisionEngine, filter_compatible_votes
from trading_system.indicators.features import FeatureSnapshot, build_feature_snapshot
from trading_system.market_data.models import Candle
from trading_system.models import AgentVote, Portfolio, Position, Side, Tick
from trading_system.risk_engine import RiskEngine


def _candles_trend(n: int = 80, start: float = 100.0, step: float = 0.4) -> list[Candle]:
    out = []
    px = start
    for i in range(n):
        o = px
        c = px + step
        out.append(
            Candle(
                ts=float(1_700_000_000 + i * 300),
                open=o,
                high=max(o, c) * 1.002,
                low=min(o, c) * 0.998,
                close=c,
                volume=2000 + (i % 3) * 800,
            )
        )
        px = c
    return out


def _candles_drop(n: int = 80, start: float = 100.0, step: float = -0.4) -> list[Candle]:
    return _candles_trend(n=n, start=start, step=step)


def _feat(candles: list[Candle], symbol: str = "SOL-USD", tf: str = "5m") -> FeatureSnapshot:
    return build_feature_snapshot(symbol=symbol, timeframe=tf, candles=candles, price=candles[-1].close)


def test_feature_snapshot_fields():
    feat = _feat(_candles_trend())
    assert feat.symbol == "SOL-USD"
    assert feat.timeframe == "5m"
    assert feat.candle_timestamp > 0
    assert feat.ema_20 is not None
    assert feat.ema_50 is not None
    assert feat.rsi_14 is not None
    assert feat.macd is not None
    assert feat.bb_position is not None


def test_momentum_strong_bullish_agreement():
    candles = _candles_trend(n=100, step=0.5)
    # Boost last volumes for confirmation
    for c in candles[-5:]:
        c.volume = 8000
    feat = _feat(candles)
    tick = Tick("SOL-USD", feat.current_price, 0.5, 8000)
    vote = MomentumAgent().vote(tick, [c.close for c in candles], [], features=feat)
    assert vote.side == Side.BUY
    assert vote.score is not None and vote.score >= 0.35
    assert "price_momentum" in vote.components
    assert "ema_trend" in vote.components
    assert vote.timeframe == "5m"
    assert vote.market_timestamp == feat.candle_timestamp
    assert vote.confidence <= 0.95


def test_momentum_strong_bearish_agreement():
    candles = _candles_drop(n=100, step=-0.5)
    for c in candles[-5:]:
        c.volume = 8000
    feat = _feat(candles)
    tick = Tick("SOL-USD", feat.current_price, -0.5, 8000)
    vote = MomentumAgent().vote(tick, [c.close for c in candles], [], features=feat)
    assert vote.side == Side.SELL
    assert vote.score is not None and vote.score <= -0.35


def test_momentum_conflicting_indicators_hold_or_low_conf():
    # Flat-ish series → conflicting / weak signals
    candles = _candles_trend(n=60, step=0.02)
    feat = _feat(candles)
    tick = Tick("SOL-USD", feat.current_price, 0.01, 1000)
    vote = MomentumAgent().vote(tick, [c.close for c in candles], [], features=feat)
    assert vote.side == Side.HOLD or abs(vote.score or 0) < 0.5
    if vote.side == Side.HOLD:
        assert vote.confidence <= 0.6


def test_momentum_missing_indicators_still_votes():
    # Short history — some indicators missing
    candles = _candles_trend(n=12, step=0.3)
    feat = _feat(candles)
    tick = Tick("SOL-USD", feat.current_price, 0.3, 1000)
    vote = MomentumAgent().vote(tick, [c.close for c in candles], [], features=feat)
    assert vote.side in {Side.BUY, Side.SELL, Side.HOLD}
    assert "INFORMATIONAL_ONLY" in vote.inputs or vote.informational is not None


def test_mean_reversion_oversold_cluster():
    # Rise then sharp dump below bands
    up = _candles_trend(n=50, start=100, step=0.2)
    px = up[-1].close
    dump = []
    for i in range(25):
        px = px * 0.985
        dump.append(
            Candle(
                ts=up[-1].ts + (i + 1) * 300,
                open=px * 1.01,
                high=px * 1.01,
                low=px * 0.99,
                close=px,
                volume=3000,
            )
        )
    candles = up + dump
    feat = _feat(candles)
    # Force oversold-style values if BB/RSI not extreme enough
    if feat.bb_position is None or feat.bb_position > 0.2:
        feat.bb_position = 0.05
        feat.bb_lower = feat.current_price * 0.99
        feat.bb_upper = feat.current_price * 1.05
    if feat.rsi_14 is None or feat.rsi_14 > 35:
        feat.rsi_14 = 28.0
    if feat.vwap is None or feat.vwap <= feat.current_price:
        feat.vwap = feat.current_price * 1.02
    if feat.sma_20 is None or feat.sma_20 <= feat.current_price:
        feat.sma_20 = feat.current_price * 1.025
    # Neutral trend so filter does not dampen BUY
    feat.ema_20 = feat.current_price
    feat.ema_50 = feat.current_price
    tick = Tick("SOL-USD", feat.current_price, -1.0, 3000)
    vote = MeanReversionAgent().vote(tick, [c.close for c in candles], [], features=feat)
    assert vote.side == Side.BUY
    assert vote.score is not None and vote.score >= 0.35


def test_mean_reversion_overbought_cluster():
    candles = _candles_trend(n=70, step=0.35)
    feat = _feat(candles)
    feat.bb_position = 0.95
    feat.rsi_14 = 75.0
    feat.vwap = feat.current_price * 0.98
    feat.sma_20 = feat.current_price * 0.97
    feat.ema_20 = feat.current_price
    feat.ema_50 = feat.current_price
    tick = Tick("SOL-USD", feat.current_price, 1.0, 3000)
    vote = MeanReversionAgent().vote(tick, [c.close for c in candles], [], features=feat)
    assert vote.side == Side.SELL


def test_mean_reversion_trend_suppresses_counter_trend():
    candles = _candles_trend(n=80, step=0.4)
    feat = _feat(candles)
    # Overbought mean-reversion SELL evidence
    feat.bb_position = 0.92
    feat.rsi_14 = 72.0
    feat.vwap = feat.current_price * 0.985
    feat.sma_20 = feat.current_price * 0.98
    # Strong bullish trend
    feat.ema_20 = feat.current_price * 1.01
    feat.ema_50 = feat.current_price * 0.99
    tick = Tick("SOL-USD", feat.current_price, 0.5, 2000)
    vote = MeanReversionAgent().vote(tick, [c.close for c in candles], [], features=feat)
    # Either HOLD or reduced confidence / dampened score vs undamped
    feat2 = _feat(candles)
    feat2.bb_position = 0.92
    feat2.rsi_14 = 72.0
    feat2.vwap = feat.current_price * 0.985
    feat2.sma_20 = feat.current_price * 0.98
    feat2.ema_20 = feat.current_price
    feat2.ema_50 = feat.current_price
    undamped = MeanReversionAgent().vote(tick, [c.close for c in candles], [], features=feat2)
    if vote.side == Side.SELL and undamped.side == Side.SELL:
        assert abs(vote.score or 0) <= abs(undamped.score or 0) + 1e-9
        assert vote.confidence <= undamped.confidence + 0.05
    else:
        assert vote.side in {Side.HOLD, Side.SELL}


def test_volatility_high_atr_without_direction_hold():
    candles = _candles_trend(n=40, step=0.05)
    feat = _feat(candles)
    feat.atr_pct = 2.5
    feat.bb_width = 5.0
    feat.realized_volatility = 0.8
    feat.relative_volume = 1.7
    feat.price_slope_pct = 0.05
    feat.ema_20 = feat.current_price
    feat.ema_50 = feat.current_price
    tick = Tick("SOL-USD", feat.current_price, 0.05, 2000)
    vote = VolatilityAgent().vote(tick, [c.close for c in candles], [], features=feat)
    assert vote.side == Side.HOLD
    assert vote.volatility_regime in {"HIGH", "EXTREME"}


def test_volatility_normal_and_extreme():
    candles = _candles_trend(n=30, step=0.1)
    feat = _feat(candles)
    feat.atr_pct = 0.5
    feat.bb_width = 1.0
    feat.realized_volatility = 0.1
    feat.volume_spike = False
    normal = VolatilityAgent().vote(
        Tick("SOL-USD", feat.current_price, 0, 1), [c.close for c in candles], [], features=feat
    )
    assert normal.volatility_regime in {"LOW", "NORMAL"}

    feat.atr_pct = 4.0
    feat.bb_width = 8.0
    feat.realized_volatility = 1.5
    feat.volume_spike = True
    extreme = VolatilityAgent().vote(
        Tick("SOL-USD", feat.current_price, 0, 1), [c.close for c in candles], [], features=feat
    )
    assert extreme.volatility_regime == "EXTREME"


def test_volatility_spike_does_not_auto_trade():
    from trading_system.models import MarketEvent

    candles = _candles_trend(n=30, step=0.05)
    feat = _feat(candles)
    feat.atr_pct = 0.4
    events = [
        MarketEvent.create("spike_up", "SOL-USD", "spike", severity="warn"),
    ]
    tick = Tick("SOL-USD", feat.current_price, 1.5, 5000)
    vote = VolatilityAgent().vote(tick, [c.close for c in candles], events, features=feat)
    assert vote.side == Side.HOLD
    assert "price_spike" in (vote.informational or {})


def test_decision_rejects_symbol_mismatch():
    engine = DecisionEngine(min_confidence=0.2, min_execution_confidence=0.1)
    votes = [
        AgentVote("momentum", "M", "SOL-USD", Side.BUY, 0.8, "up", timeframe="5m", market_timestamp=100.0),
        AgentVote("mean_reversion", "R", "BTC-USD", Side.BUY, 0.8, "up", timeframe="5m", market_timestamp=100.0),
    ]
    d = engine.decide("SOL-USD", votes, 140, timeframe="5m", market_timestamp=100.0)
    assert d is not None
    assert all(v["symbol"] == "SOL-USD" for v in d.votes)
    assert d.engine["skipped_votes"]


def test_decision_rejects_timeframe_mismatch():
    votes = [
        AgentVote("ai_analyst", "AI", "SOL-USD", Side.BUY, 0.9, "up", timeframe="15m", market_timestamp=100.0),
        AgentVote("momentum", "M", "SOL-USD", Side.BUY, 0.8, "up", timeframe="5m", market_timestamp=100.0),
    ]
    kept, skipped = filter_compatible_votes("SOL-USD", votes, timeframe="5m", market_timestamp=100.0)
    assert len(kept) == 1
    assert kept[0].agent_id == "momentum"
    assert any("timeframe" in r for s in skipped for r in s["reasons"])


def test_decision_rejects_stale_snapshot():
    votes = [
        AgentVote("momentum", "M", "SOL-USD", Side.BUY, 0.8, "up", timeframe="5m", market_timestamp=100.0),
        AgentVote("mean_reversion", "R", "SOL-USD", Side.BUY, 0.7, "up", timeframe="5m", market_timestamp=9999.0),
    ]
    kept, skipped = filter_compatible_votes(
        "SOL-USD", votes, timeframe="5m", market_timestamp=100.0, tolerance_sec=60
    )
    assert len(kept) == 1
    assert skipped and "stale" in skipped[0]["reasons"][0]


def test_execution_confidence_gate_blocks_weak_fill():
    engine = DecisionEngine(min_confidence=0.45, min_execution_confidence=0.55)
    votes = [
        AgentVote("momentum", "M", "BTC-USD", Side.SELL, 0.80, "down"),
        AgentVote("mean_reversion", "R", "BTC-USD", Side.BUY, 0.69, "fade"),
        AgentVote("volatility", "V", "BTC-USD", Side.HOLD, 0.35, "calm"),
    ]
    decision = engine.decide("BTC-USD", votes, price=78000)
    assert decision is not None
    assert decision.side == Side.SELL
    assert decision.analytical_side == "SELL"
    # Conflicting votes → low calibrated confidence → not executable
    assert decision.confidence < 0.55
    assert decision.executed is False
    assert decision.engine.get("execution_gate")


def test_ai_cache_separates_symbol_and_timeframe():
    ai = AIMarketAnalyst()
    ai._cache["x"] = (0, AIResult("BUY", 0.5, "t", "API"))  # noqa: SLF001 — seed junk
    s1 = {"symbol": "BTC-USD", "timeframe": "5m", "candle_timestamp": 1000, "short_trend": "UP", "volume_state": "HIGH", "heuristic_votes": {}, "change_5m_pct": 0.5, "rsi_14": 55}
    s2 = {"symbol": "SOL-USD", "timeframe": "5m", "candle_timestamp": 1000, "short_trend": "UP", "volume_state": "HIGH", "heuristic_votes": {}, "change_5m_pct": 0.5, "rsi_14": 55}
    s3 = {"symbol": "BTC-USD", "timeframe": "1m", "candle_timestamp": 1000, "short_trend": "UP", "volume_state": "HIGH", "heuristic_votes": {}, "change_5m_pct": 0.5, "rsi_14": 55}
    s4 = {"symbol": "BTC-USD", "timeframe": "5m", "candle_timestamp": 2000, "short_trend": "UP", "volume_state": "HIGH", "heuristic_votes": {}, "change_5m_pct": 0.5, "rsi_14": 55}
    k1 = ai._cache_key(s1)  # noqa: SLF001
    k2 = ai._cache_key(s2)  # noqa: SLF001
    k3 = ai._cache_key(s3)  # noqa: SLF001
    k4 = ai._cache_key(s4)  # noqa: SLF001
    assert k1 != k2
    assert k1 != k3
    assert k1 != k4


def test_ai_compact_heuristic_context():
    votes = [
        AgentVote("momentum", "M", "SOL-USD", Side.BUY, 0.78, "up", score=0.56),
        AgentVote("mean_reversion", "R", "SOL-USD", Side.HOLD, 0.52, "flat", score=0.12),
        AgentVote(
            "volatility", "V", "SOL-USD", Side.HOLD, 0.70, "high", score=0.0, volatility_regime="HIGH"
        ),
    ]
    compact = AIMarketAnalyst.compact_heuristic_context(votes)
    assert compact["momentum"]["side"] == "BUY"
    assert compact["momentum"]["score"] == 0.56
    assert compact["volatility"]["regime"] == "HIGH"


def test_risk_monitor_only_blocks(monkeypatch):
    import trading_system.asset_config as ac

    monkeypatch.setattr(ac, "can_open_order", lambda *a, **k: (False, "SOL-USD במצב מעקב בלבד — ביצוע הזמנות חסום."))
    engine = DecisionEngine(min_confidence=0.2, min_execution_confidence=0.1)
    votes = [
        AgentVote("a", "A", "SOL-USD", Side.BUY, 0.9, "up"),
        AgentVote("b", "B", "SOL-USD", Side.BUY, 0.85, "up"),
    ]
    d = engine.decide("SOL-USD", votes, 140)
    assert d and d.executed
    risk = RiskEngine()
    verdict = risk.evaluate(
        decision=d,
        portfolio=Portfolio(cash=100000),
        price=140,
        symbol="SOL-USD",
        features={"atr_14": 4.0, "atr_pct": 2.8},
    )
    assert verdict.status == "BLOCKED"
    assert verdict.reason_code == "monitor_only"


def test_risk_insufficient_cash_blocks():
    engine = DecisionEngine(min_confidence=0.2, min_execution_confidence=0.1)
    d = engine.decide(
        "SOL-USD",
        [
            AgentVote("a", "A", "SOL-USD", Side.BUY, 0.9, "up"),
            AgentVote("b", "B", "SOL-USD", Side.BUY, 0.9, "up"),
        ],
        140,
    )
    assert d and d.executed
    risk = RiskEngine()
    verdict = risk.evaluate(
        decision=d,
        portfolio=Portfolio(cash=0.5),
        price=140,
        symbol="SOL-USD",
        features={"atr_14": 2.0},
    )
    assert verdict.status == "BLOCKED"
    assert verdict.reason_code == "insufficient_cash"


def test_risk_cooldown_blocks():
    import time

    engine = DecisionEngine(min_confidence=0.2, min_execution_confidence=0.1)
    d = engine.decide(
        "SOL-USD",
        [
            AgentVote("a", "A", "SOL-USD", Side.BUY, 0.9, "up"),
            AgentVote("b", "B", "SOL-USD", Side.BUY, 0.9, "up"),
        ],
        140,
    )
    risk = RiskEngine(fill_cooldown_sec=45)
    verdict = risk.evaluate(
        decision=d,
        portfolio=Portfolio(cash=100000),
        price=140,
        symbol="SOL-USD",
        features={},
        last_fill_ts=time.time() - 5,
    )
    assert verdict.status == "BLOCKED"
    assert verdict.reason_code == "cooldown"


def test_risk_exposure_limit_blocks(monkeypatch):
    import trading_system.asset_config as ac

    monkeypatch.setattr(ac, "can_open_order", lambda *a, **k: (False, "חשיפת התיק חורגת מהמגבלה."))
    engine = DecisionEngine(min_confidence=0.2, min_execution_confidence=0.1)
    d = engine.decide(
        "SOL-USD",
        [
            AgentVote("a", "A", "SOL-USD", Side.BUY, 0.9, "up"),
            AgentVote("b", "B", "SOL-USD", Side.BUY, 0.9, "up"),
        ],
        140,
    )
    risk = RiskEngine()
    verdict = risk.evaluate(
        decision=d,
        portfolio=Portfolio(cash=100000),
        price=140,
        symbol="SOL-USD",
        features={"atr_14": 3.0},
    )
    assert verdict.status == "BLOCKED"
    assert verdict.reason_code == "exposure_limit"


def test_risk_approved_includes_atr_levels(monkeypatch):
    import trading_system.asset_config as ac

    monkeypatch.setattr(ac, "can_open_order", lambda *a, **k: (True, ""))
    engine = DecisionEngine(min_confidence=0.2, min_execution_confidence=0.1)
    d = engine.decide(
        "SOL-USD",
        [
            AgentVote("a", "A", "SOL-USD", Side.BUY, 0.9, "up"),
            AgentVote("b", "B", "SOL-USD", Side.BUY, 0.9, "up"),
        ],
        140,
    )
    risk = RiskEngine()
    verdict = risk.evaluate(
        decision=d,
        portfolio=Portfolio(cash=100000),
        price=140,
        symbol="SOL-USD",
        features={"atr_14": 4.12, "atr_pct": 2.9},
    )
    assert verdict.status == "APPROVED"
    assert verdict.suggested_stop is not None
    assert verdict.risk_reward == 2.0
    assert verdict.allocation_pct == 0.02
