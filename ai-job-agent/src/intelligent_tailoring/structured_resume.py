"""Strict structured resume schema for content-producing agents.

Every agent that produces resume content must emit (or be normalized into)
this JSON shape before handoff. Stable ``id`` values are assigned once at
base-resume parse time and must be carried forward unchanged so downstream
agents cannot duplicate or mis-attach entries.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from intelligent_tailoring.canonical_resume import (
    looks_like_raw_data,
    normalize_education_entries,
    normalize_project_list,
)
from intelligent_tailoring.schemas import SchemaValidationError

STRUCTURED_RESUME_SCHEMA_VERSION = "structured_resume_v1"

# Field aliases accepted when normalizing LLM / legacy payloads.
_POSITION_KEYS = ("position", "title", "role", "job_title")
_ORG_KEYS = ("organization", "company", "employer", "org")
_DATE_KEYS = ("dateRange", "date_range", "dates", "period")
_PROJECT_TITLE_KEYS = ("title", "name", "project")
_ID_KEYS = ("id", "source_entry_id", "entry_id")


def _s(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        # Never stringify structures into resume fields.
        return ""
    return str(value).strip()


def _plain_string(value: Any, *, field: str) -> str:
    text = _s(value)
    if text and looks_like_raw_data(text):
        raise SchemaValidationError(
            f"{field} contains raw data structure rather than plain text"
        )
    return text


def _as_str_list(value: Any, *, drop_raw: bool = True) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, (dict, list)):
                continue
            text = _s(item)
            if not text:
                continue
            if drop_raw and looks_like_raw_data(text):
                continue
            out.append(text)
        return out
    if isinstance(value, str) and value.strip():
        if drop_raw and looks_like_raw_data(value):
            return []
        return [value.strip()]
    return []


def _first_key(entry: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in entry and _s(entry.get(key)):
            return _s(entry.get(key))
    return ""


def make_role_id(index: int) -> str:
    return f"role_{index}"


def make_project_id(index: int) -> str:
    return f"project_{index}"


def assign_stable_ids(resume_facts: dict[str, Any]) -> dict[str, Any]:
    """Stamp stable ``source_entry_id`` / ``id`` on base experience & projects.

    IDs are derived from parse order and never regenerated downstream. Safe to
    call multiple times — existing ids are preserved.
    """
    facts = deepcopy(resume_facts) if isinstance(resume_facts, dict) else {}
    roles = [
        r for r in (facts.get("experience_roles") or facts.get("experience") or [])
        if isinstance(r, dict)
    ]
    stamped_roles: list[dict[str, Any]] = []
    for idx, role in enumerate(roles):
        entry = dict(role)
        sid = _first_key(entry, _ID_KEYS) or make_role_id(idx)
        entry["id"] = sid
        entry["source_entry_id"] = sid
        stamped_roles.append(entry)
    facts["experience_roles"] = stamped_roles

    projects = normalize_project_list(facts.get("projects") or [])
    stamped_projects: list[dict[str, Any]] = []
    for idx, proj in enumerate(projects):
        if not isinstance(proj, dict):
            continue
        entry = dict(proj)
        sid = _first_key(entry, _ID_KEYS) or make_project_id(idx)
        entry["id"] = sid
        entry["source_entry_id"] = sid
        stamped_projects.append(entry)
    facts["projects"] = stamped_projects

    # Contact block always present (may be empty).
    contact = facts.get("contact") if isinstance(facts.get("contact"), dict) else {}
    facts["contact"] = {
        "name": _s(contact.get("name")),
        "location": _s(contact.get("location") or contact.get("city")),
        "phone": _s(contact.get("phone") or contact.get("tel")),
        "email": _s(contact.get("email")),
        "github": _s(contact.get("github")),
        "linkedin": _s(contact.get("linkedin")),
        "portfolio": _s(contact.get("portfolio") or contact.get("website")),
    }
    return facts


def stamp_ids_on_resume(
    resume: dict[str, Any],
    *,
    source_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure every experience/project entry has a stable id.

    When ``source_facts`` is provided, match by identity and reuse source ids.
    Otherwise assign by current index (rebuild / fallback path).
    """
    out = deepcopy(resume) if isinstance(resume, dict) else {}
    source = assign_stable_ids(source_facts) if source_facts else {}
    src_roles = [
        r for r in (source.get("experience_roles") or []) if isinstance(r, dict)
    ]
    src_projects = [
        p for p in (source.get("projects") or []) if isinstance(p, dict)
    ]

    def _role_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
        ta = _s(a.get("title") or a.get("position")).lower()
        tb = _s(b.get("title") or b.get("position")).lower()
        ca = _s(a.get("company") or a.get("organization")).lower()
        cb = _s(b.get("company") or b.get("organization")).lower()
        if not ta or not tb:
            return False
        title_ok = ta == tb or ta in tb or tb in ta
        if not title_ok:
            return False
        if not ca or not cb:
            return True
        return ca == cb or ca in cb or cb in ca

    def _proj_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
        ta = _s(a.get("name") or a.get("title")).lower()
        tb = _s(b.get("name") or b.get("title")).lower()
        if not ta or not tb:
            return False
        return ta == tb or ta in tb or tb in ta

    used_role_ids: set[str] = set()
    experience: list[dict[str, Any]] = []
    for idx, entry in enumerate(out.get("experience") or []):
        if not isinstance(entry, dict):
            continue
        fixed = dict(entry)
        sid = _first_key(fixed, _ID_KEYS)
        # Drop stale ids that no longer match the source role identity —
        # index-based ids survive reordering and cause cross-entry restores.
        if sid and src_roles:
            src_hit = next(
                (
                    src
                    for src in src_roles
                    if _s(src.get("id") or src.get("source_entry_id")) == sid
                ),
                None,
            )
            if src_hit is not None and not _role_match(fixed, src_hit):
                sid = ""
            elif src_hit is None and any(
                _role_match(fixed, src) for src in src_roles
            ):
                # Id is unknown but a name match exists — rematch below.
                sid = ""
        if not sid and src_roles:
            for src in src_roles:
                cand = _s(src.get("id") or src.get("source_entry_id"))
                if cand and cand not in used_role_ids and _role_match(fixed, src):
                    sid = cand
                    break
        if not sid:
            sid = (
                f"unmapped_role_{idx}"
                if src_roles
                else make_role_id(idx)
            )
        base_sid = sid
        n = 2
        while sid in used_role_ids:
            sid = f"{base_sid}__dup{n}"
            n += 1
        used_role_ids.add(sid)
        fixed["id"] = sid
        fixed["source_entry_id"] = sid
        experience.append(fixed)
    out["experience"] = experience

    used_proj_ids: set[str] = set()
    projects: list[dict[str, Any]] = []
    for idx, entry in enumerate(out.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        fixed = dict(entry)
        sid = _first_key(fixed, _ID_KEYS)
        if sid and src_projects:
            src_hit = next(
                (
                    src
                    for src in src_projects
                    if _s(src.get("id") or src.get("source_entry_id")) == sid
                ),
                None,
            )
            if src_hit is not None and not _proj_match(fixed, src_hit):
                sid = ""
            elif src_hit is None and any(
                _proj_match(fixed, src) for src in src_projects
            ):
                sid = ""
        if not sid and src_projects:
            for src in src_projects:
                cand = _s(src.get("id") or src.get("source_entry_id"))
                if cand and cand not in used_proj_ids and _proj_match(fixed, src):
                    sid = cand
                    break
        if not sid:
            # Do not invent project_{idx} when source facts exist — those ids
            # collide with real source entries after reordering.
            sid = (
                f"unmapped_project_{idx}"
                if src_projects
                else make_project_id(idx)
            )
        base_sid = sid
        n = 2
        while sid in used_proj_ids:
            sid = f"{base_sid}__dup{n}"
            n += 1
        used_proj_ids.add(sid)
        fixed["id"] = sid
        fixed["source_entry_id"] = sid
        projects.append(fixed)
    out["projects"] = projects
    return out


def skills_list_to_dict(skills: Any) -> dict[str, list[str]]:
    """Convert 'Category: a, b' lines (or a dict) into a skills dict."""
    if isinstance(skills, dict):
        out: dict[str, list[str]] = {}
        for key, value in skills.items():
            cat = _s(key) or "Skills"
            atoms = _as_str_list(value)
            if atoms:
                out[cat] = atoms
        return out
    out = {}
    for line in _as_str_list(skills):
        if ":" in line:
            cat, rest = line.split(":", 1)
            cat = cat.strip() or "Skills"
            atoms = [a.strip() for a in re.split(r"[,;|/]", rest) if a.strip()]
            if atoms:
                out.setdefault(cat, [])
                for atom in atoms:
                    if atom not in out[cat]:
                        out[cat].append(atom)
        else:
            out.setdefault("Skills", [])
            if line not in out["Skills"]:
                out["Skills"].append(line)
    return out


def skills_dict_to_list(skills: Any) -> list[str]:
    """Convert skills dict back to categorized display lines for renderers."""
    if isinstance(skills, list):
        return [str(s).strip() for s in skills if str(s).strip()]
    if not isinstance(skills, dict):
        return []
    lines: list[str] = []
    for cat, atoms in skills.items():
        cat_s = _s(cat) or "Skills"
        atom_list = _as_str_list(atoms)
        if atom_list:
            lines.append(f"{cat_s}: {', '.join(atom_list)}")
    return lines


def normalize_contact(contact: Any, *, name: str = "") -> dict[str, Any]:
    src = contact if isinstance(contact, dict) else {}
    return {
        "name": _s(src.get("name")) or _s(name),
        "location": _s(src.get("location") or src.get("city")),
        "phone": _s(src.get("phone") or src.get("tel")),
        "email": _s(src.get("email")),
        "github": _s(src.get("github")) or None,
        "linkedin": _s(src.get("linkedin")) or None,
        "portfolio": _s(src.get("portfolio") or src.get("website")) or None,
    }


def to_structured_resume(
    resume: dict[str, Any] | None,
    *,
    source_facts: dict[str, Any] | None = None,
    require_ids: bool = True,
) -> dict[str, Any]:
    """Normalize any pipeline/LLM resume dict into the strict structured schema."""
    data = stamp_ids_on_resume(
        resume if isinstance(resume, dict) else {},
        source_facts=source_facts,
    )
    source = assign_stable_ids(source_facts) if source_facts else {}
    src_contact = source.get("contact") if isinstance(source.get("contact"), dict) else {}

    name = _s(data.get("name")) or _s(src_contact.get("name"))
    contact = normalize_contact(
        data.get("contact") if isinstance(data.get("contact"), dict) else src_contact,
        name=name,
    )
    if not contact.get("name") and name:
        contact["name"] = name

    title = _plain_string(
        data.get("title") or data.get("professional_title"),
        field="title",
    )
    summary = _plain_string(
        data.get("summary") or data.get("professional_summary"),
        field="summary",
    )

    experience: list[dict[str, Any]] = []
    for idx, entry in enumerate(data.get("experience") or []):
        if not isinstance(entry, dict):
            continue
        sid = _first_key(entry, _ID_KEYS) or make_role_id(idx)
        if require_ids and not sid:
            raise SchemaValidationError(f"experience[{idx}] missing stable id")
        position = _plain_string(
            _first_key(entry, _POSITION_KEYS),
            field=f"experience[{idx}].position",
        )
        organization = _plain_string(
            _first_key(entry, _ORG_KEYS),
            field=f"experience[{idx}].organization",
        )
        date_range = _plain_string(
            _first_key(entry, _DATE_KEYS),
            field=f"experience[{idx}].dateRange",
        )
        # Keep raw-looking bullets so deterministic validation can reject them
        # (do not silently drop — that hides leaks from the gate).
        bullets = _as_str_list(entry.get("bullets"), drop_raw=False)
        experience.append(
            {
                "id": sid,
                "position": position,
                "organization": organization,
                "dateRange": date_range,
                "bullets": bullets,
            }
        )

    projects: list[dict[str, Any]] = []
    for idx, entry in enumerate(data.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        sid = _first_key(entry, _ID_KEYS) or make_project_id(idx)
        proj_title = _plain_string(
            _first_key(entry, _PROJECT_TITLE_KEYS),
            field=f"projects[{idx}].title",
        )
        description = _plain_string(
            entry.get("description"),
            field=f"projects[{idx}].description",
        )
        bullets = _as_str_list(entry.get("bullets"), drop_raw=False)
        projects.append(
            {
                "id": sid,
                "title": proj_title,
                "description": description,
                "bullets": bullets,
            }
        )

    skills = skills_list_to_dict(data.get("skills"))
    # Drop raw-data skill atoms
    clean_skills: dict[str, list[str]] = {}
    for cat, atoms in skills.items():
        kept = [a for a in atoms if a and not looks_like_raw_data(a)]
        if kept:
            clean_skills[_s(cat) or "Skills"] = kept

    education_raw = normalize_education_entries(data.get("education"))
    if not education_raw and source.get("education"):
        education_raw = normalize_education_entries(source.get("education"))
    education: list[dict[str, Any]] = []
    for idx, entry in enumerate(education_raw):
        if not isinstance(entry, dict):
            continue
        education.append(
            {
                "degree": _plain_string(
                    entry.get("degree"), field=f"education[{idx}].degree"
                ),
                "institution": _plain_string(
                    entry.get("institution"), field=f"education[{idx}].institution"
                ),
                "fieldOfStudy": _plain_string(
                    entry.get("field") or entry.get("fieldOfStudy") or entry.get("field_of_study"),
                    field=f"education[{idx}].fieldOfStudy",
                )
                or None,
                "dateRange": _plain_string(
                    entry.get("dates") or entry.get("dateRange") or entry.get("date_range"),
                    field=f"education[{idx}].dateRange",
                )
                or None,
            }
        )

    return {
        "schema_version": STRUCTURED_RESUME_SCHEMA_VERSION,
        "name": contact.get("name") or name,
        "contact": contact,
        "title": title,
        "summary": summary,
        "experience": experience,
        "projects": projects,
        "skills": clean_skills,
        "education": education,
        "certifications": [
            text
            for c in (data.get("certifications") or [])
            for text in [
                _s(c)
                if not isinstance(c, dict)
                else _s(c.get("name") or c.get("title"))
            ]
            if text
        ],
    }


def structured_to_pipeline_resume(structured: dict[str, Any]) -> dict[str, Any]:
    """Convert structured schema back to the pipeline/renderer resume dict."""
    data = structured if isinstance(structured, dict) else {}
    contact = normalize_contact(data.get("contact"), name=_s(data.get("name")))
    experience = []
    for entry in data.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        sid = _s(entry.get("id") or entry.get("source_entry_id"))
        experience.append(
            {
                "id": sid,
                "source_entry_id": sid,
                "title": _s(entry.get("position") or entry.get("title")),
                "company": _s(entry.get("organization") or entry.get("company")),
                "dates": _s(entry.get("dateRange") or entry.get("dates")),
                "bullets": _as_str_list(entry.get("bullets")),
            }
        )
    projects = []
    for entry in data.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        sid = _s(entry.get("id") or entry.get("source_entry_id"))
        projects.append(
            {
                "id": sid,
                "source_entry_id": sid,
                "name": _s(entry.get("title") or entry.get("name")),
                "description": _s(entry.get("description")),
                "bullets": _as_str_list(entry.get("bullets")),
                "technologies": _as_str_list(entry.get("technologies")),
            }
        )
    education = []
    for entry in data.get("education") or []:
        if not isinstance(entry, dict):
            continue
        education.append(
            {
                "degree": _s(entry.get("degree")),
                "institution": _s(entry.get("institution")),
                "field": _s(entry.get("fieldOfStudy") or entry.get("field")),
                "dates": _s(entry.get("dateRange") or entry.get("dates")),
            }
        )
    summary = _s(data.get("summary") or data.get("professional_summary"))
    title = _s(data.get("title") or data.get("professional_title"))
    return {
        "name": _s(data.get("name")) or contact.get("name") or "",
        "contact": contact,
        "professional_title": title,
        "professional_summary": summary,
        "summary": summary,
        "skills": skills_dict_to_list(data.get("skills")),
        "experience": experience,
        "projects": projects,
        "education": education,
        "certifications": list(data.get("certifications") or []),
    }


def base_source_ids(resume_facts: dict[str, Any] | None) -> dict[str, set[str]]:
    """Return required experience/project ids from the base parse."""
    facts = assign_stable_ids(resume_facts or {})
    role_ids = {
        _s(r.get("id") or r.get("source_entry_id"))
        for r in (facts.get("experience_roles") or [])
        if isinstance(r, dict)
        and (
            _s(r.get("title") or r.get("position"))
            or _s(r.get("company") or r.get("organization"))
        )
        and _as_str_list(r.get("bullets"))
    }
    project_ids = {
        _s(p.get("id") or p.get("source_entry_id"))
        for p in (facts.get("projects") or [])
        if isinstance(p, dict)
        and _s(p.get("name") or p.get("title"))
        and (
            _s(p.get("description"))
            or _as_str_list(p.get("bullets"))
        )
    }
    return {
        "experience_ids": {x for x in role_ids if x},
        "project_ids": {x for x in project_ids if x},
    }


def count_content_units(resume: dict[str, Any] | None) -> dict[str, int]:
    """Count bullets + non-empty project descriptions for fullness checks."""
    data = resume if isinstance(resume, dict) else {}
    # Accept either structured or pipeline shape
    exp = data.get("experience") or []
    proj = data.get("projects") or []
    exp_bullets = 0
    for entry in exp:
        if isinstance(entry, dict):
            exp_bullets += len(_as_str_list(entry.get("bullets")))
    proj_units = 0
    for entry in proj:
        if not isinstance(entry, dict):
            continue
        bullets = _as_str_list(entry.get("bullets"))
        desc = _s(entry.get("description"))
        proj_units += len(bullets)
        if desc:
            proj_units += 1
    return {
        "experience_bullets": exp_bullets,
        "project_units": proj_units,
        "total_units": exp_bullets + proj_units,
        "experience_entries": len([e for e in exp if isinstance(e, dict)]),
        "project_entries": len([p for p in proj if isinstance(p, dict)]),
    }


def validate_structured_schema(structured: dict[str, Any]) -> None:
    """Raise SchemaValidationError when the structured object is malformed."""
    if not isinstance(structured, dict):
        raise SchemaValidationError("structured resume must be an object")
    for key in ("contact", "summary", "experience", "projects", "skills", "education"):
        if key not in structured:
            raise SchemaValidationError(f"structured resume missing key: {key}")
    if not isinstance(structured.get("contact"), dict):
        raise SchemaValidationError("contact must be an object")
    if not isinstance(structured.get("experience"), list):
        raise SchemaValidationError("experience must be a list")
    if not isinstance(structured.get("projects"), list):
        raise SchemaValidationError("projects must be a list")
    if not isinstance(structured.get("skills"), dict):
        raise SchemaValidationError("skills must be an object of category → list")
    if not isinstance(structured.get("education"), list):
        raise SchemaValidationError("education must be a list")
    for idx, entry in enumerate(structured["experience"]):
        if not isinstance(entry, dict):
            raise SchemaValidationError(f"experience[{idx}] must be an object")
        if not _s(entry.get("id")):
            raise SchemaValidationError(f"experience[{idx}] missing id")
        for field in ("position", "organization", "dateRange"):
            val = entry.get(field)
            if val is not None and not isinstance(val, str):
                raise SchemaValidationError(
                    f"experience[{idx}].{field} must be a plain string"
                )
            if isinstance(val, str) and looks_like_raw_data(val):
                raise SchemaValidationError(
                    f"experience[{idx}].{field} contains raw data structure"
                )
        if not isinstance(entry.get("bullets"), list):
            raise SchemaValidationError(f"experience[{idx}].bullets must be a list")
        for bi, bullet in enumerate(entry.get("bullets") or []):
            if not isinstance(bullet, str):
                raise SchemaValidationError(
                    f"experience[{idx}].bullets[{bi}] must be a plain string"
                )
            if looks_like_raw_data(bullet):
                raise SchemaValidationError(
                    f"experience[{idx}].bullets[{bi}] contains raw data structure"
                )
    for idx, entry in enumerate(structured["projects"]):
        if not isinstance(entry, dict):
            raise SchemaValidationError(f"projects[{idx}] must be an object")
        if not _s(entry.get("id")):
            raise SchemaValidationError(f"projects[{idx}] missing id")
        for field in ("title", "description"):
            val = entry.get(field)
            if val is not None and not isinstance(val, str):
                raise SchemaValidationError(
                    f"projects[{idx}].{field} must be a plain string"
                )
            if isinstance(val, str) and looks_like_raw_data(val):
                raise SchemaValidationError(
                    f"projects[{idx}].{field} contains raw data structure"
                )
        if not isinstance(entry.get("bullets"), list):
            raise SchemaValidationError(f"projects[{idx}].bullets must be a list")
    summary = structured.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise SchemaValidationError("summary must be a plain string")
    if isinstance(summary, str) and looks_like_raw_data(summary):
        raise SchemaValidationError("summary contains raw data structure")


STRUCTURED_RESUME_JSON_SCHEMA_HINT = """
{
  "name": "string",
  "contact": {
    "location": "string",
    "phone": "string",
    "email": "string",
    "github": "string|null",
    "linkedin": "string|null"
  },
  "title": "string",
  "summary": "string",
  "experience": [
    {
      "id": "string (stable, from base resume — never regenerate)",
      "position": "string",
      "organization": "string",
      "dateRange": "string",
      "bullets": ["string", "..."]
    }
  ],
  "projects": [
    {
      "id": "string (stable)",
      "title": "string",
      "description": "string",
      "bullets": ["string", "..."]
    }
  ],
  "skills": { "category name": ["string", "..."] },
  "education": [
    {
      "degree": "string",
      "institution": "string",
      "fieldOfStudy": "string",
      "dateRange": "string|null"
    }
  ]
}
""".strip()
