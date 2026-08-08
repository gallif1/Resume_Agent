"""Canonical resume schema + content inventory for the 4-agent pipeline.

Every stage after parsing must read/write compatible shapes. Content counts and
source-coverage reports make silent data loss visible and blockable.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

logger = logging.getLogger("intelligent_tailoring.canonical_resume")

CANONICAL_SCHEMA_VERSION = "canonical_resume_v1"


def content_inventory(resume: dict[str, Any] | None) -> dict[str, Any]:
    """Count structured content without logging personal prose."""
    data = resume if isinstance(resume, dict) else {}
    experience = [e for e in (data.get("experience") or []) if isinstance(e, dict)]
    projects = [p for p in (data.get("projects") or []) if isinstance(p, dict)]
    skills = [str(s).strip() for s in (data.get("skills") or []) if str(s).strip()]
    education = [e for e in (data.get("education") or []) if isinstance(e, dict)]

    exp_bullets = 0
    empty_experience = 0
    for entry in experience:
        bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        exp_bullets += len(bullets)
        if not bullets:
            empty_experience += 1

    proj_bullets = 0
    empty_projects = 0
    projects_with_desc = 0
    for entry in projects:
        bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        desc = str(entry.get("description") or "").strip()
        proj_bullets += len(bullets)
        if desc:
            projects_with_desc += 1
        if not bullets and not desc:
            empty_projects += 1

    skill_atoms = 0
    for line in skills:
        if ":" in line:
            skill_atoms += len(
                [p for p in re.split(r"\s*[,;/|]\s*", line.split(":", 1)[1]) if p.strip()]
            )
        else:
            skill_atoms += 1

    summary = str(
        data.get("professional_summary") or data.get("summary") or ""
    ).strip()

    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "experience_entries": len(experience),
        "experience_bullets": exp_bullets,
        "empty_experience_entries": empty_experience,
        "projects": len(projects),
        "project_bullets": proj_bullets,
        "projects_with_description": projects_with_desc,
        "empty_projects": empty_projects,
        "skills": len(skills),
        "skill_atoms": skill_atoms,
        "education_entries": len(education),
        "summary_words": len(summary.split()),
        "has_summary": bool(summary),
    }


def inventory_from_facts(resume_facts: dict[str, Any] | None) -> dict[str, Any]:
    """Inventory for the Agent-1 resume_facts shape (experience_roles)."""
    facts = resume_facts if isinstance(resume_facts, dict) else {}
    adapted = {
        "experience": list(facts.get("experience_roles") or facts.get("experience") or []),
        "projects": list(facts.get("projects") or []),
        "skills": list(facts.get("display_skills") or facts.get("skills") or []),
        "education": list(facts.get("education") or []),
        "professional_summary": "",
    }
    inv = content_inventory(adapted)
    inv["source"] = "resume_facts"
    return inv


def log_stage_inventory(
    *,
    generation_id: str,
    stage: str,
    resume: dict[str, Any] | None = None,
    resume_facts: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit structured content counts (no personal content)."""
    if resume is not None:
        payload = content_inventory(resume)
    else:
        payload = inventory_from_facts(resume_facts)
    payload.update(
        {
            "generation_id": generation_id,
            "stage": stage,
            "warnings": list(warnings or []),
        }
    )
    if extra:
        payload.update(extra)
    logger.info("content_inventory %s", payload)
    return payload


def to_canonical_resume(data: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize any tailored/legacy resume dict into the canonical shape."""
    src = data if isinstance(data, dict) else {}
    experience_in = src.get("experience") or src.get("experience_roles") or []
    projects_in = src.get("projects") or []
    experience: list[dict[str, Any]] = []
    for idx, entry in enumerate(experience_in):
        if not isinstance(entry, dict):
            continue
        bullets = []
        for b in entry.get("bullets") or entry.get("bullet_points") or []:
            text = str(b).strip()
            if text:
                bullets.append(
                    {
                        "id": f"exp_{idx}_b{len(bullets)}",
                        "text": text,
                        "source_fact_ids": list(entry.get("source_fact_ids") or []),
                        "requirement_ids": [],
                        "relevance_score": int(entry.get("relevance_score") or 0),
                        "status": str(entry.get("status") or "included"),
                    }
                )
        experience.append(
            {
                "id": str(entry.get("id") or f"exp_{idx}"),
                "role": str(entry.get("title") or entry.get("role") or ""),
                "organization": str(entry.get("company") or entry.get("organization") or ""),
                "dates": entry.get("dates")
                if isinstance(entry.get("dates"), dict)
                else {"raw": str(entry.get("dates") or "")},
                "context_type": str(entry.get("context_type") or "employment"),
                "bullets": bullets,
            }
        )

    projects: list[dict[str, Any]] = []
    for idx, entry in enumerate(projects_in):
        if isinstance(entry, str):
            name, desc, bullets, techs = _parse_project_string(entry)
            entry = {
                "name": name,
                "description": desc,
                "bullets": bullets,
                "technologies": techs,
            }
        if not isinstance(entry, dict):
            continue
        bullets = []
        for b in entry.get("bullets") or entry.get("bullet_points") or []:
            text = str(b).strip()
            if text:
                bullets.append(
                    {
                        "id": f"proj_{idx}_b{len(bullets)}",
                        "text": text,
                        "source_fact_ids": list(entry.get("source_fact_ids") or []),
                        "requirement_ids": [],
                        "relevance_score": int(entry.get("relevance_score") or 0),
                        "status": str(entry.get("status") or "included"),
                    }
                )
        projects.append(
            {
                "id": str(entry.get("id") or f"proj_{idx}"),
                "name": str(entry.get("name") or ""),
                "description": str(entry.get("description") or ""),
                "bullets": bullets,
                "technologies": [
                    str(t).strip()
                    for t in (entry.get("technologies") or entry.get("tech") or [])
                    if str(t).strip()
                ],
            }
        )

    skills_out: list[dict[str, Any]] = []
    for line in src.get("skills") or []:
        text = str(line).strip()
        if not text:
            continue
        if ":" in text:
            category, rest = text.split(":", 1)
            atoms = [a.strip() for a in re.split(r"\s*[,;/|]\s*", rest) if a.strip()]
            for atom in atoms:
                skills_out.append(
                    {
                        "canonical_id": atom.lower(),
                        "display_name": atom,
                        "category": category.strip() or "Other Relevant Skills",
                        "source_fact_ids": [],
                        "relevance_score": 0,
                    }
                )
        else:
            skills_out.append(
                {
                    "canonical_id": text.lower(),
                    "display_name": text,
                    "category": "Other Relevant Skills",
                    "source_fact_ids": [],
                    "relevance_score": 0,
                }
            )

    summary = str(src.get("professional_summary") or src.get("summary") or "").strip()
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "target_title": str(src.get("professional_title") or src.get("target_title") or ""),
        "professional_summary": summary,
        "experience": experience,
        "projects": projects,
        "skills": skills_out,
        "education": list(src.get("education") or []),
        "certifications": list(src.get("certifications") or []),
        "genuine_gaps": list(src.get("genuine_gaps") or []),
        "quality_metadata": dict(src.get("quality_metadata") or {}),
    }


def canonical_to_tailored(canonical: dict[str, Any]) -> dict[str, Any]:
    """Convert canonical schema back to the renderer/tailored_resume shape."""
    skills_by_cat: dict[str, list[str]] = {}
    for skill in canonical.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        cat = str(skill.get("category") or "Other Relevant Skills")
        name = str(skill.get("display_name") or "").strip()
        if not name:
            continue
        skills_by_cat.setdefault(cat, [])
        if name not in skills_by_cat[cat]:
            skills_by_cat[cat].append(name)
    skill_lines = [f"{cat}: {', '.join(names)}" for cat, names in skills_by_cat.items()]

    experience = []
    for entry in canonical.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        dates = entry.get("dates")
        if isinstance(dates, dict):
            dates_str = str(dates.get("raw") or "").strip()
            if not dates_str:
                start = str(dates.get("start") or "").strip()
                end = str(dates.get("end") or "").strip()
                dates_str = " – ".join(p for p in (start, end) if p)
        else:
            dates_str = str(dates or "")
        experience.append(
            {
                "title": str(entry.get("role") or ""),
                "company": str(entry.get("organization") or ""),
                "dates": dates_str,
                "bullets": [
                    str(b.get("text") if isinstance(b, dict) else b).strip()
                    for b in (entry.get("bullets") or [])
                    if str(b.get("text") if isinstance(b, dict) else b).strip()
                ],
            }
        )

    projects = []
    for entry in canonical.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        projects.append(
            {
                "name": str(entry.get("name") or ""),
                "description": str(entry.get("description") or ""),
                "bullets": [
                    str(b.get("text") if isinstance(b, dict) else b).strip()
                    for b in (entry.get("bullets") or [])
                    if str(b.get("text") if isinstance(b, dict) else b).strip()
                ],
                "technologies": list(entry.get("technologies") or []),
            }
        )

    summary = str(canonical.get("professional_summary") or "").strip()
    return {
        "professional_title": str(canonical.get("target_title") or ""),
        "professional_summary": summary,
        "summary": summary,
        "skills": skill_lines,
        "experience": experience,
        "projects": projects,
        "education": list(canonical.get("education") or []),
        "certifications": list(canonical.get("certifications") or []),
    }


def drop_empty_shell_entries(resume: dict[str, Any]) -> dict[str, Any]:
    """Remove experience/project headings that have no bullets or description."""
    out = deepcopy(resume) if isinstance(resume, dict) else {}
    experience = []
    for entry in out.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        if not bullets:
            continue
        experience.append({**entry, "bullets": bullets})
    projects = []
    for entry in out.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        desc = str(entry.get("description") or "").strip()
        if not bullets and not desc:
            continue
        projects.append({**entry, "bullets": bullets, "description": desc})
    out["experience"] = experience
    out["projects"] = projects
    return out


def build_source_coverage_report(
    *,
    source_facts: list[dict[str, Any]] | list[Any],
    tailored_resume: dict[str, Any],
    omission_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare source activity facts to final resume coverage."""
    activity_facts: list[dict[str, Any]] = []
    for fact in source_facts or []:
        if hasattr(fact, "to_dict"):
            f = fact.to_dict()
        elif isinstance(fact, dict):
            f = fact
        else:
            continue
        ftype = str(f.get("fact_type") or "")
        section = str(f.get("source_section") or "")
        if section in ("experience", "projects") and ftype not in ("role", "project", "skill"):
            activity_facts.append(f)
        elif ftype in ("responsibility", "achievement", "measurable_result") or (
            section == "source_fragment" and len(str(f.get("original_text") or "")) > 20
        ):
            activity_facts.append(f)

    blob = _resume_text_blob(tailored_resume).lower()
    selected = 0
    omitted: list[dict[str, Any]] = []
    for fact in activity_facts:
        text = str(fact.get("original_text") or "").strip()
        if not text:
            continue
        probe = text[:48].lower()
        tokens = [t for t in re.findall(r"[a-z0-9+#.]{4,}", text.lower()) if len(t) >= 4]
        hit = probe in blob or (
            len(tokens) >= 2 and sum(1 for t in tokens[:6] if t in blob) >= 2
        )
        if hit:
            selected += 1
        else:
            omitted.append(
                {
                    "source_fact_id": str(fact.get("id") or ""),
                    "decision": "omitted",
                    "reason": "not_present_in_final_resume",
                    "relevance_hint": str(fact.get("normalized_value") or "")[:80],
                }
            )

    decisions = list(omission_decisions or [])
    justified_ids = {
        str(d.get("source_fact_id") or "")
        for d in decisions
        if str(d.get("decision") or "") in ("omitted", "merged")
        and str(d.get("reason") or "").strip()
    }
    high_relevance_omitted = [
        o
        for o in omitted
        if o.get("source_fact_id") and o["source_fact_id"] not in justified_ids
    ]
    total = len(activity_facts)
    coverage = (selected / total) if total else 1.0
    return {
        "total_source_facts": total,
        "selected_facts": selected,
        "merged_facts": sum(
            1 for d in decisions if str(d.get("decision") or "") == "merged"
        ),
        "omitted_facts": len(omitted),
        "high_relevance_facts_omitted": high_relevance_omitted[:20],
        "coverage_score": round(coverage, 3),
    }


def completeness_failures(
    resume: dict[str, Any],
    *,
    source_inventory: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
) -> list[str]:
    """Hard completeness failures for empty shells / silent loss."""
    failures: list[str] = []
    inv = content_inventory(resume)
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()

    if re.search(
        r"\b(frontend engineer)\s+(frontend developer)\b"
        r"|\b(frontend developer)\s+(frontend engineer)\b"
        r"|\b(software engineer)\s+(software developer)\b",
        summary,
        flags=re.I,
    ):
        failures.append("duplicate_title_phrase_in_summary")

    if inv["empty_experience_entries"]:
        failures.append("empty_experience_entries")
    if inv["empty_projects"]:
        failures.append("empty_project_entries")

    src = source_inventory or {}
    if src.get("experience_bullets", 0) >= 1 and inv["experience_entries"] > 0:
        if inv["experience_bullets"] < 1:
            failures.append("experience_shown_without_bullets")
    if src.get("project_bullets", 0) + src.get("projects_with_description", 0) >= 1:
        if inv["projects"] > 0 and (
            inv["project_bullets"] + inv["projects_with_description"] < 1
        ):
            failures.append("project_shown_without_content")
    if src.get("skill_atoms", 0) >= 8 and inv["skill_atoms"] < 4:
        failures.append("skills_severely_underfilled")

    # Generic one-word Other skills
    for line in resume.get("skills") or []:
        text = str(line)
        if re.search(
            r"other relevant skills\s*:\s*(architecture|web|api|software)\b",
            text,
            flags=re.I,
        ):
            failures.append(f"generic_other_skill:{text.split(':')[-1].strip()}")

    # React must not appear under Backend
    for line in resume.get("skills") or []:
        text = str(line)
        if re.search(r"\bbackend\b", text, flags=re.I) and re.search(
            r"\breact\b", text, flags=re.I
        ):
            if not re.search(r"react\s*native", text, flags=re.I):
                failures.append("react_categorized_as_backend")

    if (
        coverage
        and coverage.get("coverage_score", 1) < 0.25
        and coverage.get("high_relevance_facts_omitted")
        and (inv["experience_bullets"] + inv["project_bullets"]) < 3
    ):
        failures.append("high_relevance_facts_silently_omitted")

    return list(dict.fromkeys(failures))


def estimate_content_density(resume: dict[str, Any]) -> dict[str, Any]:
    """Heuristic page utilization for early-career one-page resumes."""
    inv = content_inventory(resume)
    # Rough fill score: bullets + summary + skills vs expected early-career density
    expected_bullets = 6
    expected_summary = 50
    expected_skills = 12
    bullet_fill = min(1.0, inv["experience_bullets"] + inv["project_bullets"]) / expected_bullets
    summary_fill = min(1.0, inv["summary_words"] / expected_summary)
    skill_fill = min(1.0, inv["skill_atoms"] / expected_skills)
    utilization = round(0.45 * bullet_fill + 0.25 * summary_fill + 0.30 * skill_fill, 3)
    underfilled = utilization < 0.70 and (
        inv["experience_bullets"] + inv["project_bullets"] < 4
    )
    return {
        "utilization_score": utilization,
        "underfilled": underfilled,
        "inventory": inv,
    }


def restore_missing_content_from_source(
    tailored: dict[str, Any],
    *,
    resume_facts: dict[str, Any],
    max_roles: int = 3,
    max_projects: int = 2,
    min_bullets_per_role: int = 1,
    min_bullets_per_project: int = 2,
) -> dict[str, Any]:
    """Preservation-first repair: refill empty shells from verified source facts."""
    out = drop_empty_shell_entries(deepcopy(tailored))
    source_roles = [
        r for r in (resume_facts.get("experience_roles") or []) if isinstance(r, dict)
    ]
    source_projects = normalize_project_list(resume_facts.get("projects") or [])

    # If tailored experience empty/thin, restore top source roles with bullets
    tailored_exp = [e for e in (out.get("experience") or []) if isinstance(e, dict)]
    if not tailored_exp or sum(
        len([b for b in (e.get("bullets") or []) if str(b).strip()]) for e in tailored_exp
    ) < 1:
        restored = []
        for role in source_roles[:max_roles]:
            bullets = [str(b).strip() for b in (role.get("bullets") or []) if str(b).strip()]
            if not bullets:
                continue
            restored.append(
                {
                    "company": str(role.get("company") or ""),
                    "title": str(role.get("title") or ""),
                    "dates": str(role.get("dates") or ""),
                    "bullets": bullets[:3],
                }
            )
        if restored:
            out["experience"] = restored
    else:
        # Fill empty shells in place from matching source roles
        fixed = []
        for entry in tailored_exp:
            bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
            if len(bullets) >= min_bullets_per_role:
                fixed.append({**entry, "bullets": bullets})
                continue
            match = _match_role(entry, source_roles)
            if match:
                src_bullets = [
                    str(b).strip() for b in (match.get("bullets") or []) if str(b).strip()
                ]
                if src_bullets:
                    fixed.append({**entry, "bullets": src_bullets[:3]})
                    continue
            # Drop empty shell
        out["experience"] = fixed

    tailored_proj = [p for p in (out.get("projects") or []) if isinstance(p, dict)]
    if not tailored_proj or all(
        not str(p.get("description") or "").strip()
        and not [b for b in (p.get("bullets") or []) if str(b).strip()]
        for p in tailored_proj
    ):
        restored_p = []
        for proj in source_projects[:max_projects]:
            bullets = [str(b).strip() for b in (proj.get("bullets") or []) if str(b).strip()]
            desc = str(proj.get("description") or "").strip()
            if not bullets and not desc:
                continue
            restored_p.append(
                {
                    "name": str(proj.get("name") or ""),
                    "description": desc,
                    "bullets": bullets[: max(min_bullets_per_project, 2)],
                    "technologies": list(proj.get("technologies") or []),
                }
            )
        if restored_p:
            out["projects"] = restored_p
    else:
        fixed_p = []
        for entry in tailored_proj:
            bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
            desc = str(entry.get("description") or "").strip()
            if bullets or desc:
                if len(bullets) < min_bullets_per_project:
                    match = _match_project(entry, source_projects)
                    if match:
                        src_b = [
                            str(b).strip()
                            for b in (match.get("bullets") or [])
                            if str(b).strip()
                        ]
                        if src_b:
                            from intelligent_tailoring.services.one_page_compressor import (
                                _dedupe_similar,
                                texts_are_near_duplicates,
                            )

                            bullets = _dedupe_similar(bullets + src_b)[
                                : max(min_bullets_per_project, len(bullets), 3)
                            ]
                        if not desc:
                            desc = str(match.get("description") or "").strip()
                if desc and bullets:
                    from intelligent_tailoring.services.one_page_compressor import (
                        texts_are_near_duplicates,
                    )

                    if any(texts_are_near_duplicates(desc, b) for b in bullets):
                        desc = ""
                fixed_p.append({**entry, "bullets": bullets, "description": desc})
        out["projects"] = fixed_p

    # Skills: if severely thin, restore display_skills / skills from facts
    skill_lines = [str(s).strip() for s in (out.get("skills") or []) if str(s).strip()]
    source_skills = [
        str(s).strip()
        for s in (resume_facts.get("display_skills") or resume_facts.get("skills") or [])
        if str(s).strip()
    ]
    if len(skill_lines) < 2 and source_skills:
        from intelligent_tailoring.skill_taxonomy import normalize_skill_lines

        out["skills"] = normalize_skill_lines(source_skills)

    return drop_empty_shell_entries(out)


def _resume_text_blob(resume: dict[str, Any]) -> str:
    parts = [
        str(resume.get("professional_summary") or resume.get("summary") or ""),
        " ".join(str(s) for s in (resume.get("skills") or [])),
    ]
    for entry in resume.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        parts.append(str(entry.get("title") or ""))
        parts.append(str(entry.get("company") or ""))
        parts.extend(str(b) for b in (entry.get("bullets") or []))
    for entry in resume.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        parts.append(str(entry.get("name") or ""))
        parts.append(str(entry.get("description") or ""))
        parts.extend(str(b) for b in (entry.get("bullets") or []))
    return "\n".join(parts)


def _parse_project_string(raw: str) -> tuple[str, str, list[str], list[str]]:
    text = str(raw or "").strip()
    techs: list[str] = []
    tech_match = re.search(r"\[([^\]]+)\]\s*$", text)
    if tech_match:
        techs = [t.strip() for t in tech_match.group(1).split(",") if t.strip()]
        text = text[: tech_match.start()].strip()
    name = text
    desc = ""
    if ":" in text:
        name, desc = text.split(":", 1)
        name = name.strip()
        desc = desc.strip()
    bullets: list[str] = []
    if desc and ("\n" in desc or "•" in desc or desc.lstrip().startswith("-")):
        parts = re.split(r"[\n\r]+|[•●▪◦]\s*|^\s*-\s*", desc)
        cleaned = [re.sub(r"\s+", " ", p).strip(" -") for p in parts if p and len(p.strip()) > 8]
        if len(cleaned) >= 2:
            bullets = cleaned
            desc = cleaned[0] if not name else ""
            if not name and cleaned:
                name = cleaned[0][:80]
                bullets = cleaned[1:]
    return name, desc, bullets, techs


def normalize_project_list(projects: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in projects or []:
        if isinstance(item, str):
            name, desc, bullets, techs = _parse_project_string(item)
            if name or desc or bullets:
                out.append(
                    {
                        "name": name,
                        "description": desc,
                        "bullets": bullets,
                        "technologies": techs,
                    }
                )
            continue
        if not isinstance(item, dict):
            continue
        bullets = [
            str(b).strip()
            for b in (item.get("bullets") or item.get("bullet_points") or [])
            if str(b).strip()
        ]
        desc = str(item.get("description") or "").strip()
        # If description embeds bullets, split them
        if not bullets and desc and ("•" in desc or "\n-" in desc):
            name, d2, b2, techs = _parse_project_string(
                f"{item.get('name') or ''}: {desc}"
            )
            bullets = b2
            if d2:
                desc = d2
            techs = list(item.get("technologies") or item.get("tech") or techs)
        else:
            techs = [
                str(t).strip()
                for t in (item.get("technologies") or item.get("tech") or [])
                if str(t).strip()
            ]
        out.append(
            {
                "name": str(item.get("name") or ""),
                "description": desc,
                "bullets": bullets,
                "technologies": techs,
            }
        )
    return out


def _match_role(entry: dict[str, Any], source_roles: list[dict[str, Any]]) -> dict[str, Any] | None:
    title = str(entry.get("title") or "").strip().lower()
    company = str(entry.get("company") or "").strip().lower()
    for role in source_roles:
        rt = str(role.get("title") or "").strip().lower()
        rc = str(role.get("company") or "").strip().lower()
        if title and title == rt:
            return role
        if company and company == rc and (not title or not rt or title in rt or rt in title):
            return role
        if title and rt and (title in rt or rt in title):
            return role
    return source_roles[0] if len(source_roles) == 1 else None


def _match_project(
    entry: dict[str, Any], source_projects: list[dict[str, Any]]
) -> dict[str, Any] | None:
    name = str(entry.get("name") or "").strip().lower()
    for proj in source_projects:
        pn = str(proj.get("name") or "").strip().lower()
        if name and pn and (name == pn or name in pn or pn in name):
            return proj
    return source_projects[0] if len(source_projects) == 1 else None
