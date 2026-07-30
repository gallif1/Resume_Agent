"""TailoringStrategyBuilder — drives entire resume generation."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.services.job_family import (
    deprioritize_keywords,
    emphasis_keywords,
    project_priority_hints,
    skill_category_order,
)


def build_tailoring_strategy(
    *,
    job_analysis: dict[str, Any],
    resume_facts: dict[str, Any],
    evidence_map: list[dict[str, Any]],
    ranked_requirements: list[dict[str, Any]],
    language: str = "en",
) -> dict[str, Any]:
    """Build internal tailoring strategy object from job + resume analysis."""
    job_family = str(job_analysis.get("job_family") or "general")
    emphasis = emphasis_keywords(job_family)
    deprioritize = deprioritize_keywords(job_family)

    matched_reqs = [
        e["requirement"]
        for e in evidence_map
        if e.get("candidate_status") in ("MATCH", "PARTIAL")
        and e.get("importance") in ("hard", "soft")
    ]
    missing_reqs = [
        e["requirement"]
        for e in evidence_map
        if e.get("candidate_status") == "MISSING" and e.get("importance") == "hard"
    ]

    strengths: list[str] = []
    weaknesses: list[str] = list(missing_reqs[:8])
    for entry in evidence_map:
        if entry.get("candidate_status") == "MATCH" and entry.get("importance") == "hard":
            strengths.append(str(entry.get("requirement") or ""))

    skills = [str(s) for s in (resume_facts.get("display_skills") or resume_facts.get("skills") or [])]
    projects = resume_facts.get("projects") or []
    project_names = [
        str(p.get("name") or "") for p in projects if isinstance(p, dict)
    ]

    # Top skills from evidence + resume presence
    skill_scores: dict[str, int] = {}
    blob = " ".join(skills).lower()
    for kw, weight in emphasis.items():
        if kw in blob:
            skill_scores[kw] = weight
    for req in matched_reqs:
        key = req.lower()
        skill_scores[key] = max(skill_scores.get(key, 0), 40)

    top_skills = sorted(skill_scores.keys(), key=lambda k: skill_scores[k], reverse=True)[:12]
    skills_to_emphasize = top_skills[:8]
    skills_to_deprioritize = list(deprioritize)

    # Project priority from hints + names
    hints = project_priority_hints(job_family)
    project_priority: list[str] = []
    for hint in hints:
        for name in project_names:
            if hint.lower() in name.lower() and name not in project_priority:
                project_priority.append(name)
    for name in project_names:
        if name not in project_priority:
            project_priority.append(name)

    ats_keywords = list(job_analysis.get("ats_keywords") or [])
    keywords_to_insert = [
        kw for kw in ats_keywords
        if kw and kw.lower() not in blob
    ][:10]
    keywords_to_avoid = list(deprioritize)

    summary_focus = _summary_focus(job_family, job_analysis)
    experience_focus = _experience_focus(job_family)

    section_order = _section_order(job_family)

    return {
        "job_family": job_family,
        "candidate_strengths": strengths[:10],
        "candidate_weaknesses": weaknesses[:8],
        "top_resume_sections": section_order,
        "top_projects": project_priority[:5],
        "top_skills": top_skills,
        "skills_to_emphasize": skills_to_emphasize,
        "skills_to_deprioritize": skills_to_deprioritize,
        "keywords_to_insert": keywords_to_insert,
        "keywords_to_avoid": keywords_to_avoid,
        "summary_focus": summary_focus,
        "experience_focus": experience_focus,
        "project_priority": project_priority,
        "preferred_language": language,
        "skill_category_order": skill_category_order(job_family),
        "primary_role": job_analysis.get("primary_role") or "",
        "secondary_role": job_analysis.get("secondary_role") or "",
        "seniority": job_analysis.get("seniority") or "",
        "emphasis_keywords": emphasis,
    }


def _summary_focus(job_family: str, job_analysis: dict[str, Any]) -> str:
    focuses = {
        "backend": (
            "REST APIs, database design, business logic, SQL, backend architecture, "
            "validation, performance, and server-side development"
        ),
        "frontend": (
            "React/Angular/React Native UI development, responsive interfaces, "
            "client-side architecture, and REST API integration"
        ),
        "devops": (
            "AWS cloud infrastructure, CI/CD, deployment automation, monitoring, "
            "logging, and server health"
        ),
        "qa": (
            "Debugging, troubleshooting, validation, bug reproduction, testing, "
            "documentation, and reliability"
        ),
        "support": (
            "Customer communication, issue investigation, root cause analysis, "
            "logs, problem solving, and cross-functional collaboration"
        ),
    }
    base = focuses.get(job_family, "Relevant professional experience aligned to the role")
    role = job_analysis.get("primary_role") or ""
    if role:
        return f"{role}: {base}"
    return base


def _experience_focus(job_family: str) -> str:
    return {
        "backend": "API design, data layer, business logic, and backend services first",
        "frontend": "Client development, UI work, and API integration first",
        "devops": "Infrastructure, deployment, monitoring, and automation first",
        "qa": "Debugging, testing, validation, and quality work first",
        "support": "Troubleshooting, customer issues, and investigation first",
        "general": "Most relevant responsibilities first",
    }.get(job_family, "Most relevant responsibilities first")


def _section_order(job_family: str) -> list[str]:
    base = [
        "professional_summary",
        "skills",
        "experience",
        "projects",
        "education",
        "certifications",
    ]
    if job_family == "devops":
        # Projects like Server Monitor often central for DevOps
        return [
            "professional_summary",
            "skills",
            "projects",
            "experience",
            "education",
            "certifications",
        ]
    return base
