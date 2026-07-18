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
    """Structured, truncated summary of the candidate for job matching.
    
    Emphasizes Target Role and Skills Matrix for industry-agnostic matching,
    supporting career-switchers and candidates pivoting to new roles.
    """
    from profile_extractor import (
        build_dynamic_candidate_context,
        extract_target_roles,
        extract_skills_matrix,
        extract_projects,
    )
    
    # Build dynamic context
    context = build_dynamic_candidate_context(profile, cv_profile)
    target_roles = context["target_roles"]
    primary_target = context["primary_target_role"]
    skills_matrix = context["skills_matrix"]
    projects = context["projects"]
    is_career_switcher = context["is_career_switcher"]
    
    contact = cv_profile.get("contact", {}) if isinstance(cv_profile.get("contact"), dict) else {}
    experience = cv_profile.get("experience", {}) if isinstance(cv_profile.get("experience"), dict) else {}
    education = cv_profile.get("education", {}) if isinstance(cv_profile.get("education"), dict) else {}
    sections = cv_profile.get("sections", {}) if isinstance(cv_profile.get("sections"), dict) else {}
    insights = cv_profile.get("ai_insights", {}) if isinstance(cv_profile.get("ai_insights"), dict) else {}
    universal = cv_profile.get("universal_profile", {}) if isinstance(cv_profile.get("universal_profile"), dict) else {}
    location_prefs = universal.get("location_preferences", {}) if isinstance(universal.get("location_preferences"), dict) else {}

    preferred_locations = location_prefs.get("preferred_locations") or []
    location = (
        str(contact.get("location") or "").strip()
        or (str(preferred_locations[0]).strip() if preferred_locations else "")
        or str(profile.get("location") or "").strip()
    )
    remote_ok = location_prefs.get("remote_ok", profile.get("remote", False))

    # Build summary emphasizing Target Role alignment
    summary_parts = [
        f"Name: {contact.get('name') or ''}",
        f"Email: {contact.get('email') or ''}",
        f"Phone: {contact.get('phone') or ''}",
        f"Location preference: {location}",
        f"Open to remote: {remote_ok}",
        "",
        "=== TARGET ROLE (North Star for Matching) ===",
        f"Primary Target Role: {primary_target}",
        f"Alternative Target Roles: {', '.join(target_roles[1:4]) if len(target_roles) > 1 else 'None'}",
        f"Career Switcher Status: {'Yes - judge on projects/skills, not past titles' if is_career_switcher else 'No'}",
        "",
        "=== SKILLS MATRIX ===",
        f"Core Skills: {', '.join(skills_matrix['core_skills'][:15])}",
        f"Technologies/Tools: {', '.join(skills_matrix['technologies'][:15])}",
        f"Domain Knowledge: {', '.join(skills_matrix['domain_knowledge'][:10])}",
    ]
    
    # Add project experience (critical for career-switchers)
    if projects:
        summary_parts.extend([
            "",
            f"=== PROJECT PORTFOLIO ({len(projects)} projects) ===",
            "NOTE: For career-switchers and juniors, treat substantial projects as practical experience.",
        ])
        for i, project in enumerate(projects[:3], 1):
            proj_desc = project.get("description", "")
            proj_title = project.get("title", '')
            if proj_title:
                summary_parts.append(f"{i}. {proj_title}: {truncate_text(proj_desc, 150)}")
            else:
                summary_parts.append(f"{i}. {truncate_text(proj_desc, 200)}")
    
    # Employment history (context only, not primary evaluation criteria)
    summary_parts.extend([
        "",
        "=== EMPLOYMENT HISTORY (For Context Only) ===",
        f"Past Titles: {', '.join(experience.get('job_titles', [])[:3])}",
        f"Companies: {', '.join(experience.get('companies', [])[:3])}",
        f"Seniority: {experience.get('seniority_level', 'unknown')}",
        f"Years of experience (est.): {experience.get('years_of_experience_estimate', 'unknown')}",
    ])
    
    # Academic background
    summary_parts.extend([
        "",
        "=== ACADEMIC BACKGROUND ===",
        f"Degrees: {', '.join(education.get('degrees', [])[:2])}",
        f"Fields of Study: {', '.join(education.get('fields_of_study', [])[:2])}",
        f"Institutions: {', '.join(education.get('institutions', [])[:2])}",
    ])
    
    # Professional summary
    summary_text = sections.get("summary") or insights.get("professional_summary") or ""
    if summary_text:
        summary_parts.extend([
            "",
            "=== PROFESSIONAL SUMMARY ===",
            truncate_text(str(summary_text), 400)
        ])
    
    summary_parts.append("")
    summary_parts.append("=== MATCHING INSTRUCTIONS ===")
    summary_parts.append(f"- Match based on Target Role: {primary_target}")
    summary_parts.append("- Evaluate skills and projects for capability, not just job titles")
    summary_parts.append("- If candidate is career-switcher, do NOT penalize for mismatched employment history")
    summary_parts.append("- Count substantial projects as practical experience (1-2 years equivalent)")

    return truncate_text("\n".join(summary_parts), OPENAI_CV_SUMMARY_MAX_CHARS)
