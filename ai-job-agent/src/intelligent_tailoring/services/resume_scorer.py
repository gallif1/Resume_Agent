"""ResumeScorer — relevance scores 0-100 for every resume element."""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.services.job_family import emphasis_keywords

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _keyword_score(text: str, emphasis: dict[str, int], deprioritize: list[str]) -> int:
    norm = _norm(text)
    score = 10  # baseline — content exists
    for kw, weight in emphasis.items():
        if kw in norm:
            score += min(weight, 40)
    for kw in deprioritize:
        if kw in norm:
            score -= 15
    return max(0, min(100, score))


def _jd_match_score(text: str, jd_terms: list[str]) -> int:
    norm = _norm(text)
    if not jd_terms:
        return 0
    hits = sum(1 for t in jd_terms if _norm(t) in norm or norm in _norm(t))
    return min(35, hits * 8)


def score_resume_content(
    *,
    resume_facts: dict[str, Any],
    strategy: dict[str, Any],
    job_analysis: dict[str, Any],
    evidence_map: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score bullets, projects, skills, and experience entries 0-100."""
    job_family = str(strategy.get("job_family") or "general")
    emphasis = dict(strategy.get("emphasis_keywords") or emphasis_keywords(job_family))
    deprioritize = list(strategy.get("skills_to_deprioritize") or [])

    jd_terms: list[str] = []
    for key in (
        "required_technologies",
        "nice_to_have_technologies",
        "ats_keywords",
        "core_responsibilities",
    ):
        vals = job_analysis.get(key) or []
        if isinstance(vals, list):
            jd_terms.extend(str(v) for v in vals)

    evidence_by_text: dict[str, int] = {}
    for entry in evidence_map:
        ev = str(entry.get("supporting_evidence") or "").strip()
        if ev:
            bonus = 25 if entry.get("candidate_status") == "MATCH" else 12
            evidence_by_text[_norm(ev)] = bonus

    skill_scores: list[dict[str, Any]] = []
    for skill in resume_facts.get("display_skills") or resume_facts.get("skills") or []:
        text = str(skill)
        base = _keyword_score(text, emphasis, deprioritize)
        base += _jd_match_score(text, jd_terms)
        skill_scores.append({"text": text, "score": min(100, base), "type": "skill"})

    bullet_scores: list[dict[str, Any]] = []
    for role_idx, role in enumerate(resume_facts.get("experience_roles") or []):
        if not isinstance(role, dict):
            continue
        for bullet_idx, bullet in enumerate(role.get("bullets") or []):
            text = str(bullet)
            base = _keyword_score(text, emphasis, deprioritize)
            base += _jd_match_score(text, jd_terms)
            ev_bonus = evidence_by_text.get(_norm(text), 0)
            bullet_scores.append(
                {
                    "text": text,
                    "score": min(100, base + ev_bonus),
                    "type": "experience_bullet",
                    "role_index": role_idx,
                    "bullet_index": bullet_idx,
                    "company": str(role.get("company") or ""),
                    "title": str(role.get("title") or ""),
                }
            )

    project_scores: list[dict[str, Any]] = []
    hints = [h.lower() for h in (strategy.get("project_priority") or [])]
    for proj_idx, proj in enumerate(resume_facts.get("projects") or []):
        if not isinstance(proj, dict):
            continue
        name = str(proj.get("name") or "")
        desc = str(proj.get("description") or "")
        bullets = " ".join(str(b) for b in (proj.get("bullets") or []))
        combined = f"{name} {desc} {bullets}"
        base = _keyword_score(combined, emphasis, deprioritize)
        base += _jd_match_score(combined, jd_terms)
        for i, hint in enumerate(hints):
            if hint and hint in name.lower():
                base += max(5, 30 - i * 5)
        project_scores.append(
            {
                "text": name,
                "score": min(100, base),
                "type": "project",
                "project_index": proj_idx,
                "name": name,
                "description": desc,
                "bullets": [str(b) for b in (proj.get("bullets") or [])],
            }
        )

    # Per-skill family scores for cross-family comparison (e.g. FastAPI backend vs frontend)
    family_examples = {
        "backend": 98,
        "frontend": 25,
        "devops": 55,
        "support": 40,
        "qa": 45,
    }
    cross_family_skill_matrix: dict[str, dict[str, int]] = {}
    sample_skills = ["fastapi", "react", "aws", "postgresql", "debugging", "customer"]
    for sk in sample_skills:
        cross_family_skill_matrix[sk] = {}
        for fam in ("backend", "frontend", "devops", "qa", "support"):
            cross_family_skill_matrix[sk][fam] = _keyword_score(
                sk, emphasis_keywords(fam), deprioritize_keywords(fam)
            )

    return {
        "skills": skill_scores,
        "experience_bullets": bullet_scores,
        "projects": project_scores,
        "job_family": job_family,
        "cross_family_skill_matrix": cross_family_skill_matrix,
    }


def deprioritize_keywords(job_family: str) -> list[str]:
    from intelligent_tailoring.services.job_family import deprioritize_keywords as _dep

    return _dep(job_family)
