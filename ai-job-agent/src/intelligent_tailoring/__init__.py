"""Intelligent Resume Tailoring — staged, evidence-based CV generation pipeline."""

from __future__ import annotations

from intelligent_tailoring.pipeline import (
    IntelligentTailorError,
    apply_change_decisions,
    detect_language,
    regenerate_section,
    run_intelligent_tailoring,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION

__all__ = [
    "PIPELINE_VERSION",
    "IntelligentTailorError",
    "apply_change_decisions",
    "detect_language",
    "regenerate_section",
    "run_intelligent_tailoring",
]
