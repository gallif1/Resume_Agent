"""Regression tests for tailor latency gates (fewer sequential LLM rounds)."""

from __future__ import annotations

from intelligent_tailoring.refine_gates import (
    should_run_hm_refine,
    should_run_post_polish_refine,
)
from intelligent_tailoring.stages.merged_writing import MAX_INTERNAL_REPAIR_PASSES


def test_internal_repair_passes_capped_for_latency():
    assert MAX_INTERNAL_REPAIR_PASSES == 1


def test_hm_refine_skipped_for_mediocre_but_usable_draft():
    assert not should_run_hm_refine(
        overall_fit=68,
        overall_score=72,
        interview_probability=68,
        twenty_second_screen=68,
        weakest_sections=["summary"],
    )


def test_hm_refine_runs_only_on_severe_gap_with_weak_sections():
    assert should_run_hm_refine(
        overall_fit=55,
        overall_score=60,
        interview_probability=55,
        twenty_second_screen=50,
        weakest_sections=["experience", "summary"],
    )
    assert not should_run_hm_refine(
        overall_fit=50,
        overall_score=50,
        interview_probability=50,
        twenty_second_screen=50,
        weakest_sections=[],
    )


def test_post_polish_refine_skips_when_refine_already_used():
    assert not should_run_post_polish_refine(
        interview_probability=40,
        twenty_second_screen=40,
        quality_passed=False,
        weak_sections=["summary"],
        llm_refine_already_used=True,
    )


def test_post_polish_refine_requires_severe_gap():
    assert not should_run_post_polish_refine(
        interview_probability=68,
        twenty_second_screen=68,
        quality_passed=True,
        weak_sections=["summary"],
        llm_refine_already_used=False,
    )
    assert should_run_post_polish_refine(
        interview_probability=50,
        twenty_second_screen=50,
        quality_passed=False,
        weak_sections=["summary"],
        llm_refine_already_used=False,
    )


def test_source_evidence_build_is_cached():
    from match_tailor_service import SourceEvidence, _cached_source_evidence

    _cached_source_evidence.cache_clear()
    text = "Python FastAPI PostgreSQL React TypeScript " * 20
    a = SourceEvidence.build(text)
    b = SourceEvidence.build(text)
    assert a is b
    assert _cached_source_evidence.cache_info().hits >= 1
