"""Stage 1 — Resume extraction into structured facts (deterministic reuse)."""

from __future__ import annotations

import json
import re
from typing import Any

from ai_client import truncate_text
from config import OPENAI_CV_MAX_CHARS
from intelligent_tailoring.canonical_resume import normalize_project_list
from intelligent_tailoring.experience_math import (
    estimate_years_from_text,
    years_from_experience_entries,
)
from match_tailor_service import build_candidate_payload, source_resume_text


def _roles_from_master(master: dict[str, Any]) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for entry in master.get("work_experience") or []:
        if not isinstance(entry, dict):
            continue
        bullets = [
            str(b).strip()
            for b in (entry.get("bullet_points") or entry.get("bullets") or [])
            if str(b).strip()
        ]
        desc = str(entry.get("description") or "").strip()
        if desc and desc not in bullets:
            # Prefer bullets; keep description as first bullet when no bullets.
            if not bullets:
                bullets = [desc]
        title = str(entry.get("title") or entry.get("role") or "").strip()
        company = str(entry.get("company") or entry.get("organization") or "").strip()
        start = str(entry.get("start_date") or "").strip()
        end = str(entry.get("end_date") or "").strip()
        dates = str(entry.get("dates") or "").strip()
        if not dates and (start or end):
            dates = " – ".join(p for p in (start, end) if p)
        if not title and not company and not bullets:
            continue
        roles.append(
            {
                "title": title,
                "company": company,
                "dates": dates,
                "bullets": bullets,
            }
        )
    return roles


def _roles_from_experience_section(text: str) -> list[dict[str, Any]]:
    """Parse aggregator-style experience blocks into roles with bullets."""
    raw = (text or "").strip()
    if not raw:
        return []
    blocks = re.split(r"\n\s*\n+", raw)
    roles: list[dict[str, Any]] = []
    heading_re = re.compile(
        r"^(?P<title>.+?)\s*@\s*(?P<company>.+?)(?:\s*\((?P<dates>[^)]+)\))?\s*$"
    )
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        match = heading_re.match(lines[0])
        if not match:
            # Fallback: first line is title, remaining bullets
            if len(lines) == 1 and len(lines[0]) < 12:
                continue
            title = lines[0]
            company = ""
            dates = ""
            rest = lines[1:]
        else:
            title = match.group("title").strip()
            company = match.group("company").strip()
            dates = (match.group("dates") or "").strip()
            rest = lines[1:]
        bullets: list[str] = []
        prose: list[str] = []
        for ln in rest:
            cleaned = re.sub(r"^[•●▪◦\-\u2022]+\s*", "", ln).strip()
            if not cleaned:
                continue
            if ln.lstrip().startswith(("•", "-", "●", "▪", "◦")) or len(cleaned) > 40:
                bullets.append(cleaned)
            else:
                prose.append(cleaned)
        if prose and not bullets:
            bullets = prose
        elif prose:
            # Keep short prose as description-style first bullet if distinct
            for p in prose:
                if p not in bullets:
                    bullets.insert(0, p)
        if title or company or bullets:
            roles.append(
                {
                    "title": title,
                    "company": company,
                    "dates": dates,
                    "bullets": bullets,
                }
            )
    return roles


def _projects_from_master(master: dict[str, Any]) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    for proj in master.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        bullets = [
            str(b).strip()
            for b in (proj.get("bullet_points") or proj.get("bullets") or [])
            if str(b).strip()
        ]
        desc = str(proj.get("description") or "").strip()
        name = str(proj.get("name") or "").strip()
        techs = [
            str(t).strip()
            for t in (proj.get("technologies") or proj.get("tech") or [])
            if str(t).strip()
        ]
        if not bullets and desc:
            # Split multi-sentence descriptions into bullets when rich enough
            sentences = [
                s.strip()
                for s in re.split(r"(?<=[.!?])\s+", desc)
                if len(s.strip()) > 20
            ]
            if len(sentences) >= 2:
                bullets = sentences
                desc = sentences[0]
                bullets = sentences[1:]
        if name or desc or bullets:
            projects.append(
                {
                    "name": name,
                    "description": desc,
                    "bullets": bullets,
                    "technologies": techs,
                }
            )
    return projects


def _merge_roles(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer richer role objects (more bullets) when merging sources."""
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for group in groups:
        for role in group:
            if not isinstance(role, dict):
                continue
            key = (
                f"{str(role.get('title') or '').strip().lower()}|"
                f"{str(role.get('company') or '').strip().lower()}"
            )
            if not key.strip("|"):
                key = f"anon_{len(order)}"
            bullets = [
                str(b).strip() for b in (role.get("bullets") or []) if str(b).strip()
            ]
            if key not in by_key:
                by_key[key] = {
                    "title": str(role.get("title") or ""),
                    "company": str(role.get("company") or ""),
                    "dates": str(role.get("dates") or ""),
                    "bullets": bullets,
                }
                order.append(key)
            else:
                existing = by_key[key]
                # Union unique bullets across sources (not prefer-richer-only),
                # so master_profile + section text both contribute without dupes.
                merged_bullets = list(existing.get("bullets") or [])
                seen = {b.lower() for b in merged_bullets}
                for b in bullets:
                    if b.lower() not in seen:
                        merged_bullets.append(b)
                        seen.add(b.lower())
                existing["bullets"] = merged_bullets
                if not existing.get("dates") and role.get("dates"):
                    existing["dates"] = str(role.get("dates") or "")
                if not existing.get("title") and role.get("title"):
                    existing["title"] = str(role.get("title") or "")
                if not existing.get("company") and role.get("company"):
                    existing["company"] = str(role.get("company") or "")
    return [by_key[k] for k in order]


def _merge_projects(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for group in groups:
        for proj in group:
            if not isinstance(proj, dict):
                continue
            key = str(proj.get("name") or "").strip().lower() or f"proj_{len(order)}"
            bullets = [
                str(b).strip() for b in (proj.get("bullets") or []) if str(b).strip()
            ]
            desc = str(proj.get("description") or "").strip()
            techs = [
                str(t).strip()
                for t in (proj.get("technologies") or [])
                if str(t).strip()
            ]
            if key not in by_key:
                by_key[key] = {
                    "name": str(proj.get("name") or ""),
                    "description": desc,
                    "bullets": bullets,
                    "technologies": techs,
                }
                order.append(key)
            else:
                existing = by_key[key]
                merged_bullets = list(existing.get("bullets") or [])
                seen = {b.lower() for b in merged_bullets}
                for b in bullets:
                    if b.lower() not in seen:
                        merged_bullets.append(b)
                        seen.add(b.lower())
                existing["bullets"] = merged_bullets
                if len(desc) > len(str(existing.get("description") or "")):
                    existing["description"] = desc
                merged_tech = list(existing.get("technologies") or [])
                for t in techs:
                    if t not in merged_tech:
                        merged_tech.append(t)
                existing["technologies"] = merged_tech
    return [by_key[k] for k in order]


def extract_structured_resume(
    cv_profile: dict[str, Any],
    source_documents: str | None = None,
) -> dict[str, Any]:
    """Reuse existing profile + raw CV sources; never overwrite originals.

    Preservation-first: pull full roles/bullets from master_profile and section
    text when ``experience.roles`` is missing (common aggregator shape).
    """
    raw_text = source_resume_text(cv_profile, source_documents)
    payload = build_candidate_payload(cv_profile, source_documents)
    experience = cv_profile.get("experience")
    if not isinstance(experience, dict):
        experience = {}
    roles = experience.get("roles") or experience.get("jobs") or []
    if not isinstance(roles, list):
        roles = []
    # Normalize bullet_points → bullets on any role-shaped dicts
    normalized_roles: list[dict[str, Any]] = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        bullets = [
            str(b).strip()
            for b in (role.get("bullets") or role.get("bullet_points") or [])
            if str(b).strip()
        ]
        normalized_roles.append(
            {
                "title": str(role.get("title") or role.get("role") or ""),
                "company": str(role.get("company") or role.get("organization") or ""),
                "dates": str(role.get("dates") or ""),
                "bullets": bullets,
            }
        )
    roles = normalized_roles

    projects = cv_profile.get("projects") or []
    if not isinstance(projects, list):
        projects = []

    education = cv_profile.get("education") or []
    if isinstance(education, dict):
        education = education.get("entries") or education.get("items") or [education]
    if not isinstance(education, list):
        education = []

    skills_block = cv_profile.get("skills") or {}
    if isinstance(skills_block, dict):
        skill_list: list[str] = []
        for value in skills_block.values():
            if isinstance(value, list):
                skill_list.extend(str(v) for v in value)
            elif value:
                skill_list.append(str(value))
    elif isinstance(skills_block, list):
        skill_list = [str(s) for s in skills_block]
    else:
        skill_list = []

    master = cv_profile.get("master_profile")
    if not isinstance(master, dict):
        master = {}

    master_roles = _roles_from_master(master)
    section_text = ""
    sections = cv_profile.get("sections")
    if isinstance(sections, dict):
        section_text = str(sections.get("experience") or "")
    section_roles = _roles_from_experience_section(section_text)
    roles = _merge_roles(roles, master_roles, section_roles)

    master_projects = _projects_from_master(master)
    normalized_projects = normalize_project_list(projects)
    section_projects: list[dict[str, Any]] = []
    if isinstance(sections, dict) and sections.get("projects"):
        # One project string per non-empty line when section is flat text
        proj_section = str(sections.get("projects") or "")
        for line in re.split(r"\n+", proj_section):
            if line.strip():
                section_projects.extend(normalize_project_list([line.strip()]))
    projects = _merge_projects(normalized_projects, master_projects, section_projects)

    # Education from master when profile education is only degree lists
    if master.get("education") and (
        not education
        or all(not isinstance(e, dict) or not e.get("institution") for e in education)
    ):
        edu_out: list[dict[str, Any]] = []
        for entry in master.get("education") or []:
            if not isinstance(entry, dict):
                continue
            edu_out.append(
                {
                    "degree": str(entry.get("degree") or ""),
                    "institution": str(entry.get("institution") or ""),
                    "field": str(entry.get("field") or ""),
                    "dates": str(entry.get("year") or entry.get("dates") or ""),
                }
            )
        if edu_out:
            education = edu_out

    years = years_from_experience_entries(
        [
            r if isinstance(r, dict) else {"dates": str(r)}
            for r in roles
        ]
    )
    if years is None:
        years = estimate_years_from_text(raw_text)
    profile_years = experience.get("years_of_experience_estimate")
    if years is None and profile_years is not None:
        try:
            years = float(profile_years)
        except (TypeError, ValueError):
            years = None

    bullet_count = sum(len(r.get("bullets") or []) for r in roles)
    project_content = sum(
        len(p.get("bullets") or []) + (1 if p.get("description") else 0) for p in projects
    )

    facts = {
        "raw_text": truncate_text(raw_text, OPENAI_CV_MAX_CHARS),
        "candidate_payload": truncate_text(payload, OPENAI_CV_MAX_CHARS),
        "contact": cv_profile.get("contact") if isinstance(cv_profile.get("contact"), dict) else {},
        "skills": skill_list,
        "display_skills": skill_list,
        "experience_roles": roles,
        "projects": projects,
        "education": education,
        "certifications": cv_profile.get("certifications") or [],
        "years_of_experience": years,
        "sparse": len((raw_text or "").strip()) < 120
        and not roles
        and not skill_list,
        "extraction_meta": {
            "experience_entries": len(roles),
            "experience_bullets": bullet_count,
            "projects": len(projects),
            "project_content_units": project_content,
            "skills": len(skill_list),
            "used_master_profile": bool(master),
        },
    }
    return facts


def resume_facts_for_prompt(facts: dict[str, Any]) -> str:
    """Compact structured facts string for LLM stages."""
    # Prefer structured roles/projects (full bullets) over truncated payload alone.
    structured = {
        "skills": (facts.get("display_skills") or facts.get("skills") or [])[:40],
        "experience": facts.get("experience_roles"),
        "projects": facts.get("projects"),
        "education": facts.get("education"),
        "years_of_experience": facts.get("years_of_experience"),
    }
    body = json.dumps(structured, ensure_ascii=False, indent=2)
    # Keep within prompt budget but never strip to empty experience/projects.
    if len(body) > 12000:
        body = body[:12000] + "\n…(truncated)"
    return body
