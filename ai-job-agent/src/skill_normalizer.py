"""Canonical skill normalization for ATS matching (English + Hebrew)."""

from __future__ import annotations

import re

from multilingual_normalizer import (
    expand_synonyms,
    to_canonical as _to_canonical,
)

# Re-export for backward compatibility.
from multilingual_normalizer import BUILTIN_EQUIVALENCES as SKILL_EQUIVALENCES

TECH_CATEGORIES = frozenset({
    "programming_languages",
    "frameworks_libraries",
    "databases",
    "cloud_devops_tools",
    "data_ai",
    "cyber_security",
})

CONTEXT_EQUIVALENCES: dict[str, dict[str, str]] = {
    "cyber": {
        "soc analyst": "Security Analyst",
        "analyst": "Security Analyst",
        "אנליסט": "Security Analyst",
    },
}


def _clean(term: str) -> str:
    return re.sub(r"\s+", " ", (term or "").strip().lower())


def normalize_skill(term: str, *, domain: str | None = None) -> str:
    """Return the canonical form of a skill/term."""
    cleaned = _clean(term)
    if not cleaned:
        return ""

    if domain and domain in CONTEXT_EQUIVALENCES:
        context_map = CONTEXT_EQUIVALENCES[domain]
        if cleaned in context_map:
            return context_map[cleaned]

    canonical = _to_canonical(term)
    if canonical and _clean(canonical) != cleaned:
        return canonical
    if canonical:
        return canonical

    return term.strip()


def normalize_skill_set(terms: list[str], *, domain: str | None = None) -> set[str]:
    """Normalize a list of skills and return a deduplicated set."""
    result: set[str] = set()
    for term in terms:
        canonical = normalize_skill(term, domain=domain)
        if canonical:
            result.add(canonical)
    return result


def skills_match(cv_skill: str, job_skill: str, *, domain: str | None = None) -> bool:
    """Return True when two skill terms are equivalent after normalization."""
    a = normalize_skill(cv_skill, domain=domain)
    b = normalize_skill(job_skill, domain=domain)
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True
    return _clean(a) == _clean(b) or _clean(a) in _clean(b) or _clean(b) in _clean(a)


def find_matching_skills(
    cv_skills: set[str],
    required_skills: list[str],
    *,
    domain: str | None = None,
) -> tuple[list[str], list[str]]:
    """Return (matched, missing) required skills against the CV skill set."""
    matched: list[str] = []
    missing: list[str] = []

    cv_normalized = {normalize_skill(s, domain=domain).lower() for s in cv_skills}
    cv_normalized.discard("")

    for req in required_skills:
        req_canon = normalize_skill(req, domain=domain)
        if not req_canon:
            continue
        req_l = req_canon.lower()
        found = any(
            skills_match(cv, req_canon, domain=domain)
            for cv in cv_skills
        ) or req_l in cv_normalized or any(req_l in cv for cv in cv_normalized)

        if not found:
            for variant in expand_synonyms(req_canon):
                if _clean(variant) in cv_normalized or any(
                    _clean(variant) in _clean(cv) for cv in cv_normalized
                ):
                    found = True
                    break

        if found:
            matched.append(req_canon)
        else:
            missing.append(req_canon)

    return matched, missing
