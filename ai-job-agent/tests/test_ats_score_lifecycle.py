"""ATS / job-fit score lifecycle — original vs final tailored resume."""

from __future__ import annotations

from intelligent_tailoring.interview_philosophy import build_generation_report
from intelligent_tailoring.stages.ats_scoring import (
    build_score_breakdown,
    rescore_after_tailoring,
    score_from_evidence_map,
)


def _evidence():
    return [
        {
            "requirement": "React",
            "candidate_status": "MATCH",
            "inference_category": "Explicit",
            "priority": "required",
            "evidence": "Built React dashboards",
        },
        {
            "requirement": "TypeScript",
            "candidate_status": "MISSING",
            "inference_category": "Unsupported",
            "priority": "required",
            "evidence": "",
        },
        {
            "requirement": "REST API",
            "candidate_status": "PARTIAL",
            "inference_category": "Explicit",
            "priority": "preferred",
            "evidence": "Integrated REST endpoints",
        },
    ]


def test_original_score_from_evidence_map():
    scoring = score_from_evidence_map(_evidence(), job_title="Frontend Engineer")
    assert "realistic_match_score" in scoring
    assert 0 <= int(scoring["realistic_match_score"]) <= 100


def test_final_score_uses_final_resume_text_not_skills_only():
    evidence = _evidence()
    original = score_from_evidence_map(evidence, job_title="Frontend Engineer")
    original_score = int(original["realistic_match_score"])

    # Keyword appears in experience bullets (final resume), not only skills list.
    tailored = {
        "professional_summary": "Frontend engineer with React delivery.",
        "skills": ["JavaScript"],
        "experience": [
            {
                "title": "Developer",
                "bullets": ["Shipped React features and REST API integrations"],
            }
        ],
    }
    rescored = rescore_after_tailoring(
        evidence_map=evidence,
        tailored_resume=tailored,
        original_resume_text="React REST API JavaScript dashboards",
        job_title="Frontend Engineer",
        original_score=original_score,
        improved_because=["React experience was emphasized"],
    )
    assert rescored["scored_from"] == "final_validated_tailored_resume"
    assert "score_breakdown" in rescored
    bd = rescored["score_breakdown"]
    assert bd["calculation_status"] == "complete"
    assert bd["original_score"] == original_score
    assert bd["tailored_score"] == rescored["realistic_match_score"]
    assert bd["score_delta"] == bd["tailored_score"] - bd["original_score"]
    still = " ".join(bd.get("still_missing") or [])
    assert "TypeScript" in still
    assert bd["ats_keyword_bonus"] >= 1
    assert rescored["scored_from"] == "final_validated_tailored_resume"


def test_score_does_not_invent_unsupported_requirements():
    evidence = _evidence()
    tailored = {
        "skills": ["React", "TypeScript"],  # TypeScript not in source — no bonus path
        "experience": [],
    }
    rescored = rescore_after_tailoring(
        evidence_map=evidence,
        tailored_resume=tailored,
        original_resume_text="React REST API",  # no TypeScript in source
        job_title="Frontend Engineer",
        original_score=50,
    )
    # Bonus only for keywords present in BOTH source and tailored content.
    assert "TypeScript" not in (rescored.get("score_breakdown") or {}).get(
        "improved_because", []
    )
    bd = rescored["score_breakdown"]
    missing_all = (
        bd.get("missing_required_requirements")
        or []
    ) + (bd.get("still_missing") or []) + (bd.get("unsupported_requirements") or [])
    assert any("TypeScript" in m for m in missing_all)
    # Listing TypeScript in skills must not remove it from unsupported gaps.
    assert "TypeScript" in " ".join(bd.get("unsupported_requirements") or [])


def test_build_score_breakdown_shape():
    bd = build_score_breakdown(
        original_score=64,
        tailored_score=76,
        evidence_map=_evidence(),
        scoring=score_from_evidence_map(_evidence()),
        ats_keyword_bonus=2,
        improved_because=["React emphasized"],
    )
    assert bd["score_delta"] == 12
    assert bd["calculation_status"] == "complete"
    assert bd["score_version"]
    assert isinstance(bd["still_missing"], list)


def test_generation_report_includes_score_breakdown():
    report = build_generation_report(
        result={
            "evidence_map": _evidence(),
            "inferred_competencies": [],
            "change_log": [{"section": "summary"}],
            "writing_report": {},
            "recruiter_review": {"would_interview": True},
            "hiring_manager_feedback": {"overall_fit": 70},
            "tailoring_strategy": {"top_interview_reasons": ["React"]},
            "pipeline_version": "multi_agent_v1_4",
            "original_match_score": 64,
            "tailored_match_score": 72,
            "score_breakdown": {
                "original_score": 64,
                "tailored_score": 72,
                "score_delta": 8,
                "calculation_status": "complete",
            },
            "missing_requirements": ["TypeScript"],
        },
        elapsed_seconds=38,
    )
    assert report["score_breakdown"]["tailored_score"] == 72
    assert report["score_breakdown"]["original_score"] == 64
    assert report["overall_progress"] == 100
    assert report["agents_total"] == 4
