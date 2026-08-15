"""One-page resume compressor — intelligent density, not blind deletion.

Default policy: every tailored resume must fit on a single A4 page.
Compress by prioritizing relevance, removing repetition, and capping bullets
while preserving professional readability (no micro-fonts).

Requirement-coverage rule: bullets that directly match stated job requirements
are high-priority and must not be silently dropped when trimming.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

# Content budgets tuned for A4 + modern_ats theme (~10pt, ~14mm margins).
DEFAULT_SUMMARY_MAX_WORDS = 58
DEFAULT_MAX_EXPERIENCE_ROLES = 3
DEFAULT_MAX_PROJECTS = 2
DEFAULT_BULLETS_TOP_ROLE = 3
DEFAULT_BULLETS_OTHER_ROLE = 2
DEFAULT_BULLETS_PROJECT = 3
DEFAULT_MAX_SKILL_LINES = 5
DEFAULT_MAX_TOTAL_BULLETS = 14


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _trim_words(text: str, maximum: int) -> str:
    words = (text or "").split()
    if len(words) <= maximum:
        return (text or "").strip()
    trimmed = " ".join(words[:maximum]).rstrip(",;:")
    if trimmed and not trimmed.endswith("."):
        trimmed += "."
    return trimmed


def _coverage_context(strategy: dict[str, Any]) -> tuple[set[str], list[str]]:
    """Resolve requirement terms/phrases from strategy (or derive lightly)."""
    from intelligent_tailoring.requirement_coverage import (
        collect_requirement_phrases,
        requirement_term_set,
    )

    phrases = [
        str(p).strip()
        for p in (strategy.get("requirement_phrases") or [])
        if str(p).strip()
    ]
    if not phrases:
        phrases = collect_requirement_phrases(strategy=strategy)
    terms: set[str] = set()
    for t in strategy.get("requirement_terms") or []:
        if str(t).strip():
            terms.add(str(t).strip().lower())
    # Always include emphasize / propagate / must-keep skills as terms
    for key in (
        "propagate_terms",
        "skills_to_emphasize",
        "must_keep_skills",
        "shared_technologies",
        "must_keep_bullets",
    ):
        for item in strategy.get(key) or []:
            text = str(item).strip().lower()
            if text:
                terms.add(text)
                for tok in re.findall(r"[a-z0-9+#./-]{3,}", text):
                    terms.add(tok)
    if not terms:
        terms = requirement_term_set(phrases)
    else:
        terms |= requirement_term_set(phrases)
    return terms, phrases


def _bullet_score(
    bullet: str,
    emphasize: list[str],
    *,
    strongest: list[str] | None = None,
    weaker: list[str] | None = None,
    requirement_terms: set[str] | None = None,
    requirement_phrases: list[str] | None = None,
    must_keep: list[str] | None = None,
) -> int:
    """Rank by interview probability for THIS role (quality over completeness)."""
    low = _norm(bullet)
    try:
        from intelligent_tailoring.interview_philosophy import bullet_interview_score

        score = bullet_interview_score(bullet, emphasize)
    except Exception:
        score = min(len(low.split()), 28)
        for term in emphasize:
            t = _norm(term)
            if t and t in low:
                score += 25
        if re.search(
            r"\b(designed|built|implemented|developed|configured|resolved|automated)\b",
            low,
        ):
            score += 8
        if len(low.split()) < 6:
            score -= 15
        if re.match(r"^(worked on|helped with|responsible for|created database)\b", low):
            score -= 12
    for bit in strongest or []:
        frag = _norm(str(bit))[:48]
        if frag and (frag in low or any(t in low for t in frag.split() if len(t) > 4)):
            score += 22
            break
    for bit in weaker or []:
        frag = _norm(str(bit))[:48]
        if frag and frag in low:
            score -= 18
            break
    # Hard boost for requirement-matching / must-keep bullets
    if must_keep:
        for mk in must_keep:
            frag = _norm(str(mk))[:64]
            if frag and (frag in low or low in frag):
                score += 60
                break
    if requirement_terms:
        try:
            from intelligent_tailoring.requirement_coverage import (
                bullet_matches_requirements,
            )

            info = bullet_matches_requirements(
                bullet,
                requirement_terms,
                phrases=requirement_phrases,
            )
            if info.get("direct"):
                score += 55
            else:
                score += min(int(info.get("score") or 0), 40)
        except Exception:
            pass
    return score


def _significant_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+]{3,}", _norm(text)))


def texts_are_near_duplicates(a: str, b: str, *, overlap_threshold: float = 0.55) -> bool:
    """True when two resume lines express the same claim (exact, nest, or overlap)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # One line is essentially a stub/prefix of the other.
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    # Short titles (e.g. project names) often leak into description — treat
    # exact containment as a duplicate even below the 24-char stub threshold
    # when the shorter string is a complete multi-word label.
    if shorter in longer:
        if len(shorter) >= 24:
            return True
        if len(shorter.split()) >= 2 and (
            longer == shorter
            or longer.startswith(shorter + " ")
            or longer.startswith(shorter + ":")
            or longer.startswith(shorter + " -")
            or longer.startswith(shorter + " —")
            or longer.startswith(shorter + " –")
        ):
            return True
    if " ".join(na.split()[:4]) == " ".join(nb.split()[:4]) and len(na.split()) >= 4:
        return True
    tokens_a, tokens_b = _significant_tokens(a), _significant_tokens(b)
    if not tokens_a or not tokens_b:
        return False
    overlap = len(tokens_a & tokens_b) / max(len(tokens_a | tokens_b), 1)
    return overlap >= overlap_threshold


def _dedupe_similar(bullets: list[str]) -> list[str]:
    """Drop near-duplicate bullets (shared opening + high token overlap)."""
    kept: list[str] = []
    for bullet in bullets:
        if any(texts_are_near_duplicates(bullet, prior) for prior in kept):
            continue
        kept.append(bullet)
    return kept


def scrub_duplicate_entry_content(entry: dict[str, Any]) -> dict[str, Any]:
    """Collapse description↔bullet and near-duplicate bullets inside one entry."""
    from intelligent_tailoring.structural_integrity import strip_bullet_markers

    out = dict(entry)
    title = str(out.get("title") or out.get("name") or "").strip()
    raw_bullets = [
        strip_bullet_markers(str(b))
        for b in (out.get("bullets") or [])
        if str(b).strip()
    ]
    # Drop bullets that merely restate the entry title/name
    if title:
        raw_bullets = [
            b for b in raw_bullets if not texts_are_near_duplicates(b, title)
        ]
    kept = _dedupe_similar(raw_bullets)
    desc = strip_bullet_markers(str(out.get("description") or ""))
    if desc and title and texts_are_near_duplicates(desc, title):
        desc = ""
    if desc and kept and any(texts_are_near_duplicates(desc, b) for b in kept):
        desc = ""
    out["bullets"] = kept
    out["description"] = desc
    return out


def scrub_resume_duplicate_content(resume: dict[str, Any]) -> dict[str, Any]:
    """Apply near-dedupe within and across experience/project entries before export.

    Cross-entry cloning (same achievement pasted under two projects/roles) is a
    common Agent-2/expand failure mode — drop later near-duplicates globally.
    Also demote bullet-like project ``name`` fields that would render as fake headings,
    and merge whole projects that share the same normalized name.
    """
    out = deepcopy(resume)
    out["experience"] = [
        scrub_duplicate_entry_content(e)
        for e in (out.get("experience") or [])
        if isinstance(e, dict)
    ]

    # Demote sentence/bullet text wrongly stored as project names (Bylith screenshot).
    try:
        from intelligent_tailoring.claim_validator import looks_like_bullet_project_name
    except Exception:  # pragma: no cover
        looks_like_bullet_project_name = lambda _n: False  # type: ignore

    cleaned_projects: list[dict[str, Any]] = []
    for p in out.get("projects") or []:
        if not isinstance(p, dict):
            continue
        entry = scrub_duplicate_entry_content(p)
        name = str(entry.get("name") or "").strip()
        if name and looks_like_bullet_project_name(name):
            bullets = [
                str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()
            ]
            if not any(texts_are_near_duplicates(name, b) for b in bullets):
                bullets = [name] + bullets
            entry["bullets"] = _dedupe_similar(bullets)
            entry["name"] = ""
            desc = str(entry.get("description") or "").strip()
            if desc and texts_are_near_duplicates(desc, name):
                entry["description"] = ""
        cleaned_projects.append(entry)

    # Merge whole projects with the same normalized name (Server Monitor ×2).
    merged_by_name: dict[str, dict[str, Any]] = {}
    name_order: list[str] = []
    unnamed: list[dict[str, Any]] = []
    for entry in cleaned_projects:
        name = str(entry.get("name") or "").strip()
        key = _norm(name)
        if not key:
            unnamed.append(entry)
            continue
        if key not in merged_by_name:
            merged_by_name[key] = dict(entry)
            name_order.append(key)
            continue
        existing = merged_by_name[key]
        bullets = _dedupe_similar(
            [str(b).strip() for b in (existing.get("bullets") or []) if str(b).strip()]
            + [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        )
        existing["bullets"] = bullets
        desc = str(existing.get("description") or "").strip()
        other_desc = str(entry.get("description") or "").strip()
        if not desc and other_desc:
            existing["description"] = other_desc
        elif desc and other_desc and texts_are_near_duplicates(desc, other_desc):
            pass
        elif other_desc and not texts_are_near_duplicates(other_desc, desc):
            # Prefer longer unique description; avoid stacking duplicates.
            if len(other_desc) > len(desc):
                existing["description"] = other_desc
    out["projects"] = [merged_by_name[k] for k in name_order] + unnamed
    # Drop shells that have neither a real name nor content
    out["projects"] = [
        p
        for p in out["projects"]
        if str(p.get("name") or "").strip()
        or str(p.get("description") or "").strip()
        or any(str(b).strip() for b in (p.get("bullets") or []))
    ]

    # Global cross-entry near-dedupe: keep first occurrence, drop later clones.
    seen: list[str] = []
    for section in ("experience", "projects"):
        entries = out.get(section) or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kept: list[str] = []
            for bullet in entry.get("bullets") or []:
                text = str(bullet or "").strip()
                if not text:
                    continue
                if any(texts_are_near_duplicates(text, prior) for prior in seen):
                    continue
                kept.append(text)
                seen.append(text)
            entry["bullets"] = kept
            desc = str(entry.get("description") or "").strip()
            if desc and any(texts_are_near_duplicates(desc, prior) for prior in seen):
                entry["description"] = ""
            elif desc:
                # Description that merely restates the project name is dropped.
                name = str(entry.get("name") or "").strip()
                if name and texts_are_near_duplicates(desc, name):
                    entry["description"] = ""
                else:
                    seen.append(desc)
    return out


def _rank_entries(
    entries: list[dict[str, Any]],
    emphasize: list[str],
    *,
    name_keys: tuple[str, ...],
    requirement_terms: set[str] | None = None,
    requirement_phrases: list[str] | None = None,
    must_keep: list[str] | None = None,
) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        bullets = [str(b) for b in (entry.get("bullets") or []) if str(b).strip()]
        blob = " ".join(
            [str(entry.get(k) or "") for k in name_keys]
            + [str(entry.get("description") or "")]
            + bullets
        )
        score = _bullet_score(
            blob,
            emphasize,
            requirement_terms=requirement_terms,
            requirement_phrases=requirement_phrases,
            must_keep=must_keep,
        )
        # Strong boost when the entry contains a must-keep / direct-match bullet
        if must_keep:
            for mk in must_keep:
                mk_low = _norm(mk)[:64]
                if mk_low and any(mk_low in _norm(b) or _norm(b) in mk_low for b in bullets):
                    score += 80
                    break
        scored.append((score, -idx, entry))  # stable: prefer original order on ties
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [e for _, __, e in scored]


def estimate_page_pressure(resume: dict[str, Any]) -> dict[str, Any]:
    """Heuristic pressure score; higher means more likely to overflow one page.

    Extra experience/project *entries* are cheap when bullets are already short —
    the pipeline keeps every real role/project and fits the page by trimming
    bullets, not by deleting whole entries.
    """
    summary = str(resume.get("professional_summary") or resume.get("summary") or "")
    exp = [e for e in (resume.get("experience") or []) if isinstance(e, dict)]
    projects = [p for p in (resume.get("projects") or []) if isinstance(p, dict)]
    skills = list(resume.get("skills") or [])
    bullets = 0
    thin_roles = 0
    for e in exp:
        n = len([b for b in (e.get("bullets") or []) if str(b).strip()])
        bullets += n
        if n <= 1:
            thin_roles += 1
    thin_projects = 0
    for p in projects:
        n = len([b for b in (p.get("bullets") or []) if str(b).strip()])
        bullets += n
        if str(p.get("description") or "").strip():
            bullets += 1
        if n <= 1 and not str(p.get("description") or "").strip():
            thin_projects += 1
        elif n <= 1:
            thin_projects += 1
    words = len(summary.split()) + sum(
        len(str(b).split())
        for e in exp + projects
        for b in (e.get("bullets") or [])
    )
    # Empirically: ~14 bullets + ~60-word summary fits one page even with
    # additional short (1-bullet) roles/projects kept for completeness.
    pressure = 0
    pressure += max(0, bullets - DEFAULT_MAX_TOTAL_BULLETS) * 8
    fat_roles = max(0, len(exp) - thin_roles)
    fat_projects = max(0, len(projects) - thin_projects)
    # Only multi-bullet entries beyond the soft budget cost significant pressure.
    # Thin extras are heading+1-line and nearly free on a modern ATS layout.
    pressure += max(0, fat_roles - DEFAULT_MAX_EXPERIENCE_ROLES) * 8
    pressure += max(0, fat_projects - DEFAULT_MAX_PROJECTS) * 6
    pressure += max(0, (thin_roles + thin_projects) - 6) * 1
    pressure += max(0, len(summary.split()) - DEFAULT_SUMMARY_MAX_WORDS) * 1
    pressure += max(0, len(skills) - max(DEFAULT_MAX_SKILL_LINES, 6)) * 3
    pressure += max(0, words - 380) // 20
    return {
        "pressure": pressure,
        "bullet_count": bullets,
        "experience_count": len(exp),
        "project_count": len(projects),
        "summary_words": len(summary.split()),
        "skill_lines": len(skills),
        "likely_fits_one_page": pressure <= 8,
    }


def compress_resume_to_one_page(
    resume: dict[str, Any],
    *,
    strategy: dict[str, Any] | None = None,
    max_pages: int = 1,
    aggressive: bool = False,
) -> dict[str, Any]:
    """Return a compressed copy optimized for a single A4 page.

    Never invents facts. Prefers stronger bullets over more bullets.
    """
    if max_pages != 1:
        # Multi-page explicitly requested — light polish only
        out = deepcopy(resume)
        out["summary"] = str(out.get("professional_summary") or out.get("summary") or "")
        out["professional_summary"] = out["summary"]
        return out

    strategy = strategy or {}
    emphasize = [
        str(s)
        for s in (
            strategy.get("propagate_terms")
            or strategy.get("skills_to_emphasize")
            or strategy.get("must_highlight_in_summary")
            or strategy.get("top_interview_reasons")
            or strategy.get("shared_technologies")
            or []
        )
        if str(s).strip()
    ]
    strongest = [
        str(s)
        for s in (
            strategy.get("strongest_evidence")
            or strategy.get("facts_to_expand")
            or strategy.get("must_keep_bullets")
            or []
        )
        if str(s).strip()
    ][:16]
    weaker = [
        str(s)
        for s in (
            strategy.get("weaker_evidence_to_reduce")
            or strategy.get("facts_to_condense")
            or strategy.get("facts_to_omit")
            or []
        )
        if str(s).strip()
    ][:16]
    must_keep = [
        str(s).strip()
        for s in (strategy.get("must_keep_bullets") or [])
        if str(s).strip()
    ]
    req_terms, req_phrases = _coverage_context(strategy)
    shared_tech = [
        str(s).strip()
        for s in (
            strategy.get("shared_technologies")
            or strategy.get("must_keep_skills")
            or []
        )
        if str(s).strip()
    ]

    from intelligent_tailoring.requirement_coverage import select_bullets_with_coverage

    # Soft budgets: used to decide how many bullets each entry keeps.
    # Entries themselves are never dropped (minimum-content guarantee).
    summary_max = 48 if aggressive else DEFAULT_SUMMARY_MAX_WORDS
    max_roles = 2 if aggressive else DEFAULT_MAX_EXPERIENCE_ROLES
    max_projects = 1 if aggressive else DEFAULT_MAX_PROJECTS
    bullets_top = 2 if aggressive else DEFAULT_BULLETS_TOP_ROLE
    bullets_other = 1 if aggressive else DEFAULT_BULLETS_OTHER_ROLE
    bullets_proj = 1 if aggressive else DEFAULT_BULLETS_PROJECT
    max_skills = 4 if aggressive else DEFAULT_MAX_SKILL_LINES
    max_total = 10 if aggressive else DEFAULT_MAX_TOTAL_BULLETS

    out = deepcopy(resume)
    summary = str(out.get("professional_summary") or out.get("summary") or "").strip()
    # Keep at most 3 sentences
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary) if s.strip()]
    summary = " ".join(sentences[:3])
    summary = _trim_words(summary, summary_max)
    out["professional_summary"] = summary
    out["summary"] = summary

    def _score_bullet(b: str) -> int:
        return _bullet_score(
            b,
            emphasize,
            strongest=strongest,
            weaker=weaker,
            requirement_terms=req_terms,
            requirement_phrases=req_phrases,
            must_keep=must_keep,
        )

    # Experience — rank by relevance, but NEVER drop a real role entirely.
    # Low-relevance entries are shortened (fewer bullets), not omitted.
    roles = [e for e in (out.get("experience") or []) if isinstance(e, dict)]
    ranked_roles = _rank_entries(
        roles,
        emphasize,
        name_keys=("company", "title"),
        requirement_terms=req_terms,
        requirement_phrases=req_phrases,
        must_keep=must_keep,
    )
    compressed_roles: list[dict[str, Any]] = []
    total_bullets = 0
    for i, role in enumerate(ranked_roles):
        entry = dict(role)
        raw_bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        raw_bullets = _dedupe_similar(raw_bullets)
        if i == 0:
            limit = bullets_top
        elif i < max_roles:
            limit = bullets_other
        else:
            # Beyond soft role budget: keep the entry with a single strongest bullet
            limit = 1
        # Reserve at least 1 bullet per remaining role so none are emptied by budget
        remaining_roles = len(ranked_roles) - i
        room = max(0, max_total - total_bullets)
        limit = min(limit, max(1, room - max(0, remaining_roles - 1))) if room else 1
        kept = select_bullets_with_coverage(
            raw_bullets,
            limit=limit if raw_bullets else 0,
            requirement_terms=req_terms,
            phrases=req_phrases,
            score_fn=_score_bullet,
        )
        # Guarantee: if source had bullets, keep at least one
        if not kept and raw_bullets:
            kept = [raw_bullets[0]]
        entry["bullets"] = kept
        if not kept:
            continue
        total_bullets += len(kept)
        compressed_roles.append(entry)
    out["experience"] = compressed_roles

    # Projects — reorder by relevance; never drop a whole project entry.
    projects = [p for p in (out.get("projects") or []) if isinstance(p, dict)]
    ranked_projects = _rank_entries(
        projects,
        emphasize,
        name_keys=("name",),
        requirement_terms=req_terms,
        requirement_phrases=req_phrases,
        must_keep=must_keep,
    )
    compressed_projects: list[dict[str, Any]] = []
    for i, project in enumerate(ranked_projects):
        entry = dict(project)
        raw_bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        raw_bullets = _dedupe_similar(raw_bullets)
        room = max(0, max_total - total_bullets)
        remaining_projects = len(ranked_projects) - i
        # Soft project budget: full bullets for top projects, 1 for the rest
        base_limit = bullets_proj if i < max_projects else 1
        keep_n = min(base_limit, room if room > 0 else 1)
        # Reserve 1 slot per remaining project when possible
        if remaining_projects > 1 and room > 0:
            keep_n = min(keep_n, max(1, room - (remaining_projects - 1)))
        kept = select_bullets_with_coverage(
            raw_bullets,
            limit=keep_n if raw_bullets else 0,
            requirement_terms=req_terms,
            phrases=req_phrases,
            score_fn=_score_bullet,
        )
        if not kept and raw_bullets:
            kept = [raw_bullets[0]]
        entry["bullets"] = kept
        desc = str(entry.get("description") or "").strip()
        if desc and len(desc.split()) > 28:
            entry["description"] = _trim_words(desc, 28)
            desc = entry["description"]
        # Drop description when any kept bullet already covers the same claim
        if desc and kept and any(texts_are_near_duplicates(desc, b) for b in kept):
            entry["description"] = ""
            desc = ""
        # Aggressive / over-budget: prefer bullets over description to save lines
        if desc and kept and (aggressive or i >= max_projects or total_bullets >= max_total):
            entry["description"] = ""
            desc = ""
        entry = scrub_duplicate_entry_content(entry)
        kept = list(entry.get("bullets") or [])
        desc = str(entry.get("description") or "").strip()
        # Keep description-only projects rather than dropping the entry
        if not kept and not desc:
            # Last resort: restore a truncated original description if present
            orig_desc = str(project.get("description") or "").strip()
            if orig_desc:
                entry["description"] = _trim_words(orig_desc, 28)
                desc = entry["description"]
            elif raw_bullets:
                entry["bullets"] = [raw_bullets[0]]
                kept = entry["bullets"]
            else:
                continue
        total_bullets += len(kept) + (1 if desc else 0)
        compressed_projects.append(entry)
    out["projects"] = compressed_projects

    # Skills — keep shared technologies; preserve atoms from dropped category lines
    from intelligent_tailoring.requirement_coverage import prioritize_skill_lines

    skills = [str(s).strip() for s in (out.get("skills") or []) if str(s).strip()]
    # Allow more skill lines so weak-match jobs don't erase the candidate's stack
    keep_skills = max(max_skills, 6) if len(skills) >= 3 else max_skills
    out["skills"] = prioritize_skill_lines(
        skills,
        shared_tech=shared_tech or emphasize,
        max_lines=keep_skills,
        preserve_all_atoms=True,
    )

    # Education / certs — normalize + keep but don't explode
    from intelligent_tailoring.canonical_resume import normalize_education_entries

    out["education"] = normalize_education_entries(out.get("education"))[:2]
    certs = list(out.get("certifications") or [])[:3]
    out["certifications"] = certs

    out["_one_page"] = {
        "compressed": True,
        "aggressive": aggressive,
        "estimate": estimate_page_pressure(out),
    }
    return out


def compress_until_likely_fit(
    resume: dict[str, Any],
    *,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compress once, then aggressively if pressure remains high."""
    first = compress_resume_to_one_page(resume, strategy=strategy, aggressive=False)
    if estimate_page_pressure(first)["likely_fits_one_page"]:
        return first
    return compress_resume_to_one_page(first, strategy=strategy, aggressive=True)
