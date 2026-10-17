"""Regression: compact agent inputs + copy-safe decision log export."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["TRADING_USE_SIMULATED_FEED"] = "true"
os.environ["AI_ENABLED"] = "false"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from trading_system.decision_log import (
    _compact_agent_inputs,
    build_agent_entries,
    format_decision_logs_text,
)


def test_compact_agent_inputs_strips_informational_blobs():
    raw = {
        "score": 0.56,
        "USED_FOR_DECISION": {"rsi_14": 61.4, "ema_20": 180.0, "meta": {"x": 1}},
        "INFORMATIONAL_ONLY": {"chart_indicators_informational": {"rsi_14": 61}},
        "data_quality": {"stale": False, "missing_fields": ["x"] * 50},
        "timeframe_signals": {"1m_trend": "UP"},
        "human_levels": [{"price": 1}] * 20,
    }
    out = _compact_agent_inputs(raw)
    assert "INFORMATIONAL_ONLY" not in out
    assert "data_quality" not in out
    assert "timeframe_signals" not in out
    assert "human_levels" not in out
    assert out["score"] == 0.56
    assert out["USED_FOR_DECISION"]["rsi_14"] == 61.4
    # nested dict values inside USED_FOR_DECISION become short strings
    assert isinstance(out["USED_FOR_DECISION"]["meta"], str)


def test_build_agent_entries_keeps_logs_small():
    votes = [
        {
            "agent_id": "momentum",
            "agent_name": "M",
            "side": "BUY",
            "confidence": 0.8,
            "rationale": "up",
            "inputs": {
                "score": 0.5,
                "INFORMATIONAL_ONLY": {"blob": [{"n": i} for i in range(200)]},
            },
            "ts": 1.0,
        }
    ]
    entries = build_agent_entries(votes, None)
    assert entries[0]["inputs"].get("score") == 0.5
    assert "INFORMATIONAL_ONLY" not in entries[0]["inputs"]
    text = format_decision_logs_text(
        [
            {
                "id": "1",
                "timestamp": 1_700_000_000,
                "symbol": "SOL-USD",
                "kind": "NEW_DECISION",
                "signal": {"action": "BUY", "agents": entries},
                "market": {"price": 140, "change_1m_pct": 0.1},
                "decision": {
                    "action": "BUY",
                    "final_confidence": 0.7,
                    "vote_counts": {},
                    "weights": {},
                    "confidence_debug": {},
                },
                "execution": {"status": "BLOCKED"},
            }
        ],
        limit=1,
    )
    assert "SOL-USD" in text
    assert "blob" not in text
