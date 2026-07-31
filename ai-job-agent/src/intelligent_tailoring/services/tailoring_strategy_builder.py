"""TailoringStrategyBuilder — evidence-driven, profession-agnostic strategy."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.services.job_family import (
    deprioritize_keywords_from_requirements,
    emphasis_keywords_from_requirements,
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
    fact_scores: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build strategy from evidence map + JD — not a title-only template."""
    job_family = str(job_analysis.get("job_family") or "general")
    industry = str(job_analysis.get("industry") or "general")
    requirements = job_analysis.get("requirements") or {}

    emphasis = emphasis_keywords_from_requirements(
        requirements, job_family=job_family
    )
    # Merge any light soft prior already on job_analysis
    for k, v in (job_analysis.get("emphasis_keywords") or {}).items():
        emphasis[k] = max(int(emphasis.get(k, 0)), int(v))

    skills = [
        str(s)
        for s in (resume_facts.get("display_skills") or resume_facts.get("skills") or [])
    ]
    deprioritize = deprioritize_keywords_from_requirements(requirements, skills)

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

    strengths = [
        str(e.get("requirement") or "")
        for e in evidence_map
        if e.get("candidate_status") == "MATCH" and e.get("importance") == "hard"
    ][:12]

    skill_scores: dict[str, int] = {}
    blob = " ".join(skills).lower()
    for kw, weight in emphasis.items():
        if kw in blob:
            skill_scores[kw] = weight
    for req in matched_reqs:
        skill_scores[req.lower()] = max(skill_scores.get(req.lower(), 0), 40)
    # Prefer actual resume skill strings that match JD
    for skill in skills:
        low = skill.lower()
        for kw, weight in emphasis.items():
            if kw in low or low in kw:
                skill_scores[skill] = max(skill_scores.get(skill, 0), weight)

    top_skills = sorted(skill_scores.keys(), key=lambda k: skill_scores[k], reverse=True)[:16]
    skills_to_emphasize = top_skills[:10]

    # Project priority: score by JD term overlap, then soft hints
    projects = resume_facts.get("projects") or []
    project_priority = _rank_projects(projects, emphasis, job_family)

    # Fact-level priorities from KB scores
    facts_to_expand: list[str] = []
    facts_to_preserve: list[str] = []
    facts_to_condense: list[str] = []
    facts_to_omit: list[str] = []
    if fact_scores:
        for item in fact_scores:
            text = str(item.get("original_text") or "").strip()
            score = int(item.get("score") or 0)
            if not text:
                continue
            if score >= 60:
                facts_to_expand.append(text)
                facts_to_preserve.append(text)
            elif score >= 35:
                facts_to_preserve.append(text)
            elif score >= 20:
                facts_to_condense.append(text)
            else:
                facts_to_omit.append(text)

    ats_keywords = list(job_analysis.get("ats_keywords") or [])
    # CRITICAL: only insert keywords already evidenced on the resume.
    # JD-only keywords must NEVER be pushed into the rewrite plan.
    keywords_to_insert = [
        kw
        for kw in ats_keywords
        if kw and kw.lower() in blob
    ][:12]
    # Also allow matched requirements that appear in resume text
    for req in matched_reqs:
        if req and req.lower() in blob and req not in keywords_to_insert:
            keywords_to_insert.append(req)
    keywords_to_insert = keywords_to_insert[:12]

    summary_focus = _summary_focus(job_analysis, matched_reqs, strengths)
    experience_focus = _experience_focus(job_analysis, matched_reqs)
    value_prop = _value_proposition(strengths, job_analysis)

    section_order = _section_order(job_family, fact_scores)

    from intelligent_tailoring.services.evidence_amplifier import build_highlight_plan
    from intelligent_tailoring.skill_taxonomy import category_order_for_role

    highlight_plan = build_highlight_plan(
        evidence_map=evidence_map,
        skills_to_emphasize=skills_to_emphasize,
    )
    # Prefer taxonomy-aligned category order, boosted by emphasize terms
    cat_order = category_order_for_role(
        job_family, emphasize=skills_to_emphasize
    ) or skill_category_order(job_family)

    # Experience order: roles whose bullets overlap emphasize terms first
    experience_order = _experience_order(resume_facts, skills_to_emphasize)

    return {
        "target_positioning": value_prop,
        "target_job_family": job_family,
        "target_industry": industry,
        "job_family": job_family,  # compat
        "candidate_value_proposition": value_prop,
        "candidate_strengths": strengths[:10],
        "candidate_weaknesses": missing_reqs[:8],
        "important_missing_requirements": missing_reqs[:8],
        "strongest_evidence": facts_to_expand[:12],
        "top_resume_sections": section_order,
        "top_projects": project_priority[:5],
        "top_skills": top_skills,
        "skills_to_emphasize": skills_to_emphasize,
        "skills_to_deprioritize": deprioritize,
        "keywords_to_insert": keywords_to_insert,
        "keywords_to_avoid": list(deprioritize),
        "keyword_plan": keywords_to_insert + skills_to_emphasize[:5],
        "summary_focus": summary_focus,
        "experience_focus": experience_focus,
        "experience_priorities": facts_to_expand[:15],
        "experience_order": experience_order,
        "project_priorities": project_priority,
        "project_priority": project_priority,
        "education_priorities": [
            f.get("original_text")
            for f in (fact_scores or [])
            if f.get("fact_type") == "education" and int(f.get("score") or 0) >= 30
        ][:5],
        "facts_to_preserve": facts_to_preserve[:40],
        "facts_to_expand": facts_to_expand[:25],
        "facts_to_condense": facts_to_condense[:20],
        "facts_to_omit": facts_to_omit[:20],
        "section_order": section_order,
        "tone": "professional, specific, evidence-based, human",
        "output_language": language,
        "preferred_language": language,
        "skill_category_order": cat_order,
        "primary_role": job_analysis.get("primary_role") or "",
        "secondary_role": job_analysis.get("secondary_role") or "",
        "seniority": job_analysis.get("seniority") or "",
        "emphasis_keywords": emphasis,
        "highlight_plan": highlight_plan,
        "must_highlight_in_summary": list(highlight_plan.get("must_highlight") or [])[:8],
        "propagate_terms": list(highlight_plan.get("propagate_terms") or [])[:16],
        "requirement_support": list(highlight_plan.get("requirement_support") or []),
        "risk_warnings": [
            f"Missing hard requirement: {m}" for m in missing_reqs[:5]
        ],
    }


def _rank_projects(
    projects: list[Any],
    emphasis: dict[str, int],
    job_family: str,
) -> list[str]:
    scored: list[tuple[int, str]] = []
    hints = project_priority_hints(job_family)
    for p in projects:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "")
        blob = f"{name} {p.get('description') or ''} {' '.join(str(b) for b in (p.get('bullets') or []))}".lower()
        score = 0
        for kw, weight in emphasis.items():
            if kw in blob:
                score += min(weight, 30)
        for i, hint in enumerate(hints):
            if hint.lower() in name.lower():
                score += max(5, 20 - i * 4)
        scored.append((score, name))
    scored.sort(key=lambda x: -x[0])
    ordered = [n for _, n in scored if n]
    for p in projects:
        if isinstance(p, dict):
            name = str(p.get("name") or "")
            if name and name not in ordered:
                ordered.append(name)
    return ordered


def _summary_focus(
    job_analysis: dict[str, Any],
    matched_reqs: list[str],
    strengths: list[str],
) -> str:
    role = job_analysis.get("primary_role") or job_analysis.get("job_title") or "this role"
    industry = job_analysis.get("industry") or ""
    top = strengths[:5] or matched_reqs[:5]
    focus_bits = ", ".join(top) if top else "relevant professional experience"
    industry_bit = f" in {industry}" if industry and industry != "general" else ""
    return (
        f"Explain why this candidate fits {role}{industry_bit}. "
        f"Lead with specialization and business value; weave in evidenced strengths "
        f"({focus_bits}). Do not merely list tools. Sound like a human resume writer."
    )


def _experience_order(
    resume_facts: dict[str, Any],
    emphasize: list[str],
) -> list[str]:
    """Order experience entries by overlap with emphasized terms."""
    roles = resume_facts.get("experience_roles") or resume_facts.get("experience") or []
    scored: list[tuple[int, str]] = []
    emp = [str(e).lower() for e in emphasize if e]
    for role in roles:
        if not isinstance(role, dict):
            continue
        label = str(role.get("company") or role.get("title") or "").strip()
        blob = " ".join(
            [str(role.get("title") or ""), str(role.get("company") or "")]
            + [str(b) for b in (role.get("bullets") or [])]
        ).lower()
        score = sum(3 for e in emp if e and e in blob)
        scored.append((score, label))
    scored.sort(key=lambda x: -x[0])
    return [label for _, label in scored if label]


def _experience_focus(job_analysis: dict[str, Any], matched_reqs: list[str]) -> str:
    top = matched_reqs[:6]
    if top:
        return "Lead with: " + "; ".join(top)
    secondary = job_analysis.get("secondary_role") or ""
    return secondary or "Most relevant responsibilities first"


def _value_proposition(strengths: list[str], job_analysis: dict[str, Any]) -> str:
    role = job_analysis.get("primary_role") or "the target role"
    if strengths:
        return f"Candidate for {role} with demonstrated: {', '.join(strengths[:4])}"
    return f"Honest positioning for {role} based on available evidence"


def _section_order(
    job_family: str,
    fact_scores: list[dict[str, Any]] | None,
) -> list[str]:
    base = [
        "professional_summary",
        "skills",
        "experience",
        "projects",
        "education",
        "certifications",
    ]
    # Promote projects when they score higher than experience on average
    if fact_scores:
        proj = [f for f in fact_scores if f.get("source_section") == "projects"]
        exp = [f for f in fact_scores if f.get("source_section") == "experience"]
        if proj and exp:
            avg_p = sum(int(f.get("score") or 0) for f in proj) / len(proj)
            avg_e = sum(int(f.get("score") or 0) for f in exp) / len(exp)
            if avg_p > avg_e + 5:
                return [
                    "professional_summary",
                    "skills",
                    "projects",
                    "experience",
                    "education",
                    "certifications",
                ]
    if job_family in ("education", "design"):
        return [
            "professional_summary",
            "experience",
            "education",
            "skills",
            "projects",
            "certifications",
        ]
    return base
