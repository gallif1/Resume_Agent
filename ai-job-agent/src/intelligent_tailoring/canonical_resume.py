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

# Aggregator / analyzer education shape (lists, not resume entries).
_AGGREGATOR_EDU_KEYS = frozenset(
    {
        "degrees",
        "institutions",
        "fields_of_study",
        "fieldsofstudy",
        "fields",
        "entries",
        "items",
    }
)

# Patterns typical of Python/JSON dict/list repr leaking into resume text.
_RAW_DATA_PATTERNS = (
    re.compile(r"^\s*\{['\"]?\w+['\"]?\s*:"),
    re.compile(r"['\"]:\s*\["),
    re.compile(r"\{['\"]degrees['\"]"),
    re.compile(r"\{['\"]institutions['\"]"),
    re.compile(r"\{['\"]fields_?of_?study['\"]"),
    re.compile(r"\[['\"][^'\"]{1,40}['\"]\s*,\s*['\"]"),
)


def looks_like_raw_data(text: str) -> bool:
    """True when text looks like a stringified dict/list, not human resume prose."""
    t = (text or "").strip()
    if not t:
        return False
    if ("{" in t and "}" in t) and (":" in t) and ("'" in t or '"' in t):
        for pat in _RAW_DATA_PATTERNS:
            if pat.search(t):
                return True
        # Generic Python-repr dict: {'key': ...}
        if re.search(r"\{['\"][\w_]+['\"]\s*:", t):
            return True
    if t.startswith("{'") or t.startswith('{"') or t.startswith("[{"):
        return True
    return False


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _try_parse_education_dict_repr(text: str) -> dict[str, Any] | None:
    """Best-effort recovery when a dict was stringified into a degree field."""
    raw = (text or "").strip()
    if not looks_like_raw_data(raw):
        return None
    try:
        import ast

        parsed = ast.literal_eval(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    # Loose regex fallback for the common aggregator shape
    degrees = re.findall(r"['\"]degrees['\"]\s*:\s*\[([^\]]*)\]", raw, flags=re.I)
    institutions = re.findall(
        r"['\"]institutions['\"]\s*:\s*\[([^\]]*)\]", raw, flags=re.I
    )
    fields = re.findall(
        r"['\"]fields_?of_?study['\"]\s*:\s*\[([^\]]*)\]", raw, flags=re.I
    )
    if not (degrees or institutions or fields):
        return None

    def _items(blob: str) -> list[str]:
        return [m.strip() for m in re.findall(r"['\"]([^'\"]+)['\"]", blob) if m.strip()]

    return {
        "degrees": _items(degrees[0]) if degrees else [],
        "institutions": _items(institutions[0]) if institutions else [],
        "fields_of_study": _items(fields[0]) if fields else [],
    }


def _education_entry_from_parts(
    *,
    degree: str = "",
    institution: str = "",
    field: str = "",
    dates: str = "",
) -> dict[str, str]:
    deg = (degree or "").strip()
    inst = (institution or "").strip()
    fld = (field or "").strip()
    # Avoid "B.Sc in Computer Science" when degree already embeds the field
    if deg and fld and fld.lower() not in deg.lower():
        deg_out = f"{deg} in {fld}"
    else:
        deg_out = deg or (f"Degree in {fld}" if fld else "")
    return {
        "degree": deg_out,
        "institution": inst,
        "field": fld if fld and fld.lower() not in deg_out.lower() else "",
        "dates": (dates or "").strip(),
    }


def normalize_education_entries(education: Any) -> list[dict[str, Any]]:
    """Normalize aggregator dicts / mixed lists into renderable education entries.

    Accepts:
    - list of entry dicts: {degree, institution, field, dates}
    - aggregator dict: {degrees: [], institutions: [], fields_of_study: []}
    - a single aggregator dict accidentally wrapped as a list item
    Never returns stringified dicts as degree/institution text.
    """
    if education is None:
        return []

    # Aggregator object at the top level
    if isinstance(education, dict):
        keys = {str(k).lower() for k in education.keys()}
        if education.get("entries") or education.get("items"):
            return normalize_education_entries(
                education.get("entries") or education.get("items")
            )
        if keys & {"degrees", "institutions", "fields_of_study", "fieldsofstudy", "fields"}:
            degrees = _as_str_list(education.get("degrees"))
            institutions = _as_str_list(education.get("institutions"))
            fields = _as_str_list(
                education.get("fields_of_study")
                or education.get("fieldsofstudy")
                or education.get("fields")
            )
            n = max(len(degrees), len(institutions), len(fields), 0)
            if n == 0:
                return []
            # If only one of each, pair into a single entry; else zip by index
            out: list[dict[str, Any]] = []
            for i in range(n):
                out.append(
                    _education_entry_from_parts(
                        degree=degrees[i] if i < len(degrees) else (degrees[0] if len(degrees) == 1 and n > 1 else ""),
                        institution=institutions[i]
                        if i < len(institutions)
                        else (institutions[0] if len(institutions) == 1 and n > 1 else ""),
                        field=fields[i]
                        if i < len(fields)
                        else (fields[0] if len(fields) == 1 and n > 1 else ""),
                    )
                )
            # Collapse identical zip artifacts when one degree + one institution
            if len(degrees) == 1 and len(institutions) == 1 and len(fields) <= 1:
                return [
                    _education_entry_from_parts(
                        degree=degrees[0],
                        institution=institutions[0],
                        field=fields[0] if fields else "",
                    )
                ]
            # Dedupe empty-ish
            cleaned = [
                e
                for e in out
                if str(e.get("degree") or "").strip()
                or str(e.get("institution") or "").strip()
            ]
            return cleaned
        # Single entry-shaped dict
        if any(education.get(k) for k in ("degree", "institution", "field", "school")):
            return [
                _education_entry_from_parts(
                    degree=str(education.get("degree") or ""),
                    institution=str(
                        education.get("institution") or education.get("school") or ""
                    ),
                    field=str(education.get("field") or ""),
                    dates=str(
                        education.get("dates")
                        or education.get("year")
                        or education.get("graduation_year")
                        or ""
                    ),
                )
            ]
        # Unknown dict — do not stringify
        return []

    if not isinstance(education, list):
        text = str(education).strip()
        if not text or looks_like_raw_data(text):
            recovered = _try_parse_education_dict_repr(text)
            return normalize_education_entries(recovered) if recovered else []
        return [{"degree": text, "institution": "", "field": "", "dates": ""}]

    out: list[dict[str, Any]] = []
    for entry in education:
        if isinstance(entry, dict):
            keys = {str(k).lower() for k in entry.keys()}
            # Aggregator blob as a list item
            if keys & {"degrees", "institutions", "fields_of_study", "fieldsofstudy"} and not (
                entry.get("degree") or entry.get("institution")
            ):
                out.extend(normalize_education_entries(entry))
                continue
            degree = str(entry.get("degree") or "").strip()
            institution = str(
                entry.get("institution") or entry.get("school") or ""
            ).strip()
            field = str(entry.get("field") or "").strip()
            dates = str(
                entry.get("dates")
                or entry.get("year")
                or entry.get("graduation_year")
                or ""
            ).strip()
            # Recover when degree itself is a stringified aggregator dict
            if looks_like_raw_data(degree):
                recovered = _try_parse_education_dict_repr(degree)
                if recovered:
                    out.extend(normalize_education_entries(recovered))
                    continue
                degree = ""
            if looks_like_raw_data(institution):
                recovered = _try_parse_education_dict_repr(institution)
                if recovered:
                    out.extend(normalize_education_entries(recovered))
                    continue
                institution = ""
            if not degree and not institution and not field:
                continue
            out.append(
                _education_entry_from_parts(
                    degree=degree,
                    institution=institution,
                    field=field,
                    dates=dates,
                )
            )
        else:
            text = str(entry).strip()
            if not text:
                continue
            if looks_like_raw_data(text):
                recovered = _try_parse_education_dict_repr(text)
                if recovered:
                    out.extend(normalize_education_entries(recovered))
                continue
            out.append(
                {"degree": text, "institution": "", "field": "", "dates": ""}
            )

    # Dedupe by (degree, institution)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for entry in out:
        key = f"{str(entry.get('degree') or '').lower()}|{str(entry.get('institution') or '').lower()}"
        if key in seen or key == "|":
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def format_education_entry(entry: dict[str, Any]) -> dict[str, str]:
    """Return safe display fields for one education entry (never raw structures)."""
    if not isinstance(entry, dict):
        text = str(entry or "").strip()
        if looks_like_raw_data(text):
            recovered = normalize_education_entries(text)
            return format_education_entry(recovered[0]) if recovered else {
                "degree": "",
                "institution": "",
                "field": "",
                "dates": "",
                "heading": "",
            }
        return {
            "degree": text,
            "institution": "",
            "field": "",
            "dates": "",
            "heading": text,
        }
    normalized = normalize_education_entries([entry])
    if not normalized:
        return {
            "degree": "",
            "institution": "",
            "field": "",
            "dates": "",
            "heading": "",
        }
    e = normalized[0]
    degree = str(e.get("degree") or "").strip()
    institution = str(e.get("institution") or "").strip()
    field = str(e.get("field") or "").strip()
    dates = str(e.get("dates") or "").strip()
    if looks_like_raw_data(degree) or looks_like_raw_data(institution):
        return {
            "degree": "",
            "institution": "",
            "field": "",
            "dates": "",
            "heading": "",
        }
    heading = degree or institution
    return {
        "degree": degree,
        "institution": institution,
        "field": field,
        "dates": dates,
        "heading": heading,
    }


def collect_resume_bullet_texts(resume: dict[str, Any] | None) -> list[str]:
    """All experience/project bullet (+ description) strings from a resume dict."""
    data = resume if isinstance(resume, dict) else {}
    texts: list[str] = []
    for entry in list(data.get("experience") or []) + list(data.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        for b in entry.get("bullets") or []:
            t = str(b).strip()
            if t:
                texts.append(t)
        desc = str(entry.get("description") or "").strip()
        if desc:
            texts.append(desc)
    return texts


def text_overlap_ratio(a: str, b: str) -> float:
    """Jaccard token overlap for near-duplicate detection (0–1)."""
    ta = {t for t in re.findall(r"[a-z0-9+#.]{3,}", (a or "").lower())}
    tb = {t for t in re.findall(r"[a-z0-9+#.]{3,}", (b or "").lower())}
    if not ta or not tb:
        na, nb = re.sub(r"\s+", " ", (a or "").strip().lower()), re.sub(
            r"\s+", " ", (b or "").strip().lower()
        )
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def find_raw_data_leaks(resume: dict[str, Any] | None) -> list[str]:
    """Locate user-visible fields that still contain stringified structures."""
    data = resume if isinstance(resume, dict) else {}
    leaks: list[str] = []
    summary = str(data.get("professional_summary") or data.get("summary") or "")
    if looks_like_raw_data(summary):
        leaks.append("summary")
    for i, line in enumerate(data.get("skills") or []):
        if looks_like_raw_data(str(line)):
            leaks.append(f"skills[{i}]")
    for i, entry in enumerate(data.get("experience") or []):
        if not isinstance(entry, dict):
            continue
        for key in ("title", "company", "dates"):
            if looks_like_raw_data(str(entry.get(key) or "")):
                leaks.append(f"experience[{i}].{key}")
        for j, b in enumerate(entry.get("bullets") or []):
            if looks_like_raw_data(str(b)):
                leaks.append(f"experience[{i}].bullets[{j}]")
    for i, entry in enumerate(data.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        for key in ("name", "description"):
            if looks_like_raw_data(str(entry.get(key) or "")):
                leaks.append(f"projects[{i}].{key}")
        for j, b in enumerate(entry.get("bullets") or []):
            if looks_like_raw_data(str(b)):
                leaks.append(f"projects[{i}].bullets[{j}]")
    for i, entry in enumerate(data.get("education") or []):
        if not isinstance(entry, dict):
            if looks_like_raw_data(str(entry)):
                leaks.append(f"education[{i}]")
            continue
        for key in ("degree", "institution", "field", "dates"):
            if looks_like_raw_data(str(entry.get(key) or "")):
                leaks.append(f"education[{i}].{key}")
    return leaks


def sanitize_raw_data_fields(resume: dict[str, Any]) -> dict[str, Any]:
    """Strip/repair stringified structures from all user-visible resume fields."""
    out = deepcopy(resume) if isinstance(resume, dict) else {}
    summary = str(out.get("professional_summary") or out.get("summary") or "")
    if looks_like_raw_data(summary):
        out["professional_summary"] = ""
        out["summary"] = ""

    skills_clean = []
    for line in out.get("skills") or []:
        text = str(line).strip()
        if text and not looks_like_raw_data(text):
            skills_clean.append(text)
    out["skills"] = skills_clean

    for section in ("experience", "projects"):
        cleaned_section = []
        for entry in out.get(section) or []:
            if not isinstance(entry, dict):
                continue
            fixed = dict(entry)
            for key in ("title", "company", "name", "dates", "description"):
                if key in fixed and looks_like_raw_data(str(fixed.get(key) or "")):
                    fixed[key] = ""
            bullets = []
            for b in fixed.get("bullets") or []:
                text = str(b).strip()
                if text and not looks_like_raw_data(text):
                    bullets.append(text)
            fixed["bullets"] = bullets
            cleaned_section.append(fixed)
        out[section] = cleaned_section

    out["education"] = normalize_education_entries(out.get("education"))
    return out


def ensure_minimum_content_from_source(
    tailored: dict[str, Any],
    *,
    resume_facts: dict[str, Any],
    min_bullets_per_role: int = 1,
    min_bullets_per_project: int = 1,
) -> dict[str, Any]:
    """Guarantee every source Experience role and Project appears by title.

    Tailoring may shorten bullets and reorder entries, but must never omit a
    real position or project from the base resume. Skills atoms from the source
    are also restored into the Skills section when missing.
    """
    out = sanitize_raw_data_fields(deepcopy(tailored) if isinstance(tailored, dict) else {})
    source_roles = [
        r for r in (resume_facts.get("experience_roles") or resume_facts.get("experience") or [])
        if isinstance(r, dict)
        and (
            str(r.get("title") or "").strip()
            or str(r.get("company") or "").strip()
        )
        and [str(b).strip() for b in (r.get("bullets") or []) if str(b).strip()]
    ]
    source_projects = normalize_project_list(resume_facts.get("projects") or [])
    source_projects = [
        p
        for p in source_projects
        if isinstance(p, dict)
        and str(p.get("name") or "").strip()
        and (
            str(p.get("description") or "").strip()
            or [str(b).strip() for b in (p.get("bullets") or []) if str(b).strip()]
        )
    ]

    tailored_exp = [e for e in (out.get("experience") or []) if isinstance(e, dict)]
    # Index existing by normalized title
    present_titles = {
        str(e.get("title") or "").strip().lower()
        for e in tailored_exp
        if str(e.get("title") or "").strip()
    }
    present_companies = {
        (
            str(e.get("title") or "").strip().lower(),
            str(e.get("company") or "").strip().lower(),
        )
        for e in tailored_exp
    }

    merged_exp = list(tailored_exp)
    for role in source_roles:
        title = str(role.get("title") or "").strip()
        company = str(role.get("company") or "").strip()
        t_key = title.lower()
        pair = (t_key, company.lower())
        already = False
        if t_key and t_key in present_titles:
            already = True
        elif pair in present_companies:
            already = True
        else:
            # Fuzzy: title contained
            for e in merged_exp:
                et = str(e.get("title") or "").strip().lower()
                if t_key and et and (t_key == et or t_key in et or et in t_key):
                    already = True
                    # Ensure it has at least one bullet
                    bullets = [
                        str(b).strip() for b in (e.get("bullets") or []) if str(b).strip()
                    ]
                    if len(bullets) < min_bullets_per_role:
                        src_b = [
                            str(b).strip()
                            for b in (role.get("bullets") or [])
                            if str(b).strip()
                        ]
                        if src_b:
                            e["bullets"] = src_b[: max(min_bullets_per_role, 1)]
                    break
        if already:
            continue
        bullets = [str(b).strip() for b in (role.get("bullets") or []) if str(b).strip()]
        if not bullets:
            continue
        merged_exp.append(
            {
                "title": title,
                "company": company,
                "dates": str(role.get("dates") or ""),
                "bullets": bullets[: max(min_bullets_per_role, 1)],
            }
        )
        present_titles.add(t_key)
    out["experience"] = merged_exp

    tailored_proj = [p for p in (out.get("projects") or []) if isinstance(p, dict)]
    present_names = {
        str(p.get("name") or "").strip().lower()
        for p in tailored_proj
        if str(p.get("name") or "").strip()
    }
    merged_proj = list(tailored_proj)
    for proj in source_projects:
        name = str(proj.get("name") or "").strip()
        n_key = name.lower()
        already = False
        if n_key and n_key in present_names:
            already = True
            # Ensure content
            for e in merged_proj:
                en = str(e.get("name") or "").strip().lower()
                if n_key and en and (n_key == en or n_key in en or en in n_key):
                    bullets = [
                        str(b).strip() for b in (e.get("bullets") or []) if str(b).strip()
                    ]
                    desc = str(e.get("description") or "").strip()
                    if len(bullets) < min_bullets_per_project:
                        src_b = [
                            str(b).strip()
                            for b in (proj.get("bullets") or [])
                            if str(b).strip()
                        ]
                        src_d = str(proj.get("description") or "").strip()
                        if src_b:
                            from intelligent_tailoring.services.one_page_compressor import (
                                _dedupe_similar,
                            )

                            e["bullets"] = _dedupe_similar(bullets + src_b)[
                                : max(min_bullets_per_project, len(bullets), 1)
                            ]
                        if not desc and src_d:
                            e["description"] = src_d
                    break
        else:
            for e in merged_proj:
                en = str(e.get("name") or "").strip().lower()
                if n_key and en and (n_key == en or n_key in en or en in n_key):
                    already = True
                    break
        if already:
            continue
        bullets = [str(b).strip() for b in (proj.get("bullets") or []) if str(b).strip()]
        desc = str(proj.get("description") or "").strip()
        if not bullets and not desc:
            continue
        merged_proj.append(
            {
                "name": name,
                "description": desc,
                "bullets": bullets[: max(min_bullets_per_project, 1)] if bullets else [],
                "technologies": list(proj.get("technologies") or []),
            }
        )
        present_names.add(n_key)
    out["projects"] = merged_proj

    # Skills: restore every source atom into categorized lines
    source_skills = [
        str(s).strip()
        for s in (resume_facts.get("display_skills") or resume_facts.get("skills") or [])
        if str(s).strip()
    ]
    if source_skills:
        from intelligent_tailoring.skill_taxonomy import normalize_skill_lines

        current = [str(s).strip() for s in (out.get("skills") or []) if str(s).strip()]
        # Merge source + current then normalize — preserves atoms, reorders by JD later
        out["skills"] = normalize_skill_lines(
            list(dict.fromkeys(current + source_skills))
        )

    # Education: always normalize from tailored or source facts
    edu = normalize_education_entries(out.get("education"))
    if not edu:
        edu = normalize_education_entries(resume_facts.get("education"))
    out["education"] = edu

    return drop_empty_shell_entries(out)


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
    resume_facts: dict[str, Any] | None = None,
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

    # Raw structured data must never reach the user-facing resume
    for leak in find_raw_data_leaks(resume):
        failures.append(f"raw_data_leak:{leak}")

    # Summary must not copy experience/project bullets verbatim
    bullets = collect_resume_bullet_texts(resume)
    if summary and bullets:
        for sent in re.split(r"(?<=[.!?])\s+", summary):
            sent = sent.strip()
            if len(sent.split()) < 6:
                continue
            for bullet in bullets:
                if text_overlap_ratio(sent, bullet) >= 0.80:
                    failures.append("summary_duplicates_bullet")
                    break
            if "summary_duplicates_bullet" in failures:
                break

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

    # Minimum content guarantee: every source role/project title must appear
    facts = resume_facts if isinstance(resume_facts, dict) else {}
    if facts:
        source_titles = {
            str(r.get("title") or "").strip().lower()
            for r in (facts.get("experience_roles") or facts.get("experience") or [])
            if isinstance(r, dict) and str(r.get("title") or "").strip()
            and [str(b).strip() for b in (r.get("bullets") or []) if str(b).strip()]
        }
        tailored_titles = {
            str(e.get("title") or "").strip().lower()
            for e in (resume.get("experience") or [])
            if isinstance(e, dict) and str(e.get("title") or "").strip()
        }
        for title in source_titles:
            if title and not any(
                title == t or title in t or t in title for t in tailored_titles if t
            ):
                failures.append(f"missing_source_experience:{title[:48]}")
        source_projects = {
            str(p.get("name") or "").strip().lower()
            for p in normalize_project_list(facts.get("projects") or [])
            if isinstance(p, dict) and str(p.get("name") or "").strip()
        }
        tailored_projects = {
            str(p.get("name") or "").strip().lower()
            for p in (resume.get("projects") or [])
            if isinstance(p, dict) and str(p.get("name") or "").strip()
        }
        for name in source_projects:
            if name and not any(
                name == n or name in n or n in name for n in tailored_projects if n
            ):
                failures.append(f"missing_source_project:{name[:48]}")

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

    # Structural malformations (duplicate entries, cross-contam, markers)
    try:
        from intelligent_tailoring.structural_integrity import structural_failures

        for item in structural_failures(resume):
            if item.startswith(
                (
                    "duplicate_experience",
                    "duplicate_project",
                    "misplaced_entry_heading",
                    "embedded_bullet_marker",
                )
            ):
                failures.append(item)
    except Exception:
        pass

    return list(dict.fromkeys(failures))


def estimate_content_density(resume: dict[str, Any]) -> dict[str, Any]:
    """Heuristic page utilization for early-career one-page resumes.

    Half-page previews (one thin role + one thin project) must count as
    underfilled so the pipeline restores more source substance.
    """
    inv = content_inventory(resume)
    # Target a fuller one-page look — not a sparse half sheet.
    expected_bullets = 10
    expected_summary = 55
    expected_skills = 14
    total_bullets = inv["experience_bullets"] + inv["project_bullets"]
    bullet_fill = min(1.0, total_bullets / expected_bullets)
    summary_fill = min(1.0, inv["summary_words"] / expected_summary)
    skill_fill = min(1.0, inv["skill_atoms"] / expected_skills)
    utilization = round(0.45 * bullet_fill + 0.25 * summary_fill + 0.30 * skill_fill, 3)
    entry_count = inv["experience_entries"] + int(inv.get("projects") or 0)
    underfilled = (
        utilization < 0.78
        or total_bullets < 7
        or (entry_count < 3 and total_bullets < 9)
    )
    return {
        "utilization_score": utilization,
        "underfilled": underfilled,
        "inventory": inv,
    }


def expand_thin_entries_from_source(
    tailored: dict[str, Any],
    *,
    resume_facts: dict[str, Any],
    target_bullets_per_role: int = 4,
    target_bullets_per_project: int = 3,
) -> dict[str, Any]:
    """Pad existing roles/projects with additional verified source bullets.

    Used when the page is underfilled: keep tailored wording first, then append
    unused source bullets until the target density is reached.
    """
    out = deepcopy(tailored) if isinstance(tailored, dict) else {}
    source_roles = [
        r
        for r in (resume_facts.get("experience_roles") or resume_facts.get("experience") or [])
        if isinstance(r, dict)
    ]
    source_projects = normalize_project_list(resume_facts.get("projects") or [])

    from intelligent_tailoring.services.one_page_compressor import _dedupe_similar

    expanded_exp: list[dict[str, Any]] = []
    for entry in out.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        match = _match_role(entry, source_roles)
        if match and len(bullets) < target_bullets_per_role:
            src_b = [
                str(b).strip() for b in (match.get("bullets") or []) if str(b).strip()
            ]
            if src_b:
                bullets = _dedupe_similar(bullets + src_b)[:target_bullets_per_role]
                if not str(entry.get("dates") or "").strip():
                    entry["dates"] = str(match.get("dates") or "")
                if not str(entry.get("company") or "").strip():
                    entry["company"] = str(match.get("company") or "")
        expanded_exp.append({**entry, "bullets": bullets})
    if expanded_exp:
        out["experience"] = expanded_exp

    expanded_proj: list[dict[str, Any]] = []
    for entry in out.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        desc = str(entry.get("description") or "").strip()
        match = _match_project(entry, source_projects)
        if match and len(bullets) < target_bullets_per_project:
            src_b = [
                str(b).strip() for b in (match.get("bullets") or []) if str(b).strip()
            ]
            if src_b:
                bullets = _dedupe_similar(bullets + src_b)[:target_bullets_per_project]
            if not desc:
                desc = str(match.get("description") or "").strip()
        expanded_proj.append({**entry, "bullets": bullets, "description": desc})
    if expanded_proj:
        out["projects"] = expanded_proj

    return drop_empty_shell_entries(out)


def restore_missing_content_from_source(
    tailored: dict[str, Any],
    *,
    resume_facts: dict[str, Any],
    max_roles: int = 0,
    max_projects: int = 0,
    min_bullets_per_role: int = 1,
    min_bullets_per_project: int = 2,
) -> dict[str, Any]:
    """Preservation-first repair: refill empty shells from verified source facts.

    ``max_roles`` / ``max_projects`` of 0 means keep *all* source entries
    (minimum-content guarantee). Positive caps remain for callers that need them.
    """
    out = drop_empty_shell_entries(deepcopy(tailored))
    source_roles = [
        r for r in (resume_facts.get("experience_roles") or []) if isinstance(r, dict)
    ]
    source_projects = normalize_project_list(resume_facts.get("projects") or [])
    role_cap = len(source_roles) if max_roles <= 0 else max_roles
    project_cap = len(source_projects) if max_projects <= 0 else max_projects

    # If tailored experience empty/thin, restore source roles with bullets
    tailored_exp = [e for e in (out.get("experience") or []) if isinstance(e, dict)]
    if not tailored_exp or sum(
        len([b for b in (e.get("bullets") or []) if str(b).strip()]) for e in tailored_exp
    ) < 1:
        restored = []
        for role in source_roles[:role_cap]:
            bullets = [str(b).strip() for b in (role.get("bullets") or []) if str(b).strip()]
            if not bullets:
                continue
            restored.append(
                {
                    "company": str(role.get("company") or ""),
                    "title": str(role.get("title") or ""),
                    "dates": str(role.get("dates") or ""),
                    "bullets": bullets[: max(3, min_bullets_per_role)],
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
                    fixed.append(
                        {
                            **entry,
                            "bullets": src_bullets[
                                : max(3, min_bullets_per_role, len(bullets))
                            ],
                        }
                    )
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
        for proj in source_projects[:project_cap]:
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

    # Always enforce 100% source role/project title coverage + education normalize
    out = ensure_minimum_content_from_source(
        out,
        resume_facts=resume_facts,
        min_bullets_per_role=min_bullets_per_role,
        min_bullets_per_project=max(1, min(min_bullets_per_project, 2)),
    )
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
    """Match by title/company identity only — never by array position.

    Avoids restoring Tutor bullets onto Capstone (and vice versa) when a
    soft/fallback match would cross entry boundaries.
    """
    title = str(entry.get("title") or "").strip().lower()
    company = str(entry.get("company") or "").strip().lower()
    # Prefer exact title matches first
    exact = [
        role
        for role in source_roles
        if title and title == str(role.get("title") or "").strip().lower()
    ]
    if exact:
        if company:
            for role in exact:
                rc = str(role.get("company") or "").strip().lower()
                if rc == company or company in rc or rc in company:
                    return role
        return exact[0]
    for role in source_roles:
        rt = str(role.get("title") or "").strip().lower()
        rc = str(role.get("company") or "").strip().lower()
        if title and rt and (title in rt or rt in title):
            if not company or not rc or company == rc or company in rc or rc in company:
                return role
        if company and company == rc and title and rt and (title in rt or rt in title):
            return role
    # Only fall back when a single source role exists (unambiguous).
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
