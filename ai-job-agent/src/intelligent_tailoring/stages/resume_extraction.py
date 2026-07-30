"""Stage 1 — Resume extraction into structured facts (deterministic reuse)."""

from __future__ import annotations

import json
from typing import Any

from ai_client import truncate_text
from config import OPENAI_CV_MAX_CHARS
from intelligent_tailoring.experience_math import (
    estimate_years_from_text,
    years_from_experience_entries,
)
from match_tailor_service import build_candidate_payload, source_resume_text


def extract_structured_resume(
    cv_profile: dict[str, Any],
    source_documents: str | None = None,
) -> dict[str, Any]:
    """Reuse existing profile + raw CV sources; never overwrite originals."""
    raw_text = source_resume_text(cv_profile, source_documents)
    payload = build_candidate_payload(cv_profile, source_documents)
    experience = cv_profile.get("experience")
    if not isinstance(experience, dict):
        experience = {}
    roles = experience.get("roles") or experience.get("jobs") or []
    if not isinstance(roles, list):
        roles = []

    projects = cv_profile.get("projects") or []
    if not isinstance(projects, list):
        projects = []

    education = cv_profile.get("education") or []
    if isinstance(education, dict):
        education = education.get("entries") or education.get("items") or [education]
    if not isinstance(education, list):
        education = []

    skills_block = cv_profile.get("skills") or {}
    if isinstance(skills_block, dict):
        skill_list: list[str] = []
        for value in skills_block.values():
            if isinstance(value, list):
                skill_list.extend(str(v) for v in value)
            elif value:
                skill_list.append(str(value))
    elif isinstance(skills_block, list):
        skill_list = [str(s) for s in skills_block]
    else:
        skill_list = []

    years = years_from_experience_entries(
        [
            r
            if isinstance(r, dict)
            else {"dates": str(r)}
            for r in roles
        ]
    )
    if years is None:
        years = estimate_years_from_text(raw_text)
    profile_years = experience.get("years_of_experience_estimate")
    if years is None and profile_years is not None:
        try:
            years = float(profile_years)
        except (TypeError, ValueError):
            years = None

    facts = {
        "raw_text": truncate_text(raw_text, OPENAI_CV_MAX_CHARS),
        "candidate_payload": truncate_text(payload, OPENAI_CV_MAX_CHARS),
        "contact": cv_profile.get("contact") if isinstance(cv_profile.get("contact"), dict) else {},
        "skills": skill_list,
        "experience_roles": roles,
        "projects": projects,
        "education": education,
        "certifications": cv_profile.get("certifications") or [],
        "years_of_experience": years,
        "sparse": len((raw_text or "").strip()) < 120
        and not roles
        and not skill_list,
    }
    return facts


def resume_facts_for_prompt(facts: dict[str, Any]) -> str:
    """Compact structured facts string for LLM stages."""
    return facts.get("candidate_payload") or json.dumps(
        {
            "skills": facts.get("skills"),
            "experience": facts.get("experience_roles"),
            "projects": facts.get("projects"),
            "education": facts.get("education"),
            "years_of_experience": facts.get("years_of_experience"),
        },
        ensure_ascii=False,
        indent=2,
    )
