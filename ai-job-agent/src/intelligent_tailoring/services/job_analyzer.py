"""JobAnalyzer — deep job understanding for tailoring strategy."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.services.job_family import (
    detect_job_family,
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
    """Understand the job: family, roles, seniority, tech stack, responsibilities."""
    if requirements is None:
        requirements = extract_job_requirements(
            job, use_cache=use_cache, jd_snapshot=jd_snapshot
        )

    title = str(job.get("title") or "")
    job_family = detect_job_family(title, requirements)
    primary_role = infer_primary_role(job_family, title)
    secondary_role = infer_secondary_role(job_family)

    required = list(requirements.get("required_skills") or requirements.get("hard_requirements") or [])
    preferred = list(requirements.get("preferred_skills") or requirements.get("soft_requirements") or [])
    tools = list(requirements.get("tools_technologies") or [])
    responsibilities = list(requirements.get("responsibilities") or [])
    soft_skills = list(requirements.get("soft_skills") or [])
    ats_keywords = list(requirements.get("ats_keywords") or [])

    return {
        "job_family": job_family,
        "primary_role": primary_role,
        "secondary_role": secondary_role,
        "seniority": str(requirements.get("seniority_level") or "").strip(),
        "required_technologies": required,
        "nice_to_have_technologies": preferred,
        "core_responsibilities": responsibilities,
        "soft_skills": soft_skills,
        "ats_keywords": ats_keywords,
        "tools_technologies": tools,
        "requirements": requirements,
        "job_title": title,
        "company": str(job.get("company") or ""),
    }
