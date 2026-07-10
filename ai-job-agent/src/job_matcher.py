"""OpenAI-powered semantic job matching with rule-based fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ai_client import (
    VALID_ACTIONS,
    VALID_DECISIONS,
    call_openai_json,
    clamp_score,
    normalize_string_list,
    summarize_job_text,
)
from candidate_summary import build_candidate_summary
from rule_based_matcher import score_job_fallback

JOB_MATCH_SYSTEM = """You are an expert technical recruiter evaluating job fit.
Compare the candidate against ONE job posting and return structured JSON.

Return ONE JSON object:
{
  "match_score": 87,
  "decision": "HIGH_MATCH",
  "strengths": ["Python", "FastAPI", "SQL"],
  "missing_skills": ["Docker"],
  "recommended_action": "APPLY_NOW",
  "explanation": "2-4 sentences explaining fit, gaps, and recommendation"
}

Field rules:
- match_score: 0-100 overall fit for THIS specific job
- decision: one of HIGH_MATCH (80+), MEDIUM_MATCH (55-79), LOW_MATCH (30-54), REJECT (<30 or clearly wrong)
- recommended_action: APPLY_NOW, APPLY_IF_DESPERATE, or SKIP
- strengths: candidate strengths relevant to this job (0-8 items)
- missing_skills: gaps for this specific role (0-6 items)
- Be realistic about seniority, domain, and experience level
- Penalize senior/lead roles for junior candidates
- Return valid JSON only"""


@dataclass
class JobMatchResult:
    match_score: int
    match_reason: str
    match_method: str
    ai_decision: str | None = None
    ai_strengths: list[str] = field(default_factory=list)
    ai_missing_skills: list[str] = field(default_factory=list)
    ai_recommended_action: str | None = None
    ai_explanation: str | None = None
    fallback_score: int | None = None
    match_category: str | None = None
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    rejection_reason: str | None = None
    candidate_strategy_hash: str | None = None

    def to_db_fields(self) -> dict[str, Any]:
        return {
            "match_score": self.match_score,
            "match_reason": self.match_reason,
            "match_method": self.match_method,
            "ai_decision": self.ai_decision,
            "ai_strengths": json.dumps(self.ai_strengths, ensure_ascii=False),
            "ai_missing_skills": json.dumps(self.ai_missing_skills, ensure_ascii=False),
            "ai_recommended_action": self.ai_recommended_action,
            "ai_explanation": self.ai_explanation,
            "fallback_score": self.fallback_score,
            "match_category": self.match_category,
            "matched_keywords": json.dumps(self.matched_keywords, ensure_ascii=False),
            "missing_keywords": json.dumps(self.missing_keywords, ensure_ascii=False),
            "rejection_reason": self.rejection_reason,
            "candidate_strategy_hash": self.candidate_strategy_hash,
        }


def _normalize_ai_match(data: dict[str, Any]) -> dict[str, Any]:
    score = clamp_score(data.get("match_score"))
    decision = str(data.get("decision", "") or "").upper().strip()
    if decision not in VALID_DECISIONS:
        if score >= 80:
            decision = "HIGH_MATCH"
        elif score >= 55:
            decision = "MEDIUM_MATCH"
        elif score >= 30:
            decision = "LOW_MATCH"
        else:
            decision = "REJECT"

    action = str(data.get("recommended_action", "") or "").upper().strip()
    if action not in VALID_ACTIONS:
        if score >= 75:
            action = "APPLY_NOW"
        elif score >= 50:
            action = "APPLY_IF_DESPERATE"
        else:
            action = "SKIP"

    return {
        "match_score": score,
        "decision": decision,
        "strengths": normalize_string_list(data.get("strengths", [])),
        "missing_skills": normalize_string_list(data.get("missing_skills", [])),
        "recommended_action": action,
        "explanation": str(data.get("explanation", "") or "").strip(),
    }


def _format_roles_context(ai_roles: dict[str, Any] | None) -> str:
    if not ai_roles or not ai_roles.get("best_fit_roles"):
        return "(no AI role analysis available)"
    lines = []
    for entry in ai_roles["best_fit_roles"][:6]:
        realistic = "yes" if entry.get("realistic_for_application", True) else "no"
        gaps = ", ".join(entry.get("missing_skills", [])[:4]) or "none"
        lines.append(
            f"- {entry['role']} (fit {entry['score']}): {entry.get('reason', '')} "
            f"[apply now: {realistic}; gaps: {gaps}]"
        )
    return "\n".join(lines)


def match_job_with_ai(
    job: dict[str, Any],
    profile: dict[str, Any],
    cv_profile: dict[str, Any],
    ai_roles: dict[str, Any] | None,
    candidate_summary: str | None = None,
) -> JobMatchResult:
    summary = candidate_summary or build_candidate_summary(profile, cv_profile)
    description = job.get("full_description") or job.get("description") or ""
    job_summary = summarize_job_text(
        job.get("title") or "",
        job.get("company") or "",
        job.get("location") or "",
        description,
    )

    user_prompt = f"""Evaluate this job for the candidate.

--- CANDIDATE ---
{summary}

--- AI ROLE ANALYSIS ---
{_format_roles_context(ai_roles)}

--- JOB ---
{job_summary}
"""

    cache_payload = (
        f"job_match_v1\n{job.get('job_url', job.get('id', ''))}\n"
        f"{hash(summary)}\n{job_summary}"
    )

    raw = call_openai_json(
        JOB_MATCH_SYSTEM,
        user_prompt,
        cache_namespace="job_match",
        cache_payload=cache_payload,
    )
    normalized = _normalize_ai_match(raw)
    from_cache = raw.get("_from_cache", False)

    reason_parts = [
        f"[ai{'/cache' if from_cache else ''}] {normalized['decision']} "
        f"({normalized['match_score']}) → {normalized['recommended_action']}",
    ]
    if normalized["strengths"]:
        reason_parts.append(f"strengths: {', '.join(normalized['strengths'])}")
    if normalized["missing_skills"]:
        reason_parts.append(f"gaps: {', '.join(normalized['missing_skills'])}")

    return JobMatchResult(
        match_score=normalized["match_score"],
        match_reason="; ".join(reason_parts),
        match_method="ai",
        ai_decision=normalized["decision"],
        ai_strengths=normalized["strengths"],
        ai_missing_skills=normalized["missing_skills"],
        ai_recommended_action=normalized["recommended_action"],
        ai_explanation=normalized["explanation"],
    )


def match_job_fallback(
    job: dict[str, Any],
    profile: dict[str, Any],
    cv_profile: dict[str, Any],
) -> JobMatchResult:
    score, reason = score_job_fallback(job, profile, cv_profile)
    return JobMatchResult(
        match_score=score,
        match_reason=reason,
        match_method="fallback",
        fallback_score=score,
    )


def match_job(
    job: dict[str, Any],
    profile: dict[str, Any],
    cv_profile: dict[str, Any],
    ai_roles: dict[str, Any] | None = None,
    *,
    use_ai: bool = True,
    candidate_summary: str | None = None,
) -> JobMatchResult:
    """Score a job with AI, or rule-based scoring when use_ai=False."""
    if not use_ai:
        return match_job_fallback(job, profile, cv_profile)

    fallback = match_job_fallback(job, profile, cv_profile)
    ai_result = match_job_with_ai(
        job, profile, cv_profile, ai_roles, candidate_summary=candidate_summary
    )
    ai_result.fallback_score = fallback.match_score
    return ai_result
