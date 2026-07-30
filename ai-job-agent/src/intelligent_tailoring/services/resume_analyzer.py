"""ResumeAnalyzer — structured resume facts for tailoring."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.stages.resume_extraction import extract_structured_resume


def analyze_resume(
    cv_profile: dict[str, Any],
    source_documents: str | None = None,
) -> dict[str, Any]:
    """Extract and return structured resume facts (deterministic)."""
    facts = extract_structured_resume(cv_profile, source_documents)
    return facts


def resume_facts_to_baseline_resume(facts: dict[str, Any]) -> dict[str, Any]:
    """Build a baseline resume dict from extracted facts for similarity checks."""
    roles = facts.get("experience_roles") or []
    experience = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        experience.append(
            {
                "company": str(role.get("company") or ""),
                "title": str(role.get("title") or ""),
                "dates": str(role.get("dates") or ""),
                "bullets": [
                    str(b) for b in (role.get("bullets") or []) if str(b).strip()
                ],
            }
        )
    projects = []
    for proj in facts.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        projects.append(
            {
                "name": str(proj.get("name") or ""),
                "description": str(proj.get("description") or ""),
                "bullets": [
                    str(b) for b in (proj.get("bullets") or []) if str(b).strip()
                ],
            }
        )
    return {
        "professional_title": "",
        "professional_summary": "",
        "summary": "",
        "skills": [str(s) for s in (facts.get("skills") or []) if str(s).strip()],
        "experience": experience,
        "projects": projects,
        "education": list(facts.get("education") or []),
        "certifications": list(facts.get("certifications") or []),
    }
