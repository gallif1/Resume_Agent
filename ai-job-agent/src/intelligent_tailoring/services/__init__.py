"""Modular tailoring services for deep job-family-specific resume tailoring."""

from intelligent_tailoring.services.job_analyzer import analyze_job
from intelligent_tailoring.services.resume_analyzer import analyze_resume
from intelligent_tailoring.services.resume_rebuilder import rebuild_resume_structure
from intelligent_tailoring.services.resume_rewriter import rewrite_resume_with_strategy
from intelligent_tailoring.services.resume_scorer import score_resume_content
from intelligent_tailoring.services.resume_validator import validate_tailoring_depth
from intelligent_tailoring.services.tailoring_reporter import build_tailoring_report
from intelligent_tailoring.services.tailoring_strategy_builder import build_tailoring_strategy

__all__ = [
    "analyze_job",
    "analyze_resume",
    "build_tailoring_strategy",
    "score_resume_content",
    "rebuild_resume_structure",
    "rewrite_resume_with_strategy",
    "validate_tailoring_depth",
    "build_tailoring_report",
]
