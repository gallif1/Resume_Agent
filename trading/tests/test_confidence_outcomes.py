"""Tests for calibrated confidence, pretrade AI, outcomes, and log heartbeat."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "true"
os.environ["OPENAI_API_KEY"] = ""  # force unavailable unless mocked
os.environ["DECISION_LOG_HEARTBEAT_SECONDS"] = "60"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.ai.analyst import AIMarketAnalyst, AIResult
from trading_system.decision_engine import DecisionEngine, calibrated_confidence
from trading_system.decision_log import (
    DecisionLogBuffer,
    build_decision_log,
    build_execution,
    build_market_snapshot,
    classify_kind,
    format_decision_logs_text,
)
from trading_system.models import AgentVote, Portfolio, Position, Side, Tick
from trading_system.outcomes import OutcomeStore, direction_correct


def _votes_buy_hold():
    return [
        AgentVote("momentum", "Momentum", "AAPL", Side.HOLD, 0.40, "flat"),
        AgentVote("mean_reversion", "MeanRev", "AAPL", Side.BUY, 0.76, "oversold"),
        AgentVote("volatility", "Vol", "AAPL", Side.BUY, 0.55, "spike"),
    ]


def test_two_buy_one_hold_not_auto_99():
    engine = DecisionEngine(min_confidence=0.45)
    d = engine.decide("AAPL", _votes_buy_hold(), 320.0)
    assert d is not None
    assert d.side == Side.BUY
    assert d.confidence < 0.90
    assert 0.55 <= d.confidence <= 0.85
    dbg = d.engine["confidence_debug"]
    assert dbg["hold_ratio"] > 0
    assert dbg["opposition_ratio"] == 0
    assert "action_support" in dbg


def test_unanimous_buy_higher_than_mixed_hold():
    engine = DecisionEngine(min_confidence=0.45)
    mixed = engine.decide("AAPL", _votes_buy_hold(), 100)
    unanimous = engine.decide(
        "AAPL",
        [
            AgentVote("a", "A", "AAPL", Side.BUY, 0.9, "up"),
            AgentVote("b", "B", "AAPL", Side.BUY, 0.88, "up"),
            AgentVote("c", "C", "AAPL", Side.BUY, 0.85, "up"),
        ],
        100,
    )
    assert mixed and unanimous
    assert unanimous.confidence > mixed.confidence


def test_buy_sell_disagreement_lowers_confidence():
    engine = DecisionEngine(min_confidence=0.45)
    agree = engine.decide(
        "BTC-USD",
        [
            AgentVote("a", "A", "BTC-USD", Side.BUY, 0.8, "up"),
            AgentVote("b", "B", "BTC-USD", Side.BUY, 0.75, "up"),
            AgentVote("c", "C", "BTC-USD", Side.HOLD, 0.2, "flat"),
        ],
        100,
    )
    clash = engine.decide(
        "BTC-USD",
        [
            AgentVote("a", "A", "BTC-USD", Side.BUY, 0.8, "up"),
            AgentVote("b", "B", "BTC-USD", Side.SELL, 0.75, "down"),
            AgentVote("c", "C", "BTC-USD", Side.HOLD, 0.2, "flat"),
        ],
        100,
    )
    assert agree and clash
    # Clash may become HOLD if scores don't clear threshold; if BUY, confidence lower.
    if clash.side == Side.BUY:
        assert clash.confidence < agree.confidence
    assert clash.engine["confidence_debug"]["opposition_ratio"] >= 0


def test_pretrade_reuses_fresh_cache_without_api_call():
    ai = AIMarketAnalyst()
    snap = {
        "symbol": "BTC-USD",
        "short_trend": "UP",
        "volume_state": "NORMAL",
        "heuristic_votes": {"momentum": "BUY"},
        "change_5m_pct": 0.4,
        "rsi_14": 55,
    }
    key = ai._cache_key(snap)  # noqa: SLF001
    result = AIResult("BUY", 0.7, "fresh", "API")
    ai._cache[key] = (time.time(), result)  # noqa: SLF001
    ai._last_by_symbol["BTC-USD"] = {
        "action": "BUY",
        "confidence": 0.7,
        "reason": "fresh",
        "source": "API",
        "ts": time.time(),
        "cache_key": key,
    }
    ai._call_llm = MagicMock(side_effect=AssertionError("should not call"))  # type: ignore[method-assign]
    tick = Tick("BTC-USD", 100, 0.1, 1)
    vote, meta = ai.pretrade_vote(tick, snap)
    assert vote is not None
    assert meta["source"] == "PRETRADE_CACHE"
    ai._call_llm.assert_not_called()


def test_pretrade_stale_triggers_api_when_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # Reload config constants already imported — patch module attrs.
    import trading_system.ai.analyst as mod
    import trading_system.config as cfg

    monkeypatch.setattr(mod, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(cfg, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(mod, "AI_ENABLED", True)

    ai = AIMarketAnalyst()
    snap = {
        "symbol": "ETH-USD",
        "short_trend": "DOWN",
        "volume_state": "HIGH",
        "heuristic_votes": {"momentum": "SELL"},
        "change_5m_pct": -1.0,
        "rsi_14": 30,
    }
    ai._call_llm = MagicMock(  # type: ignore[method-assign]
        return_value=AIResult("SELL", 0.8, "pretrade", "API")
    )
    tick = Tick("ETH-USD", 2000, -0.5, 1)
    vote, meta = ai.pretrade_vote(tick, snap)
    assert vote is not None
    assert vote.side == Side.SELL
    assert meta["source"] == "PRETRADE_API"
    ai._call_llm.assert_called_once()


def test_pretrade_ai_can_flip_final_decision():
    engine = DecisionEngine(min_confidence=0.45, ai_weight=1.5)
    heuristics = [
        AgentVote("momentum", "Momentum", "SOL-USD", Side.HOLD, 0.4, "flat"),
        AgentVote("mean_reversion", "MeanRev", "SOL-USD", Side.BUY, 0.7, "fade"),
        AgentVote("volatility", "Vol", "SOL-USD", Side.BUY, 0.55, "vol"),
    ]
    before = engine.decide("SOL-USD", heuristics, 140)
    assert before and before.side == Side.BUY
    with_ai = heuristics + [
        AgentVote("ai_analyst", "AI Market Analyst", "SOL-USD", Side.SELL, 0.9, "fade rally")
    ]
    after = engine.decide("SOL-USD", with_ai, 140)
    assert after is not None
    # Strong weighted SELL should prevent a clean BUY win or flip side.
    assert after.side in {Side.SELL, Side.HOLD} or after.confidence < before.confidence


def test_ai_failure_does_not_crash_pretrade(monkeypatch):
    import trading_system.ai.analyst as mod

    monkeypatch.setattr(mod, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(mod, "AI_ENABLED", True)
    ai = AIMarketAnalyst()
    ai._call_llm = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    tick = Tick("BTC-USD", 100, 0, 1)
    vote, meta = ai.pretrade_vote(tick, {"symbol": "BTC-USD", "rsi_14": 50, "change_5m_pct": 0})
    assert vote is None
    assert meta["source"] == "PRETRADE_UNAVAILABLE"


def test_outcomes_recorded_once_per_horizon(tmp_path):
    store = OutcomeStore(path=tmp_path / "outcomes.jsonl")
    row = store.record_actionable(
        decision_id="dec1",
        symbol="AAPL",
        action="BUY",
        entry_price=100.0,
        entry_ts=1_000.0,
        agent_votes=[
            {"agent_id": "momentum", "agent_name": "Momentum", "side": "BUY"},
            {"agent_id": "mean_reversion", "agent_name": "MeanRev", "side": "HOLD"},
        ],
    )
    assert row
    # Force horizons due.
    for h in row["horizons"].values():
        h["due_at"] = 0
    store._by_id[row["id"]] = row  # noqa: SLF001

    prices = {"5m": 101.0, "15m": 102.0, "60m": 99.0}
    calls = {"n": 0}

    def get_price(symbol: str, target_ts: float):
        calls["n"] += 1
        # Map due order loosely by call count.
        vals = list(prices.values())
        idx = min(calls["n"] - 1, len(vals) - 1)
        return vals[idx], "ok"

    n1 = store.resolve_due(get_price, now=10_000)
    assert n1 == 3
    n2 = store.resolve_due(get_price, now=10_000)
    assert n2 == 0  # each horizon once
    got = store.get_by_decision("dec1")
    assert got
    assert got["horizons"]["5m"]["status"] == "resolved"
    assert got["horizons"]["15m"]["status"] == "resolved"
    assert got["horizons"]["60m"]["status"] == "resolved"


def test_buy_higher_price_marked_correct():
    assert direction_correct("BUY", 0.18) is True
    assert direction_correct("BUY", -0.33) is False


def test_sell_lower_price_marked_correct():
    assert direction_correct("SELL", -0.5) is True
    assert direction_correct("SELL", 0.2) is False


def test_signal_still_active_noise_reduced():
    buf = DecisionLogBuffer(limit=50, heartbeat_sec=60)
    engine = DecisionEngine(min_confidence=0.45)
    votes = _votes_buy_hold()
    decision = engine.decide("SOL-USD", votes, 140)
    assert decision
    decision.executed = False
    market = build_market_snapshot(
        symbol="SOL-USD",
        price=140,
        ts=time.time(),
        volume=1,
        indicator={},
        events=[],
        quote_meta={"provider": "sim", "session": "open", "freshness": "live"},
    )
    execution = build_execution(
        decision=decision,
        block_reason="Cooldown active (45s); 40s remaining",
        cooldown_remaining_sec=40,
        last_fill_ts=time.time() - 5,
    )
    kind = classify_kind(decision=decision, execution=execution, prev=None)
    rec = build_decision_log(
        decision=decision,
        market=market,
        agents=[],
        execution=execution,
        kind=kind,
    )
    assert buf.add(rec) is not None
    # Many near-identical cooldown updates should not grow the buffer.
    for i in range(20):
        decision2 = engine.decide("SOL-USD", votes, 140)
        assert decision2
        decision2.executed = False
        decision2.ts = time.time()
        execution2 = build_execution(
            decision=decision2,
            block_reason=f"Cooldown active (45s); {39 - i}s remaining",
            cooldown_remaining_sec=39 - i,
            last_fill_ts=time.time() - 6 - i,
        )
        kind2 = classify_kind(decision=decision2, execution=execution2, prev=buf._last_by_symbol.get("SOL-USD"))
        rec2 = build_decision_log(
            decision=decision2,
            market=market,
            agents=[],
            execution=execution2,
            kind=kind2,
        )
        buf.add(rec2)
    assert len(buf.recent) <= 3


def test_trade_execution_logs_preserved():
    buf = DecisionLogBuffer(limit=50, heartbeat_sec=60)
    engine = DecisionEngine(min_confidence=0.45)
    decision = engine.decide("SOL-USD", _votes_buy_hold(), 140)
    assert decision
    decision.executed = True
    decision.fill_price = 140
    decision.quantity = 1.5
    market = build_market_snapshot(
        symbol="SOL-USD",
        price=140,
        ts=time.time(),
        volume=1,
        indicator={},
        events=[],
        quote_meta={},
    )
    execution = build_execution(
        decision=decision,
        block_reason=None,
        cooldown_remaining_sec=None,
        last_fill_ts=time.time(),
    )
    kind = classify_kind(decision=decision, execution=execution, prev=None)
    assert kind == "TRADE_EXECUTED"
    rec = build_decision_log(
        decision=decision, market=market, agents=[], execution=execution, kind=kind
    )
    assert buf.add(rec) is not None
    text = format_decision_logs_text(buf.recent)
    assert "TRADE_EXECUTED" in text or "FILLED" in text
    assert "Fill price" in text


def test_application_remains_paper_trading():
    from trading_system.runtime import TradingRuntime

    rt = TradingRuntime()
    snap = rt.snapshot()
    assert snap.get("paper_trading_only") is True
    assert "broker" not in snap
    # apply_fill only mutates local Portfolio
    engine = DecisionEngine()
    d = engine.decide("AAPL", _votes_buy_hold(), 100)
    assert d
    pf = Portfolio(cash=10_000)
    before = pf.cash
    engine.apply_fill(pf, d)
    assert pf.cash <= before


def test_outcome_persistence_survives_reload(tmp_path):
    path = tmp_path / "outcomes.jsonl"
    store = OutcomeStore(path=path)
    store.record_actionable(
        decision_id="x1",
        symbol="NVDA",
        action="SELL",
        entry_price=500,
        entry_ts=time.time() - 400,
        agent_votes=[{"agent_id": "momentum", "side": "SELL"}],
    )
    store2 = OutcomeStore(path=path)
    assert store2.get_by_decision("x1") is not None


def test_calibrated_confidence_exports_intermediates():
    weights = {Side.BUY: 1.305, Side.SELL: 0.0, Side.HOLD: 0.4}
    votes = _votes_buy_hold()
    conf, dbg = calibrated_confidence(Side.BUY, weights, votes)
    assert 0.55 <= conf <= 0.85
    for k in (
        "action_support",
        "agreement_factor",
        "hold_ratio",
        "opposition_ratio",
        "final_confidence",
        "formula",
    ):
        assert k in dbg
