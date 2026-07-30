"""Modular tailoring services for deep job-family-specific resume tailoring."""

from intelligent_tailoring.services.job_analyzer import analyze_job
from intelligent_tailoring.services.resume_analyzer import analyze_resume
from intelligent_tailoring.services.resume_rebuilder import rebuild_resume_structure
from intelligent_tailoring.services.resume_rewriter import rewrite_resume_with_strategy
from intelligent_tailoring.services.resume_scorer import score_resume_content
from intelligent_tailoring.services.resume_validator import validate_tailoring_depth
from intelligent_tailoring.services.missed_evidence import find_missed_evidence
from intelligent_tailoring.services.quality import evaluate_tailoring_quality
from intelligent_tailoring.services.tailoring_reporter import build_tailoring_report
from intelligent_tailoring.services.tailoring_strategy_builder import build_tailoring_strategy
from intelligent_tailoring.knowledge_base import build_knowledge_base

__all__ = [
    "analyze_job",
    "analyze_resume",
    "build_knowledge_base",
    "build_tailoring_strategy",
    "score_resume_content",
    "rebuild_resume_structure",
    "rewrite_resume_with_strategy",
    "validate_tailoring_depth",
    "find_missed_evidence",
    "evaluate_tailoring_quality",
    "build_tailoring_report",
]
