"""Deterministic bilingual job-search query assembly (no AI)."""

from __future__ import annotations

import re
from typing import Any

from rule_based_matcher import SENIOR_KEYWORDS

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")

MAX_QUERIES_EN = 8
MAX_QUERIES_HE = 5
MAX_QUERIES_MIXED = 4
MAX_QUERIES_TOTAL = 16


def _is_hebrew(text: str) -> bool:
    return bool(_HEBREW_RE.search(text or ""))


def dedupe_queries(queries: list[str], *, max_items: int = MAX_QUERIES_TOTAL) -> list[str]:
    """Deduplicate search queries case-insensitively, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for query in queries:
        text = str(query or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= max_items:
            break
    return result


def split_keywords_by_script(keywords: list[str]) -> tuple[list[str], list[str]]:
    """Split keywords into English and Hebrew lists."""
    en: list[str] = []
    he: list[str] = []
    for kw in keywords:
        text = str(kw or "").strip()
        if not text:
            continue
        if _is_hebrew(text):
            if text not in he:
                he.append(text)
        else:
            if text not in en:
                en.append(text)
    return en, he


def build_mixed_queries(
    queries_en: list[str],
    queries_he: list[str],
    *,
    technologies: list[str] | None = None,
    max_items: int = MAX_QUERIES_MIXED,
) -> list[str]:
    """Build Hebrew-English mixed queries when useful (e.g. 'מפתח Python')."""
    mixed: list[str] = []
    techs = [t for t in (technologies or []) if t and not _is_hebrew(t)][:4]
    he_roles = [q for q in queries_he if len(q) >= 2][:3]
    en_roles = [q for q in queries_en if len(q) >= 2][:3]

    for he_role in he_roles:
        for tech in techs:
            candidate = f"{he_role} {tech}".strip()
            if candidate not in mixed:
                mixed.append(candidate)
            if len(mixed) >= max_items:
                return mixed

    for en_role in en_roles[:2]:
        for he_fragment in he_roles[:2]:
            if _is_hebrew(he_fragment) and not _is_hebrew(en_role):
                candidate = f"{he_fragment} {en_role}".strip()
                if candidate not in mixed:
                    mixed.append(candidate)
                if len(mixed) >= max_items:
                    return mixed

    return mixed[:max_items]


def _seniority_exclusions(seniority: str) -> list[str]:
    seniority = (seniority or "unknown").lower()
    if seniority in ("junior", "student", "intern"):
        return list(SENIOR_KEYWORDS[:8])
    if seniority in ("senior", "lead", "manager"):
        return ["junior", "entry", "graduate", "student", "ג'וניור", "גוניור", "סטודנט"]
    return []


def build_collection_query_entry(
    profile: dict[str, Any],
    *,
    category: str = "",
    priority: int = 80,
) -> dict[str, Any]:
    """Build one collection_queries entry from universal profile fields."""
    preferred = list(profile.get("preferred_role_titles") or [])
    alternative = list(profile.get("alternative_role_titles") or [])
    keywords_en = list(profile.get("search_keywords_en") or [])
    keywords_he = list(profile.get("search_keywords_he") or [])
    exclusion = list(profile.get("exclusion_keywords") or [])
    technologies = list(profile.get("technologies_tools") or [])
    seniority = str(profile.get("seniority_level") or "unknown")

    queries_en = dedupe_queries(
        preferred + alternative + keywords_en,
        max_items=MAX_QUERIES_EN,
    )
    queries_he = dedupe_queries(keywords_he, max_items=MAX_QUERIES_HE)
    if not queries_he:
        _, he_from_alt = split_keywords_by_script(alternative)
        queries_he = dedupe_queries(he_from_alt, max_items=MAX_QUERIES_HE)

    queries_mixed = build_mixed_queries(
        queries_en, queries_he, technologies=technologies, max_items=MAX_QUERIES_MIXED
    )

    all_queries = dedupe_queries(
        queries_en + queries_he + queries_mixed,
        max_items=MAX_QUERIES_TOTAL,
    )

    if not category:
        if preferred:
            category = preferred[0].lower().replace(" ", "_")[:40]
        elif queries_en:
            category = queries_en[0].lower().replace(" ", "_")[:40]
        else:
            category = "primary"

    exclude = dedupe_queries(
        exclusion + _seniority_exclusions(seniority),
        max_items=15,
    )

    primary_role = preferred[0] if preferred else (queries_en[0] if queries_en else queries_he[0] if queries_he else "")

    return {
        "category": category,
        "priority": max(0, min(100, priority)),
        "primary_role": primary_role,
        "queries_en": queries_en,
        "queries_he": queries_he,
        "queries_mixed": queries_mixed,
        "search_queries": queries_en,
        "hebrew_search_queries": queries_he,
        "queries": all_queries,
        "exclude_keywords": exclude,
    }


def build_collection_queries(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Build collection query plan from a universal profile."""
    existing = profile.get("collection_queries")
    if isinstance(existing, list) and existing:
        return [normalize_collection_entry(entry, profile) for entry in existing if entry]

    canonical_roles = list(profile.get("canonical_roles") or [])
    entries: list[dict[str, Any]] = []

    if len(canonical_roles) > 1:
        for index, role in enumerate(canonical_roles[:4]):
            entry_profile = dict(profile)
            entry_profile["preferred_role_titles"] = [role]
            entry_profile["alternative_role_titles"] = []
            entries.append(
                build_collection_query_entry(
                    entry_profile,
                    category=role.lower().replace(" ", "_")[:40],
                    priority=max(50, 90 - index * 10),
                )
            )
    else:
        entries.append(build_collection_query_entry(profile))

    return entries


def normalize_collection_entry(entry: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Normalize an AI-provided collection entry and enrich with mixed queries."""
    if not isinstance(entry, dict):
        return build_collection_query_entry(profile)

    queries_en = list(entry.get("queries_en") or entry.get("search_queries") or [])
    queries_he = list(entry.get("queries_he") or entry.get("hebrew_search_queries") or [])
    queries_mixed = list(entry.get("queries_mixed") or [])

    if not queries_en and entry.get("primary_role"):
        queries_en = [str(entry["primary_role"])]
    for key in ("alternative_titles",):
        value = entry.get(key)
        if isinstance(value, list):
            queries_en.extend(str(v) for v in value)

    technologies = list(profile.get("technologies_tools") or [])
    if not queries_mixed:
        queries_mixed = build_mixed_queries(queries_en, queries_he, technologies=technologies)

    all_queries = dedupe_queries(
        queries_en + queries_he + queries_mixed + list(entry.get("queries") or []),
        max_items=MAX_QUERIES_TOTAL,
    )

    category = str(entry.get("category") or "primary")
    try:
        priority = int(round(float(entry.get("priority", 80))))
    except (TypeError, ValueError):
        priority = 80

    exclusion = list(entry.get("exclude_keywords") or profile.get("exclusion_keywords") or [])
    exclusion = dedupe_queries(
        exclusion + _seniority_exclusions(str(profile.get("seniority_level") or "")),
        max_items=15,
    )

    primary_role = str(entry.get("primary_role") or (queries_en[0] if queries_en else ""))

    return {
        "category": category,
        "priority": max(0, min(100, priority)),
        "primary_role": primary_role,
        "queries_en": dedupe_queries(queries_en, max_items=MAX_QUERIES_EN),
        "queries_he": dedupe_queries(queries_he, max_items=MAX_QUERIES_HE),
        "queries_mixed": dedupe_queries(queries_mixed, max_items=MAX_QUERIES_MIXED),
        "search_queries": dedupe_queries(queries_en, max_items=MAX_QUERIES_EN),
        "hebrew_search_queries": dedupe_queries(queries_he, max_items=MAX_QUERIES_HE),
        "queries": all_queries,
        "exclude_keywords": exclusion,
    }
