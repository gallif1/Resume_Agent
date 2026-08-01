"""Stage 10 — ATS / relevance scoring before and after tailoring (deterministic)."""

from __future__ import annotations

from typing import Any

from match_tailor_service import (
    cap_for_unmet_core_count,
    compute_rubric_scores,
    unmet_core_requirements,
)
from intelligent_tailoring.stages.evidence_mapping import evidence_status_for_scoring

SCORE_VERSION = "final_resume_v1"


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


def _tailored_resume_text(tailored_resume: dict[str, Any]) -> str:
    """Flatten final resume content (excluding layout-only markup)."""
    parts: list[str] = []
    summary = tailored_resume.get("professional_summary") or tailored_resume.get(
        "summary"
    )
    if summary:
        parts.append(str(summary))
    skills = tailored_resume.get("skills") or []
    if isinstance(skills, list):
        parts.extend(str(s) for s in skills)
    elif skills:
        parts.append(str(skills))
    for exp in tailored_resume.get("experience") or []:
        if not isinstance(exp, dict):
            parts.append(str(exp))
            continue
        for key in ("title", "company", "description", "summary"):
            if exp.get(key):
                parts.append(str(exp[key]))
        for b in exp.get("bullets") or exp.get("highlights") or []:
            parts.append(str(b))
    for proj in tailored_resume.get("projects") or []:
        if not isinstance(proj, dict):
            parts.append(str(proj))
            continue
        for key in ("name", "title", "description"):
            if proj.get(key):
                parts.append(str(proj[key]))
        for b in proj.get("bullets") or proj.get("highlights") or []:
            parts.append(str(b))
    return "\n".join(parts)


def _pct(matched: int, total: int) -> int | None:
    if total <= 0:
        return None
    return int(round(100.0 * matched / total))


def build_score_breakdown(
    *,
    original_score: int,
    tailored_score: int,
    evidence_map: list[dict[str, Any]],
    scoring: dict[str, Any],
    ats_keyword_bonus: int = 0,
    resume_version_id: str | int | None = None,
    improved_because: list[str] | None = None,
    calculation_status: str = "complete",
) -> dict[str, Any]:
    """Structured score payload for the live generation UI."""
    hard = list(scoring.get("hard_requirements") or [])
    soft = list(scoring.get("soft_requirements") or [])
    hard_match = sum(
        1
        for r in hard
        if isinstance(r, dict) and r.get("candidate_status") in ("MATCH", "PARTIAL")
    )
    soft_match = sum(
        1
        for r in soft
        if isinstance(r, dict) and r.get("candidate_status") in ("MATCH", "PARTIAL")
    )
    def _req_name(item: dict[str, Any]) -> str:
        return str(
            item.get("requirement") or item.get("text") or ""
        ).strip()

    missing_required = [
        _req_name(r)
        for r in hard
        if isinstance(r, dict) and r.get("candidate_status") == "MISSING"
    ]
    # Also honor evidence-map priorities when the scoring bucket put all
    # requirements into soft (common for lightweight fixtures / unknown ontology).
    for e in evidence_map:
        if not isinstance(e, dict) or e.get("candidate_status") != "MISSING":
            continue
        name = _req_name(e)
        if not name:
            continue
        pri = str(e.get("priority") or "").lower()
        if pri in ("required", "must", "hard", "core"):
            missing_required.append(name)
    missing_required = list(dict.fromkeys(m for m in missing_required if m))

    missing_preferred = [
        _req_name(r)
        for r in soft
        if isinstance(r, dict) and r.get("candidate_status") == "MISSING"
    ]
    missing_preferred = [
        m for m in dict.fromkeys(missing_preferred) if m and m not in missing_required
    ]
    unsupported = [
        _req_name(e)
        for e in evidence_map
        if isinstance(e, dict) and e.get("candidate_status") == "MISSING"
    ]
    unsupported = [u for u in dict.fromkeys(unsupported) if u][:12]

    evidence_strong = sum(
        1
        for e in evidence_map
        if isinstance(e, dict)
        and e.get("candidate_status") in ("MATCH", "PARTIAL")
        and e.get("inference_category") in ("Explicit", "Strongly Inferred", None)
    )
    evidence_total = len([e for e in evidence_map if isinstance(e, dict)]) or 1

    hard_score = scoring.get("hard_score_pct")
    soft_score = scoring.get("soft_score_pct")
    if hard_score is None:
        hard_score = _pct(hard_match, len(hard) or 1)
    if soft_score is None:
        soft_score = _pct(soft_match, len(soft) or 1)

    # Keyword alignment: base coverage plus small surfaced-keyword bonus (max +5).
    base_align = int(hard_score or 0)
    ats_align = min(100, base_align + max(0, int(ats_keyword_bonus)) * 4)

    return {
        "original_score": int(original_score),
        "tailored_score": int(tailored_score),
        "score_delta": int(tailored_score) - int(original_score),
        "requirements_coverage": int(hard_score or 0),
        "ats_keyword_alignment": ats_align,
        "evidence_strength": int(round(100.0 * evidence_strong / evidence_total)),
        "role_relevance": int(soft_score or 0),
        "seniority_fit": scoring.get("seniority_fit"),
        "missing_required_requirements": missing_required[:12],
        "missing_preferred_requirements": missing_preferred[:12],
        "unsupported_requirements": unsupported,
        "still_missing": (missing_required + missing_preferred)[:12],
        "improved_because": list(improved_because or [])[:6],
        "calculation_status": calculation_status,
        "score_version": SCORE_VERSION,
        "resume_version_id": resume_version_id,
        "ats_keyword_bonus": int(ats_keyword_bonus),
    }


def rescore_after_tailoring(
    *,
    evidence_map: list[dict[str, Any]],
    tailored_resume: dict[str, Any],
    original_resume_text: str,
    job_title: str = "",
    original_score: int | None = None,
    improved_because: list[str] | None = None,
    resume_version_id: str | int | None = None,
) -> dict[str, Any]:
    """Recompute score after tailoring from the final validated resume.

    Tailoring cannot invent new evidence, so the score is still driven by the
    evidence map. Presentation quality does not inflate the honest match score.
    A small ATS keyword coverage bump (max +5) is allowed only for keywords that
    already appear in the original resume and were surfaced in the final
    tailored resume content (not layout markup).
    """
    base = score_from_evidence_map(evidence_map, job_title=job_title)
    tailored_text = _tailored_resume_text(tailored_resume).lower()
    source_l = (original_resume_text or "").lower()
    bonus = 0
    for entry in evidence_map:
        if entry.get("inference_category") != "Explicit":
            continue
        req = str(entry.get("requirement") or "").strip()
        if not req:
            continue
        req_l = req.lower()
        if req_l in source_l and req_l in tailored_text:
            bonus += 1
    bonus = min(5, bonus)
    score = min(100, int(base["realistic_match_score"]) + bonus)
    orig = (
        int(original_score)
        if original_score is not None
        else int(base["realistic_match_score"])
    )
    breakdown = build_score_breakdown(
        original_score=orig,
        tailored_score=score,
        evidence_map=evidence_map,
        scoring=base,
        ats_keyword_bonus=bonus,
        resume_version_id=resume_version_id,
        improved_because=improved_because,
        calculation_status="complete",
    )
    return {
        **base,
        "realistic_match_score": score,
        "ats_keyword_bonus": bonus,
        "score_breakdown": breakdown,
        "scored_from": "final_validated_tailored_resume",
    }
