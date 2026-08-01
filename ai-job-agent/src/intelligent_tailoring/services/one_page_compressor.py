"""One-page resume compressor — intelligent density, not blind deletion.

Default policy: every tailored resume must fit on a single A4 page.
Compress by prioritizing relevance, removing repetition, and capping bullets
while preserving professional readability (no micro-fonts).
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


def _bullet_score(
    bullet: str,
    emphasize: list[str],
    *,
    strongest: list[str] | None = None,
    weaker: list[str] | None = None,
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
    return score


def _dedupe_similar(bullets: list[str]) -> list[str]:
    """Drop near-duplicate bullets (shared opening + high token overlap)."""
    kept: list[str] = []
    for bullet in bullets:
        tokens = set(re.findall(r"[a-z0-9+]{3,}", _norm(bullet)))
        duplicate = False
        for prior in kept:
            prior_tokens = set(re.findall(r"[a-z0-9+]{3,}", _norm(prior)))
            if not tokens or not prior_tokens:
                continue
            overlap = len(tokens & prior_tokens) / max(len(tokens | prior_tokens), 1)
            if overlap >= 0.72:
                duplicate = True
                break
            # Same first 4 words
            if " ".join(_norm(bullet).split()[:4]) == " ".join(_norm(prior).split()[:4]):
                duplicate = True
                break
        if not duplicate:
            kept.append(bullet)
    return kept


def _rank_entries(
    entries: list[dict[str, Any]],
    emphasize: list[str],
    *,
    name_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        blob = " ".join(
            [str(entry.get(k) or "") for k in name_keys]
            + [str(entry.get("description") or "")]
            + [str(b) for b in (entry.get("bullets") or [])]
        )
        score = _bullet_score(blob, emphasize)
        scored.append((score, -idx, entry))  # stable: prefer original order on ties
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [e for _, __, e in scored]


def estimate_page_pressure(resume: dict[str, Any]) -> dict[str, Any]:
    """Heuristic pressure score; higher means more likely to overflow one page."""
    summary = str(resume.get("professional_summary") or resume.get("summary") or "")
    exp = [e for e in (resume.get("experience") or []) if isinstance(e, dict)]
    projects = [p for p in (resume.get("projects") or []) if isinstance(p, dict)]
    skills = list(resume.get("skills") or [])
    bullets = 0
    for e in exp:
        bullets += len([b for b in (e.get("bullets") or []) if str(b).strip()])
    for p in projects:
        bullets += len([b for b in (p.get("bullets") or []) if str(b).strip()])
        if str(p.get("description") or "").strip():
            bullets += 1
    words = len(summary.split()) + sum(
        len(str(b).split())
        for e in exp + projects
        for b in (e.get("bullets") or [])
    )
    # Empirically: ~14 bullets + ~60-word summary + 3 roles + 2 projects ≈ one page
    pressure = 0
    pressure += max(0, bullets - DEFAULT_MAX_TOTAL_BULLETS) * 8
    pressure += max(0, len(exp) - DEFAULT_MAX_EXPERIENCE_ROLES) * 12
    pressure += max(0, len(projects) - DEFAULT_MAX_PROJECTS) * 10
    pressure += max(0, len(summary.split()) - DEFAULT_SUMMARY_MAX_WORDS) * 1
    pressure += max(0, len(skills) - DEFAULT_MAX_SKILL_LINES) * 4
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
            or []
        )
        if str(s).strip()
    ]
    strongest = [
        str(s)
        for s in (
            strategy.get("strongest_evidence")
            or strategy.get("facts_to_expand")
            or []
        )
        if str(s).strip()
    ][:12]
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

    summary_max = 48 if aggressive else DEFAULT_SUMMARY_MAX_WORDS
    max_roles = 2 if aggressive else DEFAULT_MAX_EXPERIENCE_ROLES
    max_projects = 1 if aggressive else DEFAULT_MAX_PROJECTS
    bullets_top = 2 if aggressive else DEFAULT_BULLETS_TOP_ROLE
    bullets_other = 2 if aggressive else DEFAULT_BULLETS_OTHER_ROLE
    bullets_proj = 2 if aggressive else DEFAULT_BULLETS_PROJECT
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

    # Experience — rank, cap roles, cap bullets
    roles = [e for e in (out.get("experience") or []) if isinstance(e, dict)]
    ranked_roles = _rank_entries(roles, emphasize, name_keys=("company", "title"))
    compressed_roles: list[dict[str, Any]] = []
    total_bullets = 0
    for i, role in enumerate(ranked_roles[:max_roles]):
        entry = dict(role)
        raw_bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        raw_bullets = _dedupe_similar(raw_bullets)
        limit = bullets_top if i == 0 else bullets_other
        scored = sorted(
            raw_bullets,
            key=lambda b: _bullet_score(
                b, emphasize, strongest=strongest, weaker=weaker
            ),
            reverse=True,
        )
        # Prefer strongest evidence order for interview probability
        kept = scored[:limit]
        entry["bullets"] = kept
        total_bullets += len(kept)
        compressed_roles.append(entry)
    out["experience"] = compressed_roles

    # Projects
    projects = [p for p in (out.get("projects") or []) if isinstance(p, dict)]
    ranked_projects = _rank_entries(projects, emphasize, name_keys=("name",))
    compressed_projects: list[dict[str, Any]] = []
    for project in ranked_projects[:max_projects]:
        if total_bullets >= max_total:
            break
        entry = dict(project)
        raw_bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        raw_bullets = _dedupe_similar(raw_bullets)
        scored = sorted(
            raw_bullets,
            key=lambda b: _bullet_score(
                b, emphasize, strongest=strongest, weaker=weaker
            ),
            reverse=True,
        )
        room = max(0, max_total - total_bullets)
        kept = scored[: min(bullets_proj, room)]
        entry["bullets"] = kept
        desc = str(entry.get("description") or "").strip()
        if desc and len(desc.split()) > 28:
            entry["description"] = _trim_words(desc, 28)
        # Drop stub description if bullets already cover it
        if entry.get("description") and kept:
            if _norm(str(entry["description"]))[:40] in _norm(kept[0]):
                entry["description"] = ""
        total_bullets += len(kept)
        compressed_projects.append(entry)
    out["projects"] = compressed_projects

    # Skills — keep role-ordered lines, cap count
    skills = [str(s).strip() for s in (out.get("skills") or []) if str(s).strip()]
    out["skills"] = skills[:max_skills]

    # Education / certs — keep but don't explode
    education = [e for e in (out.get("education") or []) if isinstance(e, dict)]
    out["education"] = education[:2]
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
