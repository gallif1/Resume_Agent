"""JobAnalyzer — deep job understanding for universal tailoring."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.services.job_family import (
    detect_industry,
    detect_job_family,
    emphasis_keywords_from_requirements,
    infer_primary_role,
    infer_secondary_role,
)
from intelligent_tailoring.stages.job_requirement_extraction import (
    extract_job_requirements,
)


def analyze_job(
    job: dict[str, Any],
    *,
    use_cache: bool = True,
    jd_snapshot: str | None = None,
    requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Understand the job from responsibilities — not title alone."""
    if requirements is None:
        requirements = extract_job_requirements(
            job, use_cache=use_cache, jd_snapshot=jd_snapshot
        )

    title = str(job.get("title") or "")
    job_family = detect_job_family(title, requirements)
    industry = detect_industry(title, requirements)
    primary_role = infer_primary_role(job_family, title)
    secondary_role = infer_secondary_role(job_family)

    required = list(
        requirements.get("required_skills")
        or requirements.get("hard_requirements")
        or []
    )
    preferred = list(
        requirements.get("preferred_skills")
        or requirements.get("soft_requirements")
        or []
    )
    tools = list(requirements.get("tools_technologies") or [])
    responsibilities = list(requirements.get("responsibilities") or [])
    soft_skills = list(requirements.get("soft_skills") or [])
    ats_keywords = list(requirements.get("ats_keywords") or [])

    # Structured JobRequirement-like list for evidence mapping consumers
    job_requirements_structured: list[dict[str, Any]] = []
    for i, text in enumerate(requirements.get("hard_requirements") or required):
        job_requirements_structured.append(
            {
                "id": f"req_hard_{i}",
                "text": str(text),
                "normalized_competency": str(text).lower(),
                "category": "hard",
                "priority": 100 - i,
                "required_or_preferred": "required",
                "explicit_or_inferred": "explicit",
                "evidence_expected": "direct or strongly inferred",
                "synonyms": [],
            }
        )
    for i, text in enumerate(requirements.get("soft_requirements") or preferred):
        job_requirements_structured.append(
            {
                "id": f"req_soft_{i}",
                "text": str(text),
                "normalized_competency": str(text).lower(),
                "category": "soft",
                "priority": 50 - i,
                "required_or_preferred": "preferred",
                "explicit_or_inferred": "explicit",
                "evidence_expected": "direct or strongly inferred",
                "synonyms": [],
            }
        )

    emphasis = emphasis_keywords_from_requirements(
        requirements, job_family=job_family
    )

    return {
        "job_family": job_family,
        "industry": industry,
        "normalized_job_family": job_family,
        "primary_role": primary_role,
        "secondary_role": secondary_role,
        "seniority": str(requirements.get("seniority_level") or "").strip(),
        "required_technologies": required,
        "required_competencies": required,
        "nice_to_have_technologies": preferred,
        "preferred_competencies": preferred,
        "core_responsibilities": responsibilities,
        "soft_skills": soft_skills,
        "ats_keywords": ats_keywords,
        "tools_technologies": tools,
        "requirements": requirements,
        "job_requirements_structured": job_requirements_structured,
        "job_title": title,
        "company": str(job.get("company") or ""),
        "emphasis_keywords": emphasis,
        "output_language": str(requirements.get("language") or "en"),
    }
