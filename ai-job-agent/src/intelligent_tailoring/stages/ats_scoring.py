"""Stage 10 — ATS / relevance scoring before and after tailoring (deterministic)."""

from __future__ import annotations

from typing import Any

from match_tailor_service import (
    cap_for_unmet_core_count,
    compute_rubric_scores,
    unmet_core_requirements,
)
from intelligent_tailoring.stages.evidence_mapping import evidence_status_for_scoring


def score_from_evidence_map(
    evidence_map: list[dict[str, Any]],
    *,
    job_title: str = "",
) -> dict[str, Any]:
    """Reproducible match score via the existing hard/soft rubric + hard cap."""
    buckets = evidence_status_for_scoring(evidence_map)
    hard = buckets["hard_requirements"]
    soft = buckets["soft_requirements"]
    scoring = compute_rubric_scores(hard, soft)
    unmet = unmet_core_requirements(job_title, hard)
    cap = cap_for_unmet_core_count(len(unmet))
    composite = int(scoring.get("composite_score") or 0)
    final = composite if cap is None else min(composite, cap)
    return {
        **scoring,
        "realistic_match_score": final,
        "hard_cap_applied": cap is not None,
        "cap": cap,
        "unmet_core_requirements": unmet,
        "hard_requirements": hard,
        "soft_requirements": soft,
    }


def rescore_after_tailoring(
    *,
    evidence_map: list[dict[str, Any]],
    tailored_resume: dict[str, Any],
    original_resume_text: str,
    job_title: str = "",
) -> dict[str, Any]:
    """Recompute score after tailoring.

    Tailoring cannot invent new evidence, so the score is still driven by the
    evidence map. Presentation quality does not inflate the honest match score.
    A small ATS keyword coverage bump (max +5) is allowed only for keywords that
    already appear in the original resume and were surfaced in the tailored skills.
    """
    base = score_from_evidence_map(evidence_map, job_title=job_title)
    skills = tailored_resume.get("skills") or []
    skill_blob = " ".join(str(s) for s in skills).lower()
    source_l = (original_resume_text or "").lower()
    bonus = 0
    for entry in evidence_map:
        if entry.get("inference_category") != "Explicit":
            continue
        req = str(entry.get("requirement") or "").strip()
        if not req:
            continue
        if req.lower() in source_l and req.lower() in skill_blob:
            bonus += 1
    bonus = min(5, bonus)
    score = min(100, int(base["realistic_match_score"]) + bonus)
    return {
        **base,
        "realistic_match_score": score,
        "ats_keyword_bonus": bonus,
    }
