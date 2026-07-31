"""ResumeRebuilder — deterministic reordering before LLM rewrite."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _sort_by_score(items: list[Any], scores: list[dict[str, Any]], key_field: str) -> list[Any]:
    score_map: dict[str, int] = {}
    for s in scores:
        text = str(s.get("text") or s.get("name") or "")
        score_map[text] = int(s.get("score") or 0)

    def sort_key(item: Any) -> int:
        if isinstance(item, dict):
            text = str(item.get("name") or item.get("text") or "")
        else:
            text = str(item)
        return -score_map.get(text, 0)

    return sorted(items, key=sort_key)


def _group_skills(
    skills: list[str],
    scores: list[dict[str, Any]],
    category_order: list[str],
    job_family: str,
) -> list[str]:
    """Group and order skills by category relevance for the job family."""
    score_map = {str(s["text"]): int(s["score"]) for s in scores if s.get("text")}
    categorized: dict[str, list[tuple[int, str]]] = {cat: [] for cat in category_order}
    other_key = category_order[-1] if category_order else "Other"
    for skill in skills:
        text = str(skill).strip()
        if not text:
            continue
        low = text.lower()
        score = score_map.get(text, score_map.get(text.split(":")[-1].strip(), 20))
        cat = _categorize_skill(low, job_family)
        bucket = cat if cat in categorized else other_key
        categorized.setdefault(bucket, []).append((score, text))

    result: list[str] = []
    for cat in category_order:
        entries = categorized.get(cat) or []
        entries.sort(key=lambda x: -x[0])
        for score, text in entries:
            if ":" in text and not text.startswith(tuple(category_order)):
                result.append(text)
            else:
                # Format as category line when multiple skills share category
                pass
        # Emit grouped category lines
        if entries:
            skill_names = [t for _, t in entries]
            if len(skill_names) == 1 and ":" in skill_names[0]:
                result.append(skill_names[0])
            elif skill_names:
                plain = [s.split(":")[-1].strip() if ":" in s else s for s in skill_names]
                result.append(f"{cat}: {', '.join(plain)}")

    # Dedupe preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for line in result:
        if line not in seen:
            seen.add(line)
            deduped.append(line)
    if not deduped:
        # Fallback: sort raw skills by score
        sorted_skills = sorted(
            skills,
            key=lambda s: -score_map.get(str(s), 10),
        )
        deduped = [str(s) for s in sorted_skills if str(s).strip()]
    return deduped


def _categorize_skill(low: str, job_family: str) -> str:
    """Deterministic taxonomy — job_family no longer misclassifies skills."""
    _ = job_family
    from intelligent_tailoring.skill_taxonomy import categorize_skill

    return categorize_skill(low)


def rebuild_resume_structure(
    *,
    resume_facts: dict[str, Any],
    scores: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Reorder skills, bullets, and projects without rewriting text yet."""
    job_family = str(strategy.get("job_family") or "general")
    category_order = list(strategy.get("skill_category_order") or ["Skills", "Other"])

    roles = deepcopy(resume_facts.get("experience_roles") or [])
    projects = deepcopy(resume_facts.get("projects") or [])
    skills = [str(s) for s in (resume_facts.get("display_skills") or resume_facts.get("skills") or [])]

    bullet_scores = scores.get("experience_bullets") or []
    project_scores = scores.get("projects") or []
    skill_scores = scores.get("skills") or []

    # Reorder bullets within each role
    for role_idx, role in enumerate(roles):
        if not isinstance(role, dict):
            continue
        bullets = [str(b) for b in (role.get("bullets") or [])]
        role_bullet_scores = [
            s for s in bullet_scores if s.get("role_index") == role_idx
        ]
        score_by_idx = {
            int(s.get("bullet_index") or 0): int(s.get("score") or 0)
            for s in role_bullet_scores
        }
        indexed = list(enumerate(bullets))
        indexed.sort(key=lambda pair: -score_by_idx.get(pair[0], 0))
        role["bullets"] = [b for _, b in indexed]

    # Reorder projects
    proj_score_map = {
        int(s.get("project_index") or 0): int(s.get("score") or 0)
        for s in project_scores
    }
    indexed_proj = list(enumerate(projects))
    indexed_proj.sort(key=lambda pair: -proj_score_map.get(pair[0], 0))
    projects = [p for _, p in indexed_proj]

    grouped_skills = _group_skills(skills, skill_scores, category_order, job_family)

    rebuilt = {
        "professional_title": str(strategy.get("primary_role") or ""),
        "professional_summary": "",  # filled by rewriter
        "summary": "",
        "skills": grouped_skills,
        "experience": [
            {
                "company": str(r.get("company") or ""),
                "title": str(r.get("title") or ""),
                "dates": str(r.get("dates") or ""),
                "bullets": list(r.get("bullets") or []),
            }
            for r in roles
            if isinstance(r, dict)
        ],
        "projects": [
            {
                "name": str(p.get("name") or ""),
                "description": str(p.get("description") or ""),
                "bullets": list(p.get("bullets") or []),
            }
            for p in projects
            if isinstance(p, dict)
        ],
        "education": list(resume_facts.get("education") or []),
        "certifications": list(resume_facts.get("certifications") or []),
        "section_order": list(strategy.get("top_resume_sections") or []),
    }
    return rebuilt
