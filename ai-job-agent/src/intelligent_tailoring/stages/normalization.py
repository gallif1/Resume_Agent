"""Stage 3 — Skill/terminology normalization via ontology (deterministic)."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.ontology import SkillOntology, dedupe_skills, get_ontology


def normalize_terms(
    requirements: dict[str, Any],
    resume_facts: dict[str, Any],
    *,
    ontology: SkillOntology | None = None,
) -> dict[str, Any]:
    ontology = ontology or get_ontology()
    skill_fields = (
        "required_skills",
        "preferred_skills",
        "tools_technologies",
        "industry_terminology",
        "soft_skills",
        "ats_keywords",
        "hard_requirements",
        "soft_requirements",
        "education_certifications",
    )
    normalized_req: dict[str, Any] = dict(requirements)
    for field in skill_fields:
        raw_list = requirements.get(field) or []
        if not isinstance(raw_list, list):
            continue
        mapped = [ontology.normalize_term(str(t)) for t in raw_list]
        normalized_req[field] = dedupe_skills(mapped)

    resume_skills = [str(s) for s in (resume_facts.get("skills") or [])]
    normalized_resume_skills = dedupe_skills(
        ontology.normalize_term(s) for s in resume_skills
    )
    return {
        "requirements": normalized_req,
        "resume_skills": normalized_resume_skills,
        "resume_skill_mappings": [
            {"raw": s, "normalized": ontology.normalize_term(s)} for s in resume_skills
        ],
    }
