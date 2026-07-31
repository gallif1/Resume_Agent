"""Internal Resume Quality Score — drives weak-section regeneration.

Dimensions (0–100 each):
  naturalness, evidence_utilization, job_relevance, ats_optimization,
  human_writing_quality, technical_clarity, section_balance,
  visual_quality, role_differentiation
"""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.writing.ai_detector import detect_ai_writing
from intelligent_tailoring.writing.ai_phrases import AI_CLICHE_PHRASES
from intelligent_tailoring.writing.style_validator import evaluate_writing_quality

DEFAULT_QUALITY_THRESHOLD = 72

_WEIGHTS = {
    "naturalness": 0.16,
    "evidence_utilization": 0.14,
    "job_relevance": 0.16,
    "ats_optimization": 0.08,
    "human_writing_quality": 0.16,
    "technical_clarity": 0.10,
    "section_balance": 0.08,
    "visual_quality": 0.04,
    "role_differentiation": 0.08,
}


def _clamp(score: float) -> int:
    return max(0, min(100, int(round(score))))


def _resume_blob(resume: dict[str, Any]) -> str:
    parts = [
        str(resume.get("professional_summary") or resume.get("summary") or ""),
        " ".join(str(s) for s in (resume.get("skills") or [])),
    ]
    for entry in resume.get("experience") or []:
        if isinstance(entry, dict):
            parts.extend(str(b) for b in (entry.get("bullets") or []))
    for entry in resume.get("projects") or []:
        if isinstance(entry, dict):
            parts.append(str(entry.get("description") or ""))
            parts.extend(str(b) for b in (entry.get("bullets") or []))
    return " ".join(parts)


def _naturalness(resume: dict[str, Any], ai: dict[str, Any], style: dict[str, Any]) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = int(ai.get("human_score") or style.get("dimensions", {}).get("ai_likeness") or 60)
    blob = _resume_blob(resume).lower()
    hits = [p for p in AI_CLICHE_PHRASES if p in blob]
    if hits:
        score -= min(40, 8 * len(hits))
        notes.append(f"ai_cliches:{','.join(hits[:4])}")
    if re.search(r"\bprofessional with (knowledge|experience)\b", blob):
        score -= 20
        notes.append("generic_professional_with")
    if re.search(r"\b(strong understanding|highly motivated|passionate about)\b", blob):
        score -= 15
        notes.append("banned_filler")
    return _clamp(score), notes


def _evidence_utilization(
    resume: dict[str, Any],
    *,
    highlight_plan: dict[str, Any] | None,
    evidence_inventory: dict[str, Any] | None,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    blob = _resume_blob(resume).lower()
    must = list((highlight_plan or {}).get("must_highlight") or [])
    if must:
        covered = sum(1 for m in must if str(m).lower() in blob)
        score = 100.0 * covered / max(len(must), 1)
        if covered < len(must):
            missing = [m for m in must if str(m).lower() not in blob][:5]
            notes.append(f"unhighlighted:{','.join(missing)}")
    else:
        score = 65.0

    thin = list((evidence_inventory or {}).get("thin_projects") or [])
    projects = resume.get("projects") or []
    if projects:
        short = 0
        for p in projects:
            if not isinstance(p, dict):
                continue
            bullets = p.get("bullets") or []
            if len(bullets) < 2:
                short += 1
        if short:
            score -= min(25, short * 10)
            notes.append(f"thin_projects:{short}")
    elif thin:
        notes.append("inventory_had_thin_projects")
    return _clamp(score), notes


def _job_relevance(
    resume: dict[str, Any],
    *,
    strategy: dict[str, Any] | None,
    highlight_plan: dict[str, Any] | None,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    blob = _resume_blob(resume).lower()
    emphasize = list((strategy or {}).get("skills_to_emphasize") or [])
    propagate = list((highlight_plan or {}).get("propagate_terms") or emphasize)
    if not propagate:
        return 60, ["no_emphasis_terms"]
    hits = sum(1 for t in propagate[:12] if str(t).lower() in blob)
    score = 40 + 60 * hits / max(min(len(propagate), 12), 1)
    # Summary should mention at least one emphasized term
    summary = str(resume.get("professional_summary") or resume.get("summary") or "").lower()
    if not any(str(t).lower() in summary for t in propagate[:8]):
        score -= 15
        notes.append("summary_misses_emphasis")
    return _clamp(score), notes


def _ats_score(resume: dict[str, Any]) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = 85
    if not str(resume.get("professional_summary") or resume.get("summary") or "").strip():
        score -= 40
        notes.append("missing_summary")
    if not (resume.get("skills") or []):
        score -= 15
        notes.append("missing_skills")
    blob = _resume_blob(resume)
    if "\t\t" in blob or "||||" in blob:
        score -= 30
        notes.append("layout_artifacts")
    return _clamp(score), notes


def _technical_clarity(resume: dict[str, Any]) -> tuple[int, list[str]]:
    notes: list[str] = []
    bullets: list[str] = []
    for entry in list(resume.get("experience") or []) + list(resume.get("projects") or []):
        if isinstance(entry, dict):
            bullets.extend(str(b) for b in (entry.get("bullets") or []) if str(b).strip())
    if not bullets:
        return 50, ["no_bullets"]
    vague = sum(
        1
        for b in bullets
        if re.match(r"^(worked on|helped with|responsible for|created database)\b", b, re.I)
        or len(b.split()) < 6
    )
    score = 90 - min(50, vague * 12)
    if vague:
        notes.append(f"vague_bullets:{vague}")
    # Prefer specific nouns/verbs
    specific = sum(
        1
        for b in bullets
        if re.search(
            r"\b(designed|built|implemented|developed|configured|resolved|taught|negotiated|forecast)\b",
            b,
            re.I,
        )
    )
    score += min(10, specific)
    return _clamp(score), notes


def _section_balance(resume: dict[str, Any]) -> tuple[int, list[str]]:
    notes: list[str] = []
    summary_words = len(
        str(resume.get("professional_summary") or resume.get("summary") or "").split()
    )
    exp_bullets = sum(
        len(e.get("bullets") or [])
        for e in (resume.get("experience") or [])
        if isinstance(e, dict)
    )
    proj_bullets = sum(
        len(p.get("bullets") or [])
        for p in (resume.get("projects") or [])
        if isinstance(p, dict)
    )
    score = 70
    if 35 <= summary_words <= 90:
        score += 15
    elif summary_words < 20:
        score -= 20
        notes.append("summary_too_short")
    elif summary_words > 110:
        score -= 10
        notes.append("summary_too_long")
    if exp_bullets >= 3:
        score += 8
    if (resume.get("projects") or []) and proj_bullets < 2:
        score -= 12
        notes.append("projects_underdeveloped")
    if not (resume.get("skills") or []):
        score -= 10
    return _clamp(score), notes


def _role_differentiation(
    resume: dict[str, Any],
    *,
    strategy: dict[str, Any] | None,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    family = str((strategy or {}).get("job_family") or (strategy or {}).get("target_job_family") or "")
    focus = str((strategy or {}).get("summary_focus") or "").lower()
    summary = str(resume.get("professional_summary") or resume.get("summary") or "").lower()
    skills = " ".join(str(s) for s in (resume.get("skills") or [])).lower()
    score = 55
    if family and family != "general":
        score += 10
    # First skill category should align with emphasis / family cues
    first_skill = str((resume.get("skills") or [""])[0]).lower()
    cues = {
        "backend": ("backend", "api", "python", "java", "database"),
        "frontend": ("frontend", "react", "ui", "javascript", "css"),
        "devops": ("cloud", "devops", "docker", "kubernetes", "ci/cd"),
        "data": ("python", "sql", "data", "analytics"),
        "finance": ("finance", "excel", "forecast", "reconcil"),
        "sales": ("sales", "crm", "salesforce", "pipeline"),
        "healthcare": ("patient", "ehr", "clinical", "healthcare"),
        "education": ("teach", "curriculum", "classroom", "instruction"),
    }
    family_cues = cues.get(family, ())
    if family_cues and any(c in first_skill or c in skills[:80] for c in family_cues):
        score += 20
    else:
        notes.append("skills_order_not_role_led")
    if focus and any(tok in summary for tok in focus.split() if len(tok) > 4):
        score += 10
    else:
        notes.append("summary_weak_role_signal")
    return _clamp(score), notes


def evaluate_resume_quality(
    resume: dict[str, Any],
    *,
    strategy: dict[str, Any] | None = None,
    highlight_plan: dict[str, Any] | None = None,
    evidence_inventory: dict[str, Any] | None = None,
    recruiter_review: dict[str, Any] | None = None,
    hiring_manager: dict[str, Any] | None = None,
    threshold: int = DEFAULT_QUALITY_THRESHOLD,
) -> dict[str, Any]:
    """Compute multi-dimension quality score and weak sections to regenerate."""
    style = evaluate_writing_quality(resume)
    ai = detect_ai_writing(resume)

    dims: dict[str, int] = {}
    notes: dict[str, list[str]] = {}

    dims["naturalness"], notes["naturalness"] = _naturalness(resume, ai, style)
    dims["evidence_utilization"], notes["evidence_utilization"] = _evidence_utilization(
        resume, highlight_plan=highlight_plan, evidence_inventory=evidence_inventory
    )
    dims["job_relevance"], notes["job_relevance"] = _job_relevance(
        resume, strategy=strategy, highlight_plan=highlight_plan
    )
    dims["ats_optimization"], notes["ats_optimization"] = _ats_score(resume)
    dims["human_writing_quality"] = _clamp(
        (
            int(style.get("overall_score") or 0) * 0.6
            + int(ai.get("human_score") or 0) * 0.4
        )
    )
    notes["human_writing_quality"] = list((style.get("weak_dimensions") or {}).keys())[:6]
    dims["technical_clarity"], notes["technical_clarity"] = _technical_clarity(resume)
    dims["section_balance"], notes["section_balance"] = _section_balance(resume)
    dims["visual_quality"] = 80  # presentation handled by themes; structural proxy
    if not (resume.get("skills") or []):
        dims["visual_quality"] = 55
    dims["role_differentiation"], notes["role_differentiation"] = _role_differentiation(
        resume, strategy=strategy
    )

    # Blend external reviewer signals when present
    if recruiter_review:
        interview = int(recruiter_review.get("interview_quality") or 0)
        human = int(recruiter_review.get("human_believability") or 0)
        dims["human_writing_quality"] = _clamp(
            dims["human_writing_quality"] * 0.7 + max(interview, human) * 0.3
        )
        dims["naturalness"] = _clamp(dims["naturalness"] * 0.7 + human * 0.3)
    if hiring_manager:
        dims["job_relevance"] = _clamp(
            dims["job_relevance"] * 0.6
            + int(hiring_manager.get("overall_fit") or 0) * 0.25
            + int(hiring_manager.get("evidence_quality") or 0) * 0.15
        )

    overall = sum(dims[k] * _WEIGHTS[k] for k in _WEIGHTS)
    overall_i = _clamp(overall)

    weak_sections: list[str] = []
    if dims["naturalness"] < threshold or dims["human_writing_quality"] < threshold:
        weak_sections.append("summary")
        weak_sections.append("experience")
    if dims["job_relevance"] < threshold or "summary_misses_emphasis" in notes["job_relevance"]:
        if "summary" not in weak_sections:
            weak_sections.append("summary")
        weak_sections.append("skills")
    if dims["evidence_utilization"] < threshold:
        weak_sections.append("projects")
        weak_sections.append("experience")
    if dims["technical_clarity"] < threshold:
        weak_sections.append("experience")
        weak_sections.append("projects")
    if dims["section_balance"] < threshold and "projects_underdeveloped" in notes["section_balance"]:
        weak_sections.append("projects")
    if dims["role_differentiation"] < threshold:
        weak_sections.append("skills")
        if "summary" not in weak_sections:
            weak_sections.append("summary")

    # Recruiter/HM explicit section requests
    for src in (recruiter_review or {}, hiring_manager or {}):
        for s in src.get("sections_to_regenerate") or src.get("weakest_sections") or []:
            key = "summary" if s in {"summary", "professional_summary"} else str(s)
            if key in {"summary", "experience", "projects", "skills"} and key not in weak_sections:
                weak_sections.append(key)

    # Deduplicate preserve order
    weak_sections = list(dict.fromkeys(weak_sections))

    return {
        "overall_score": overall_i,
        "threshold": threshold,
        "passed": overall_i >= threshold,
        "dimensions": dims,
        "notes": notes,
        "weak_sections": weak_sections,
        "weights": dict(_WEIGHTS),
        "style_overall": style.get("overall_score"),
        "ai_risk": ai.get("ai_risk"),
    }
