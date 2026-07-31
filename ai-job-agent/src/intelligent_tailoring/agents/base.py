"""Shared agent protocol — modular, testable, side-effect free."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

InT = TypeVar("InT")
OutT = TypeVar("OutT")


@dataclass
class AgentContext:
    """Shared run context (cache flags, language). Never holds free-form prompts."""

    use_cache: bool = True
    language: str = "en"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult(Generic[OutT]):
    """Uniform agent return envelope."""

    agent_id: str
    output: OutT
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = self.output
        serialized = out.to_dict() if hasattr(out, "to_dict") else out
        return {
            "agent_id": self.agent_id,
            "output": serialized,
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
        }


class Agent(ABC, Generic[InT, OutT]):
    """Base class for every specialist agent.

    Contract:
    - Exactly one responsibility (documented on the subclass)
    - Deterministic input/output schemas
    - No hidden side effects (no DB writes, no file mutation)
    - Independently testable via ``run(input, context)``
    """

    agent_id: str = "base"
    responsibility: str = ""

    @abstractmethod
    def run(self, payload: InT, context: AgentContext | None = None) -> AgentResult[OutT]:
        """Execute the agent and return a typed result."""

    def __call__(
        self, payload: InT, context: AgentContext | None = None
    ) -> AgentResult[OutT]:
        return self.run(payload, context)
