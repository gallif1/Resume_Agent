"""Performance/regression: WS payloads stay compact after indicator upgrade."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.agents import MomentumAgent
from trading_system.decision_engine import DecisionEngine
from trading_system.decision_log import _compact_agent_inputs, build_agent_entries
from trading_system.indicators.features import build_feature_snapshot
from trading_system.market_data.models import Candle
from trading_system.models import AgentVote, Side, Tick


def _candles(n: int = 80) -> list[Candle]:
    px = 100.0
    out = []
    for i in range(n):
        o, c = px, px + 0.3
        out.append(
            Candle(
                ts=1_700_000_000 + i * 300,
                open=o,
                high=max(o, c) * 1.001,
                low=min(o, c) * 0.999,
                close=c,
                volume=2000,
            )
        )
        px = c
    return out


def test_agent_public_dict_is_compact():
    candles = _candles()
    feat = build_feature_snapshot(symbol="SOL-USD", timeframe="5m", candles=candles)
    vote = MomentumAgent().vote(
        Tick("SOL-USD", feat.current_price, 0.4, 2000),
        [c.close for c in candles],
        [],
        features=feat,
    )
    # Simulate accidental bulky attachment (old behavior).
    vote.inputs["INFORMATIONAL_ONLY"] = {"blob": [{"n": i} for i in range(500)]}
    vote.informational = {"human_levels": [{"price": 1}] * 200}

    pub = vote.to_public_dict()
    raw = json.dumps(pub)
    assert "INFORMATIONAL_ONLY" not in raw
    assert "human_levels" not in raw
    assert len(raw) < 2500
    assert pub["agent_id"] == "momentum"
    assert "components" in pub


def test_decision_public_dict_slims_votes():
    engine = DecisionEngine(min_confidence=0.2, min_execution_confidence=0.1)
    votes = [
        AgentVote("a", "A", "SOL-USD", Side.BUY, 0.9, "up", timeframe="5m"),
        AgentVote("b", "B", "SOL-USD", Side.BUY, 0.85, "up", timeframe="5m"),
    ]
    d = engine.decide("SOL-USD", votes, 140, timeframe="5m")
    assert d is not None
    pub = d.to_public_dict()
    blob = json.dumps(pub)
    assert len(blob) < 8000
    assert pub["engine"].get("weights")


def test_compact_agent_inputs_strips_blobs():
    out = _compact_agent_inputs(
        {
            "score": 0.5,
            "INFORMATIONAL_ONLY": {"x": list(range(1000))},
            "data_quality": {"stale": False},
            "USED_FOR_DECISION": {"rsi_14": 55.0},
        }
    )
    assert "INFORMATIONAL_ONLY" not in out
    assert "data_quality" not in out
    assert out["USED_FOR_DECISION"]["rsi_14"] == 55.0
    entries = build_agent_entries(
        [
            {
                "agent_id": "momentum",
                "side": "BUY",
                "confidence": 0.8,
                "rationale": "x",
                "inputs": {"INFORMATIONAL_ONLY": {"huge": True}, "score": 1},
            }
        ],
        None,
    )
    assert "INFORMATIONAL_ONLY" not in entries[0]["inputs"]
