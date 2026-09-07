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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d


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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d


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
