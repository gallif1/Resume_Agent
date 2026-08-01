"""Multi-agent Resume Intelligence Platform.

Each agent has exactly one responsibility, typed input/output schemas,
and no hidden side effects. The orchestrator wires agents with structured
objects — not free-form prompts between stages.
"""

from __future__ import annotations

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.orchestrator import (
    AGENT_CATALOG,
    LEGACY_AGENT_CATALOG,
    build_agent_instances,
    run_multi_agent_pipeline,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION

__all__ = [
    "AGENT_CATALOG",
    "LEGACY_AGENT_CATALOG",
    "Agent",
    "AgentContext",
    "AgentResult",
    "PIPELINE_VERSION",
    "build_agent_instances",
    "run_multi_agent_pipeline",
]
