"""End-to-end structural integrity for tailored resumes.

Catches the opposite failure mode of content-dropping: duplicated entries,
cross-contaminated bullets (another role's title/company/dates line), embedded
bullet markers, and description↔bullet near-duplicates.

Auto-repairs when possible so malformed content is never silently exported.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from intelligent_tailoring.services.one_page_compressor import (
    _dedupe_similar,
    texts_are_near_duplicates,
)

# Leading bullet markers that content agents sometimes bake into text.
# Require whitespace (or another marker) after "-" / "*" so "-5% latency"
# is preserved while "- Developed..." and "• • Developed..." are stripped.
_BULLET_MARKER_RE = re.compile(
    r"^(?:(?:[\s]*[•●▪◦\u2022]+[\s]*)+|(?:[\s]*[-*][\s]+)+)"
)

# Title | Company | Jul 2022 – Jul 2023  (or similar meta lines)
_PIPE_META_RE = re.compile(r"\|")
_DATE_RANGE_STRICT_RE = re.compile(
    r"(?:"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}"
    r"|\d{4}"
    r")\s*[–\-—]\s*"
    r"(?:"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}"
    r"|\d{4}|present|current"
    r")",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def strip_bullet_markers(text: str) -> str:
    """Remove leading bullet/list markers until plain text remains."""
    out = str(text or "").strip()
    # Repeat so "• • Developed..." and "- - foo" both collapse.
    for _ in range(4):
        cleaned = _BULLET_MARKER_RE.sub("", out).strip()
        if cleaned == out:
            break
        out = cleaned
    return out


def experience_identity_key(entry: dict[str, Any]) -> str:
    """Stable key for experience entries: title + company (dates optional soft)."""
    title = _norm(str(entry.get("title") or ""))
    company = _norm(str(entry.get("company") or ""))
    # Strip common separators so "Lead – Tribe" and "Lead - Tribe" match.
    title = re.sub(r"\s*[–\-—|:]\s*", " ", title).strip()
    company = re.sub(r"\s*[–\-—|:]\s*", " ", company).strip()
    if title or company:
        return f"{title}|{company}"
    return ""


def project_identity_key(entry: dict[str, Any]) -> str:
    name = _norm(str(entry.get("name") or ""))
    name = re.sub(r"\s*[–\-—|:]\s*", " ", name).strip()
    return name


def _titles_soft_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Containment for "Capstone Project Lead" vs "Capstone Project Lead – Tribe"
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 8 and shorter in longer:
        return True
    return False


def _find_identity_group_key(
    entry: dict[str, Any],
    existing_keys: list[str],
    key_to_entry: dict[str, dict[str, Any]],
    *,
    section: str,
) -> str | None:
    """Return an existing group key when this entry soft-matches one."""
    if section == "experience":
        title = str(entry.get("title") or "")
        company = _norm(str(entry.get("company") or ""))
        for key in existing_keys:
            other = key_to_entry[key]
            ot = str(other.get("title") or "")
            oc = _norm(str(other.get("company") or ""))
            if not _titles_soft_match(title, ot):
                continue
            # Same/empty company, or one company contained in the other
            if (
                not company
                or not oc
                or company == oc
                or company in oc
                or oc in company
            ):
                return key
        return None
    name = str(entry.get("name") or "")
    for key in existing_keys:
        other = key_to_entry[key]
        if _titles_soft_match(name, str(other.get("name") or "")):
            return key
    return None


def looks_like_entry_heading(
    text: str,
    *,
    known_titles: set[str] | None = None,
    known_companies: set[str] | None = None,
) -> bool:
    """True when a bullet looks like another entry's title/company/date line."""
    raw = strip_bullet_markers(text)
    low = _norm(raw)
    if not low or len(low) < 8:
        return False

    # Explicit pipe-separated meta: "Python Programming Tutor | Tel Hai | Jul 2022 – Jul 2023"
    if _PIPE_META_RE.search(raw) and _DATE_RANGE_STRICT_RE.search(raw):
        return True
    if _PIPE_META_RE.search(raw):
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        if len(parts) >= 2 and all(len(p) < 60 for p in parts):
            # Short pipe segments without verbs → heading, not achievement
            if not re.search(
                r"\b(built|designed|developed|implemented|led|created|tutored|"
                r"configured|resolved|automated|improved|reduced|increased)\b",
                low,
            ):
                return True

    # Matches a known title/company exactly or as a prefix line
    for title in known_titles or set():
        t = _norm(title)
        if t and len(t) >= 8 and (low == t or low.startswith(t + " ") or low.startswith(t + "|")):
            return True
    for company in known_companies or set():
        c = _norm(company)
        if c and len(c) >= 4 and low == c:
            return True

    # Bare "Title – Company (dates)" style without action verbs
    if _DATE_RANGE_STRICT_RE.search(raw) and not re.search(
        r"\b(built|designed|developed|implemented|led|created|tutored|"
        r"configured|resolved|automated|improved|reduced|increased|"
        r"using|with|for|via)\b",
        low,
    ):
        return True

    return False


def _union_bullets(*groups: list[str]) -> list[str]:
    combined: list[str] = []
    for group in groups:
        for b in group:
            cleaned = strip_bullet_markers(str(b))
            if cleaned:
                combined.append(cleaned)
    return _dedupe_similar(combined)


def consolidate_experience_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate experience entries (same title+company) into one each."""
    order: list[str] = []
    by_key: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hard = experience_identity_key(entry)
        soft = _find_identity_group_key(entry, order, by_key, section="experience")
        key = soft or hard or f"anon_{len(order)}"
        bullets = [
            strip_bullet_markers(str(b))
            for b in (entry.get("bullets") or [])
            if str(b).strip()
        ]
        if key not in by_key:
            by_key[key] = {
                **entry,
                "title": str(entry.get("title") or ""),
                "company": str(entry.get("company") or ""),
                "dates": str(entry.get("dates") or ""),
                "bullets": bullets,
            }
            order.append(key)
            continue
        existing = by_key[key]
        # Prefer longer/more specific title & company/dates
        if len(str(entry.get("title") or "")) > len(str(existing.get("title") or "")):
            existing["title"] = str(entry.get("title") or "")
        if len(str(entry.get("company") or "")) > len(str(existing.get("company") or "")):
            existing["company"] = str(entry.get("company") or "")
        if not existing.get("dates") and entry.get("dates"):
            existing["dates"] = str(entry.get("dates") or "")
        if entry.get("source_entry_id") and not existing.get("source_entry_id"):
            existing["source_entry_id"] = entry.get("source_entry_id")
        existing["bullets"] = _union_bullets(
            list(existing.get("bullets") or []),
            bullets,
        )
    return [by_key[k] for k in order]


def consolidate_project_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate project entries (same title) into one each."""
    order: list[str] = []
    by_key: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hard = project_identity_key(entry)
        soft = _find_identity_group_key(entry, order, by_key, section="projects")
        key = soft or hard or f"proj_{len(order)}"
        bullets = [
            strip_bullet_markers(str(b))
            for b in (entry.get("bullets") or [])
            if str(b).strip()
        ]
        desc = strip_bullet_markers(str(entry.get("description") or ""))
        techs = [
            str(t).strip()
            for t in (entry.get("technologies") or [])
            if str(t).strip()
        ]
        if key not in by_key:
            by_key[key] = {
                **entry,
                "name": str(entry.get("name") or ""),
                "description": desc,
                "bullets": bullets,
                "technologies": techs,
            }
            order.append(key)
            continue
        existing = by_key[key]
        if len(str(entry.get("name") or "")) > len(str(existing.get("name") or "")):
            existing["name"] = str(entry.get("name") or "")
        if len(desc) > len(str(existing.get("description") or "")):
            existing["description"] = desc
        if entry.get("source_entry_id") and not existing.get("source_entry_id"):
            existing["source_entry_id"] = entry.get("source_entry_id")
        existing["bullets"] = _union_bullets(
            list(existing.get("bullets") or []),
            bullets,
        )
        merged_tech = list(existing.get("technologies") or [])
        for t in techs:
            if t not in merged_tech:
                merged_tech.append(t)
        existing["technologies"] = merged_tech
    return [by_key[k] for k in order]


def sanitize_entry_content(
    entry: dict[str, Any],
    *,
    section: str,
    known_titles: set[str],
    known_companies: set[str],
) -> dict[str, Any]:
    """Strip markers, drop misplaced headings, and collapse title/desc dupes."""
    out = dict(entry)
    title = str(
        out.get("title") if section == "experience" else out.get("name") or ""
    ).strip()
    company = str(out.get("company") or "").strip()
    dates = str(out.get("dates") or "").strip()
    desc = strip_bullet_markers(str(out.get("description") or ""))

    # Other entries' identities (exclude self) for cross-contamination checks
    other_titles = {t for t in known_titles if _norm(t) != _norm(title)}
    other_companies = {c for c in known_companies if _norm(c) != _norm(company)}

    cleaned_bullets: list[str] = []
    for raw in out.get("bullets") or []:
        bullet = strip_bullet_markers(str(raw))
        if not bullet:
            continue
        # Drop bullets that are just this entry's own title/company/dates
        if _norm(bullet) == _norm(title) and title:
            continue
        if company and _norm(bullet) == _norm(company):
            continue
        if dates and _norm(bullet) == _norm(dates):
            continue
        meta_line = " | ".join(p for p in (title, company, dates) if p)
        if meta_line and texts_are_near_duplicates(bullet, meta_line):
            continue
        # Drop bullets that look like another entry's heading/meta
        if looks_like_entry_heading(
            bullet,
            known_titles=other_titles,
            known_companies=other_companies,
        ):
            continue
        # Drop bullets that duplicate the description line
        if desc and texts_are_near_duplicates(bullet, desc):
            continue
        cleaned_bullets.append(bullet)

    cleaned_bullets = _dedupe_similar(cleaned_bullets)

    # If description duplicates a remaining bullet or the title, clear it
    if desc:
        if title and texts_are_near_duplicates(desc, title):
            desc = ""
        elif any(texts_are_near_duplicates(desc, b) for b in cleaned_bullets):
            desc = ""

    out["bullets"] = cleaned_bullets
    if section == "projects":
        out["description"] = desc
        out["name"] = title or str(out.get("name") or "")
    return out


def structural_failures(resume: dict[str, Any]) -> list[str]:
    """Report structural malformations (duplicates, markers, cross-contam)."""
    failures: list[str] = []
    experience = [e for e in (resume.get("experience") or []) if isinstance(e, dict)]
    projects = [p for p in (resume.get("projects") or []) if isinstance(p, dict)]

    exp_keys = [experience_identity_key(e) for e in experience]
    seen_exp: set[str] = set()
    for key in exp_keys:
        if key and key in seen_exp:
            failures.append(f"duplicate_experience_entry:{key}")
        if key:
            seen_exp.add(key)
    # Soft duplicate titles
    titles = [_norm(str(e.get("title") or "")) for e in experience]
    for i, t in enumerate(titles):
        if not t:
            continue
        for j, other in enumerate(titles):
            if j <= i or not other:
                continue
            if _titles_soft_match(t, other):
                failures.append(f"duplicate_experience_title:{t}")

    proj_names = [_norm(str(p.get("name") or "")) for p in projects]
    seen_proj: set[str] = set()
    for name in proj_names:
        if name and name in seen_proj:
            failures.append(f"duplicate_project_entry:{name}")
        if name:
            seen_proj.add(name)

    known_titles = {
        str(e.get("title") or "").strip() for e in experience if str(e.get("title") or "").strip()
    } | {
        str(p.get("name") or "").strip() for p in projects if str(p.get("name") or "").strip()
    }
    known_companies = {
        str(e.get("company") or "").strip()
        for e in experience
        if str(e.get("company") or "").strip()
    }

    def _scan_entry(entry: dict[str, Any], *, section: str) -> None:
        title = str(
            entry.get("title") if section == "experience" else entry.get("name") or ""
        )
        desc = str(entry.get("description") or "")
        bullets = list(entry.get("bullets") or [])
        if title and not bullets and section == "experience":
            failures.append(f"empty_bullets_under_entry:{title}")
        if title and not bullets and not desc and section == "projects":
            failures.append(f"empty_content_under_project:{title}")
        for b in bullets:
            text = str(b)
            if _BULLET_MARKER_RE.match(text or ""):
                failures.append("embedded_bullet_marker")
            if looks_like_entry_heading(
                text,
                known_titles={t for t in known_titles if _norm(t) != _norm(title)},
                known_companies=known_companies,
            ):
                failures.append(f"misplaced_entry_heading_bullet:{text[:48]}")
            if title and texts_are_near_duplicates(text, title):
                failures.append(f"bullet_duplicates_title:{title}")
            if desc and texts_are_near_duplicates(text, desc):
                failures.append(f"bullet_duplicates_description:{title}")

    for entry in experience:
        _scan_entry(entry, section="experience")
    for entry in projects:
        _scan_entry(entry, section="projects")

    return list(dict.fromkeys(failures))


def validate_and_repair_resume_structure(resume: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate entries, strip markers, and drop cross-contaminated bullets.

    Always auto-corrects; returns a cleaned resume safe for render/export.
    """
    out = deepcopy(resume) if isinstance(resume, dict) else {}
    experience = consolidate_experience_entries(
        [e for e in (out.get("experience") or []) if isinstance(e, dict)]
    )
    projects = consolidate_project_entries(
        [p for p in (out.get("projects") or []) if isinstance(p, dict)]
    )

    known_titles = {
        str(e.get("title") or "").strip() for e in experience if str(e.get("title") or "").strip()
    } | {
        str(p.get("name") or "").strip() for p in projects if str(p.get("name") or "").strip()
    }
    known_companies = {
        str(e.get("company") or "").strip()
        for e in experience
        if str(e.get("company") or "").strip()
    }

    out["experience"] = [
        sanitize_entry_content(
            e,
            section="experience",
            known_titles=known_titles,
            known_companies=known_companies,
        )
        for e in experience
    ]
    # Drop experience entries that ended up with zero bullets after sanitization
    out["experience"] = [
        e for e in out["experience"] if [b for b in (e.get("bullets") or []) if str(b).strip()]
    ]

    out["projects"] = [
        sanitize_entry_content(
            p,
            section="projects",
            known_titles=known_titles,
            known_companies=known_companies,
        )
        for p in projects
    ]
    out["projects"] = [
        p
        for p in out["projects"]
        if [b for b in (p.get("bullets") or []) if str(b).strip()]
        or str(p.get("description") or "").strip()
    ]

    # Attach placeholder ids only when missing. Never invent project_{idx} /
    # role_{idx} — those collide with real source ids after reordering and
    # cause cross-entry bullet restores.
    for idx, entry in enumerate(out["experience"]):
        if not entry.get("source_entry_id") and not entry.get("id"):
            entry["source_entry_id"] = f"unmapped_role_{idx}"
            entry["id"] = entry["source_entry_id"]
    for idx, entry in enumerate(out["projects"]):
        if not entry.get("source_entry_id") and not entry.get("id"):
            entry["source_entry_id"] = f"unmapped_project_{idx}"
            entry["id"] = entry["source_entry_id"]

    out["_structural_integrity"] = {
        "failures_before": structural_failures(resume if isinstance(resume, dict) else {}),
        "failures_after": structural_failures(out),
        "repaired": True,
    }
    return out
