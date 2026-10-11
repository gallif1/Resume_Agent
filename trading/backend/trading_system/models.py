"""Shared dataclasses / event payloads for the trading system."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
import time
import uuid


class SystemState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Tick:
    symbol: str
    price: float
    change_pct: float
    volume: float
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketEvent:
    id: str
    kind: str
    symbol: str
    message: str
    severity: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    @staticmethod
    def create(
        kind: str,
        symbol: str,
        message: str,
        severity: str = "info",
        **payload: Any,
    ) -> "MarketEvent":
        return MarketEvent(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            symbol=symbol,
            message=message,
            severity=severity,
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentVote:
    agent_id: str
    agent_name: str
    symbol: str
    side: Side
    confidence: float
    rationale: str
    ts: float = field(default_factory=time.time)
    # Numeric inputs used by the rule (for transparent logs). Optional.
    inputs: dict[str, Any] = field(default_factory=dict)
    # Explicit analysis context — must match other votes in a decision cycle.
    timeframe: str = ""
    market_timestamp: float | None = None
    score: float | None = None
    # Component contributions actually used for the decision (−1..+1).
    components: dict[str, Any] = field(default_factory=dict)
    # Explicit split: used_for_decision vs informational_only.
    used_for_decision: dict[str, Any] = field(default_factory=dict)
    informational: dict[str, Any] = field(default_factory=dict)
    volatility_regime: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d

    def to_public_dict(self) -> dict[str, Any]:
        """Compact payload for WebSocket / UI — avoids megabyte ticks."""
        used = _compact_scalar_map(self.used_for_decision or self.inputs.get("USED_FOR_DECISION"))
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "symbol": self.symbol,
            "side": self.side.value,
            "confidence": round(float(self.confidence), 4),
            "rationale": (self.rationale or "")[:280],
            "ts": self.ts,
            "timeframe": self.timeframe or "",
            "market_timestamp": self.market_timestamp,
            "score": self.score,
            "components": {
                k: round(float(v), 4)
                for k, v in (self.components or {}).items()
                if isinstance(v, (int, float))
            },
            "used_for_decision": used,
            "volatility_regime": self.volatility_regime,
            # Tiny inputs for backward-compatible UI formatters.
            "inputs": {
                "score": self.score,
                "USED_FOR_DECISION": used,
                "source": (self.inputs or {}).get("source"),
                "skip_reason": (self.inputs or {}).get("skip_reason"),
            },
        }


def _compact_scalar_map(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, val in list(raw.items())[:16]:
        if isinstance(val, (str, int, float, bool)) or val is None:
            out[str(key)] = val if not isinstance(val, str) else val[:120]
        elif isinstance(val, (list, tuple)):
            out[str(key)] = list(val)[:4]
        else:
            out[str(key)] = str(val)[:80]
    return out


@dataclass
class Decision:
    id: str
    symbol: str
    side: Side
    confidence: float
    votes: list[dict[str, Any]]
    rationale: str
    executed: bool = False
    fill_price: float | None = None
    quantity: float | None = None
    ts: float = field(default_factory=time.time)
    # Structured Decision Engine explanation (weights, confidence math).
    engine: dict[str, Any] = field(default_factory=dict)
    timeframe: str = ""
    market_timestamp: float | None = None
    # Analytical recommendation kept when execution is gated to HOLD/BLOCKED.
    analytical_side: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d

    def to_public_dict(self) -> dict[str, Any]:
        """Decision payload for UI ticks — slim votes + engine summary."""
        engine = self.engine or {}
        slim_engine = {
            "vote_counts": engine.get("vote_counts"),
            "weights": engine.get("weights"),
            "action_score": engine.get("action_score"),
            "hold_score": engine.get("hold_score"),
            "threshold": engine.get("threshold"),
            "winning_action": engine.get("winning_action"),
            "min_execution_confidence": engine.get("min_execution_confidence"),
            "execution_gate": engine.get("execution_gate"),
            "analysis_timeframe": engine.get("analysis_timeframe") or self.timeframe,
            "risk": _slim_risk(engine.get("risk")),
            "confidence_debug": {
                k: (engine.get("confidence_debug") or {}).get(k)
                for k in (
                    "final_confidence",
                    "action_support",
                    "agreement_factor",
                    "hold_ratio",
                    "opposition_ratio",
                    "formula",
                )
            }
            if engine.get("confidence_debug")
            else {},
        }
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side.value,
            "confidence": self.confidence,
            "rationale": (self.rationale or "")[:320],
            "executed": self.executed,
            "fill_price": self.fill_price,
            "quantity": self.quantity,
            "ts": self.ts,
            "timeframe": self.timeframe,
            "market_timestamp": self.market_timestamp,
            "analytical_side": self.analytical_side,
            "votes": [
                v if not isinstance(v, dict) else _public_vote_dict(v)
                for v in (self.votes or [])
            ],
            "engine": slim_engine,
        }


def _slim_risk(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    keys = (
        "approved",
        "status",
        "reason",
        "reason_code",
        "allocation_pct",
        "atr",
        "atr_pct",
        "atr_risk_level",
        "suggested_stop",
        "suggested_take_profit",
        "risk_reward",
    )
    return {k: raw.get(k) for k in keys if k in raw}


def _public_vote_dict(v: dict[str, Any]) -> dict[str, Any]:
    used = _compact_scalar_map(v.get("used_for_decision") or (v.get("inputs") or {}).get("USED_FOR_DECISION"))
    comps = v.get("components") or {}
    return {
        "agent_id": v.get("agent_id"),
        "agent_name": v.get("agent_name"),
        "symbol": v.get("symbol"),
        "side": v.get("side"),
        "confidence": v.get("confidence"),
        "rationale": str(v.get("rationale") or "")[:280],
        "ts": v.get("ts"),
        "timeframe": v.get("timeframe") or "",
        "market_timestamp": v.get("market_timestamp"),
        "score": v.get("score"),
        "components": {
            k: comps[k]
            for k in list(comps)[:12]
            if isinstance(comps.get(k), (int, float))
        },
        "used_for_decision": used,
        "volatility_regime": v.get("volatility_regime"),
        "inputs": {
            "score": v.get("score"),
            "USED_FOR_DECISION": used,
            "source": (v.get("inputs") or {}).get("source"),
            "skip_reason": (v.get("inputs") or {}).get("skip_reason"),
        },
    }


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_price: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
        }
