"""Derive an ATS-ready candidate profile from an existing parsed CV."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from skill_normalizer import TECH_CATEGORIES, normalize_skill_set

TECHNOLOGY_CATEGORIES = TECH_CATEGORIES


@dataclass
class AtsCandidateProfile:
    skills: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    experience_years: float | None = None
    previous_roles: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    seniority: str = "unknown"
    domain: str | None = None

    @property
    def all_skills_set(self) -> set[str]:
        return set(self.skills) | set(self.technologies) | set(self.languages) | set(self.certifications)


def _flatten_category(skills: dict[str, Any], categories: frozenset[str]) -> list[str]:
    items: list[str] = []
    if not isinstance(skills, dict):
        return items
    for category, values in skills.items():
        if category not in categories:
            continue
        if isinstance(values, list):
            items.extend(str(v) for v in values if v)
    return items


def _flatten_all_skills(skills: dict[str, Any]) -> list[str]:
    items: list[str] = []
    if not isinstance(skills, dict):
        return items
    for values in skills.values():
        if isinstance(values, list):
            items.extend(str(v) for v in values if v)
    return items


def build_ats_candidate(cv_profile: dict[str, Any]) -> AtsCandidateProfile:
    """Build an ATS candidate profile from the existing cv_profile.json schema."""
    universal = cv_profile.get("universal_profile") or {}
    skills_dict = cv_profile.get("skills") or {}
    experience = cv_profile.get("experience") or {}
    education = cv_profile.get("education") or {}

    domain = cv_profile.get("primary_domain")

    if universal.get("canonical_skills") or universal.get("technologies_tools"):
        all_raw = list(universal.get("canonical_skills") or []) + list(
            universal.get("technologies_tools") or []
        )
        tech_raw = list(universal.get("technologies_tools") or [])
    else:
        all_raw = _flatten_all_skills(skills_dict)
        tech_raw = _flatten_category(skills_dict, TECHNOLOGY_CATEGORIES)

    languages_raw = list(universal.get("languages") or []) or list(
        skills_dict.get("languages") or []
    )
    sections = cv_profile.get("sections") or {}
    if sections.get("languages"):
        languages_raw.append(sections["languages"])

    education_items: list[str] = list(universal.get("education") or [])
    if not education_items:
        for key in ("degrees", "fields_of_study", "institutions"):
            for item in education.get(key) or []:
                if item:
                    education_items.append(str(item))

    years = universal.get("years_of_experience")
    if years is None:
        years = experience.get("years_of_experience_estimate")
    try:
        experience_years = float(years) if years is not None else None
    except (TypeError, ValueError):
        experience_years = None

    seniority = str(
        universal.get("seniority_level") or experience.get("seniority_level") or "unknown"
    ).strip().lower()
    if not seniority:
        seniority = "unknown"

    previous_roles = list(universal.get("preferred_role_titles") or []) or [
        str(r) for r in (experience.get("job_titles") or []) if r
    ]

    certifications = list(universal.get("certifications") or []) or [
        str(c) for c in (cv_profile.get("certifications") or []) if c
    ]

    return AtsCandidateProfile(
        skills=sorted(normalize_skill_set(all_raw, domain=domain)),
        technologies=sorted(normalize_skill_set(tech_raw, domain=domain)),
        experience_years=experience_years,
        previous_roles=previous_roles,
        projects=[str(p) for p in (cv_profile.get("projects") or []) if p],
        education=education_items,
        languages=sorted(normalize_skill_set(languages_raw, domain=domain)),
        certifications=certifications,
        seniority=seniority,
        domain=domain,
    )
