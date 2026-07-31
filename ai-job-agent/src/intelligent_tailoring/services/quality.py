"""Tailoring quality evaluation — multi-signal, profession-agnostic.

Does NOT use a single global similarity threshold as the sole quality measure.
"""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.services.similarity import blended_similarity, resume_section_text

_GENERIC_PHRASES = (
    "results-driven",
    "hard worker",
    "team player",
    "passionate about",
    "seeking a challenging",
    "excellent communication skills",
    "proven track record",
    "dynamic professional",
    "self-motivated",
    "go-getter",
)


def evaluate_tailoring_quality(
    *,
    tailored_resume: dict[str, Any],
    baseline_resume: dict[str, Any],
    strategy: dict[str, Any],
    evidence_map: list[dict[str, Any]],
    missed_evidence: dict[str, Any] | None = None,
    fact_scores: list[dict[str, Any]] | None = None,
    unsupported_claim_count: int = 0,
    change_log: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce TailoringQualityReport and decide whether regeneration is needed."""
    missed = missed_evidence or {}
    change_log = change_log or []

    # --- Requirement coverage ---
    hard = [
        e
        for e in evidence_map
        if e.get("importance") == "hard"
    ]
    covered = [
        e for e in hard if e.get("candidate_status") in ("MATCH", "PARTIAL")
    ]
    job_requirement_coverage = (
        round(len(covered) / len(hard), 4) if hard else 1.0
    )

    # --- Source evidence utilization ---
    high_value = [
        f for f in (fact_scores or []) if int(f.get("score") or 0) >= 50
    ]
    tailored_blob = " ".join(
        [
            resume_section_text(tailored_resume, "summary"),
            resume_section_text(tailored_resume, "experience"),
            resume_section_text(tailored_resume, "projects"),
            resume_section_text(tailored_resume, "skills"),
        ]
    ).lower()
    used_hv = 0
    for f in high_value:
        text = str(f.get("original_text") or "").lower()
        tokens = [t for t in re.findall(r"[a-z0-9\u0590-\u05ff]{4,}", text)]
        if tokens and sum(1 for t in tokens if t in tailored_blob) >= max(1, len(tokens) // 3):
            used_hv += 1
    high_value_fact_utilization = (
        round(used_hv / len(high_value), 4) if high_value else 1.0
    )

    additional = missed.get("additional_relevant_facts_found") or []
    source_evidence_utilization = round(
        max(0.0, 1.0 - min(1.0, len(additional) / 10.0)), 4
    )

    # --- Summary specificity ---
    summary = str(
        tailored_resume.get("professional_summary") or tailored_resume.get("summary") or ""
    )
    summary_focus = str(strategy.get("summary_focus") or "")
    summary_specificity = round(
        blended_similarity(summary, summary_focus) if summary_focus else 0.5,
        4,
    )
    # Boost if summary mentions strategy keywords / JD terms
    keyword_plan = strategy.get("keyword_plan") or strategy.get("keywords_to_insert") or []
    kw_hits = sum(1 for k in keyword_plan if str(k).lower() in summary.lower())
    summary_specificity = min(1.0, summary_specificity + 0.05 * kw_hits)

    # --- Section prioritization ---
    section_prioritization_score = _section_priority_score(tailored_resume, strategy)

    # --- Keyword alignment ---
    ats = list(strategy.get("keywords_to_insert") or []) + list(
        strategy.get("skills_to_emphasize") or []
    )
    if ats:
        hits = sum(1 for k in ats if str(k).lower() in tailored_blob)
        keyword_alignment_score = round(hits / len(ats), 4)
    else:
        keyword_alignment_score = 0.7

    # --- Truthfulness ---
    truthfulness_score = max(0.0, 1.0 - min(1.0, unsupported_claim_count * 0.15))

    # --- Generic content ---
    generic_hits = sum(1 for p in _GENERIC_PHRASES if p in summary.lower())
    generic_content_score = round(min(1.0, generic_hits / 3.0), 4)

    # --- Base similarity (informational, not sole gate) ---
    base_sim = blended_similarity(
        resume_section_text(tailored_resume, "summary"),
        resume_section_text(baseline_resume, "summary"),
    )
    # Title-only change detector
    title_only = _mostly_title_change(tailored_resume, baseline_resume, change_log)

    warnings: list[str] = []
    regeneration_required = False

    if high_value and high_value_fact_utilization < 0.25:
        # Warn only — forcing rewrite regen for utilization caused mock/LLM
        # exhaustion and is better handled by missed-evidence injection.
        warnings.append("Important high-value source evidence was under-utilized")
    # Missed evidence is injected into strategy before rewrite — warn but do not
    # auto-regen solely on count (avoids loops when facts were already promoted).
    if len(additional) >= 8:
        warnings.append(
            f"{len(additional)} overlooked relevant facts remain after promotion"
        )
    if generic_content_score >= 0.66 and (not summary or len(summary.split()) < 18):
        warnings.append("Summary is generic or too short for the target role")
        regeneration_required = True
    if unsupported_claim_count > 0:
        warnings.append(f"{unsupported_claim_count} unsupported claims present")
        regeneration_required = True
    if title_only and len(change_log) < 2:
        warnings.append("Result mainly changes the title without meaningful content shifts")
        regeneration_required = True
    if job_requirement_coverage < 0.35 and hard:
        warnings.append("Low coverage of hard job requirements despite available pathway")

    # Overall score (weighted)
    overall = int(
        round(
            100
            * (
                0.20 * job_requirement_coverage
                + 0.18 * high_value_fact_utilization
                + 0.12 * source_evidence_utilization
                + 0.15 * summary_specificity
                + 0.10 * section_prioritization_score
                + 0.10 * keyword_alignment_score
                + 0.10 * truthfulness_score
                + 0.05 * (1.0 - generic_content_score)
            )
        )
    )

    return {
        "overall_tailoring_score": max(0, min(100, overall)),
        "job_requirement_coverage": job_requirement_coverage,
        "source_evidence_utilization": source_evidence_utilization,
        "high_value_fact_utilization": high_value_fact_utilization,
        "summary_specificity": summary_specificity,
        "section_prioritization_score": section_prioritization_score,
        "keyword_alignment_score": keyword_alignment_score,
        "truthfulness_score": truthfulness_score,
        "unsupported_claim_count": unsupported_claim_count,
        "generic_content_score": generic_content_score,
        "base_resume_similarity": round(base_sim, 4),
        "cross_job_similarity": {},  # filled by caller when comparing jobs
        "warnings": warnings,
        "regeneration_required": regeneration_required,
        "title_only_change": title_only,
    }


def _section_priority_score(resume: dict[str, Any], strategy: dict[str, Any]) -> float:
    skills = [str(s).lower() for s in (resume.get("skills") or [])]
    emphasize = [str(s).lower() for s in (strategy.get("skills_to_emphasize") or [])]
    if not emphasize or not skills:
        return 0.6
    # Check if emphasized skills appear early
    top = " ".join(skills[:3])
    hits = sum(1 for e in emphasize[:5] if e and e in top)
    return round(min(1.0, 0.4 + 0.15 * hits), 4)


def _mostly_title_change(
    tailored: dict[str, Any],
    baseline: dict[str, Any],
    change_log: list[dict[str, Any]],
) -> bool:
    """True when almost nothing but the title/summary headline changed."""
    substantive = [
        c
        for c in change_log
        if str(c.get("original_text") or "").strip()
        and str(c.get("new_text") or "").strip()
        and str(c.get("original_text")) != str(c.get("new_text"))
    ]
    if len(substantive) >= 3:
        return False
    exp_sim = blended_similarity(
        resume_section_text(tailored, "experience"),
        resume_section_text(baseline, "experience"),
    )
    skills_sim = blended_similarity(
        resume_section_text(tailored, "skills"),
        resume_section_text(baseline, "skills"),
    )
    return exp_sim > 0.92 and skills_sim > 0.92


def should_regenerate_for_quality(report: dict[str, Any], attempt: int, max_attempts: int = 1) -> bool:
    if attempt >= max_attempts:
        return False
    return bool(report.get("regeneration_required"))
