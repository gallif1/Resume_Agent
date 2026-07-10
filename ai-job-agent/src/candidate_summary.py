"""Build compact candidate summaries for OpenAI matching (token-efficient)."""

from __future__ import annotations

from typing import Any

from ai_client import truncate_text
from config import OPENAI_CV_SUMMARY_MAX_CHARS


def flatten_skills(cv_profile: dict[str, Any], max_per_category: int = 8) -> list[str]:
    skills = cv_profile.get("skills", {})
    flat: list[str] = []
    if isinstance(skills, dict):
        for items in skills.values():
            if not isinstance(items, list):
                continue
            for item in items[:max_per_category]:
                text = str(item).strip()
                if text and text not in flat:
                    flat.append(text)
    elif isinstance(skills, list):
        flat = [str(item).strip() for item in skills if str(item).strip()]
    return flat[:40]


def build_candidate_summary(profile: dict[str, Any], cv_profile: dict[str, Any]) -> str:
    """Structured, truncated summary of the candidate for job matching."""
    contact = cv_profile.get("contact", {}) if isinstance(cv_profile.get("contact"), dict) else {}
    experience = cv_profile.get("experience", {}) if isinstance(cv_profile.get("experience"), dict) else {}
    education = cv_profile.get("education", {}) if isinstance(cv_profile.get("education"), dict) else {}
    sections = cv_profile.get("sections", {}) if isinstance(cv_profile.get("sections"), dict) else {}
    insights = cv_profile.get("ai_insights", {}) if isinstance(cv_profile.get("ai_insights"), dict) else {}
    universal = cv_profile.get("universal_profile", {}) if isinstance(cv_profile.get("universal_profile"), dict) else {}
    location_prefs = universal.get("location_preferences", {}) if isinstance(universal.get("location_preferences"), dict) else {}

    skills = flatten_skills(cv_profile)
    projects = cv_profile.get("projects", [])
    if isinstance(projects, list):
        project_text = "; ".join(str(p).strip() for p in projects[:4] if str(p).strip())
    else:
        project_text = ""

    preferred_locations = location_prefs.get("preferred_locations") or []
    location = (
        str(contact.get("location") or "").strip()
        or (str(preferred_locations[0]).strip() if preferred_locations else "")
        or str(profile.get("location") or "").strip()
    )
    remote_ok = location_prefs.get("remote_ok", profile.get("remote", False))

    target_roles: list[str] = []
    for source in (
        cv_profile.get("best_fit_roles"),
        universal.get("preferred_role_titles"),
        insights.get("recommended_job_types"),
        profile.get("target_roles"),
    ):
        if not isinstance(source, list):
            continue
        for role in source:
            text = str(role).strip()
            if text and text not in target_roles:
                target_roles.append(text)

    summary_parts = [
        f"Name: {contact.get('name') or ''}",
        f"Email: {contact.get('email') or ''}",
        f"Phone: {contact.get('phone') or ''}",
        f"Location preference: {location}",
        f"Open to remote: {remote_ok}",
        f"Target roles: {', '.join(target_roles[:6])}",
        f"Seniority: {experience.get('seniority_level', 'unknown')}",
        f"Years of experience (est.): {experience.get('years_of_experience_estimate', 'unknown')}",
        f"Recent titles: {', '.join(experience.get('job_titles', [])[:3])}",
        f"Companies: {', '.join(experience.get('companies', [])[:3])}",
        f"Education: {', '.join(education.get('degrees', [])[:2])} — "
        f"{', '.join(education.get('fields_of_study', [])[:2])}",
        f"Skills: {', '.join(skills)}",
        f"Strengths: {', '.join(cv_profile.get('strengths', [])[:6])}",
    ]

    summary_text = sections.get("summary") or insights.get("professional_summary") or ""
    if summary_text:
        summary_parts.append(f"Summary: {truncate_text(str(summary_text), 600)}")

    if project_text:
        summary_parts.append(f"Projects: {truncate_text(project_text, 800)}")

    return truncate_text("\n".join(summary_parts), OPENAI_CV_SUMMARY_MAX_CHARS)
