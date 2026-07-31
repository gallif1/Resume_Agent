"""Intelligent Resume Tailoring — multi-agent, evidence-based CV generation."""

from __future__ import annotations

from intelligent_tailoring.pipeline import (
    IntelligentTailorError,
    apply_change_decisions,
    detect_language,
    regenerate_section,
    run_intelligent_tailoring,
    run_intelligent_tailoring_agents,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION

__all__ = [
    "PIPELINE_VERSION",
    "IntelligentTailorError",
    "apply_change_decisions",
    "detect_language",
    "regenerate_section",
    "run_intelligent_tailoring",
    "run_intelligent_tailoring_agents",
]
