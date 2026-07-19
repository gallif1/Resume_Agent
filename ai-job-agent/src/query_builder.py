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

# Broad titles that dominate board rankings and collapse CV diversity when used alone.
_GENERIC_QUERY_RE = re.compile(
    r"^(software\s+engineer|software\s+developer|developer|engineer|"
    r"מפתח|מפתח\s+תוכנה|מהנדס\s+תוכנה|programmer|full\s*stack(\s+developer)?)$",
    re.IGNORECASE,
)


# Boards where Hebrew/mixed tech queries return near-zero relevant listings.
ENGLISH_ONLY_BOARDS = frozenset({
    "linkedin",
    "gotfriends",
    "indeed",
    "secret_tel_aviv",
    "geektime",
})
BILINGUAL_BOARDS = frozenset({"drushim", "alljobs"})


def _is_hebrew(text: str) -> bool:
    return bool(_HEBREW_RE.search(text or ""))


def is_english_query(text: str) -> bool:
    """True for non-empty queries with no Hebrew characters."""
    value = str(text or "").strip()
    return bool(value) and not _is_hebrew(value)


def is_hebrew_query(text: str) -> bool:
    """True when the query contains Hebrew (pure or mixed)."""
    return _is_hebrew(str(text or "").strip())


def _query_specificity_score(query: str, profile_technologies: list[str] | None = None) -> int:
    """Higher score = more CV-specific / less likely to return identical top results.
    
    Uses dynamic profile technologies instead of hardcoded tech stacks.
    """
    text = (query or "").strip()
    if not text:
        return -100
    words = [w for w in re.split(r"\s+", text) if w]
    score = 0
    if _GENERIC_QUERY_RE.match(text):
        score -= 40
    if len(words) >= 2:
        score += 12
    if len(words) >= 3:
        score += 8
    if any(ch.isdigit() for ch in text):
        score += 4
    # Prefer role+tech / mixed-script queries that differentiate candidates.
    if _is_hebrew(text) and re.search(r"[A-Za-z]", text):
        score += 18
    
    # Dynamic technology matching - check if query contains any candidate's technologies
    if profile_technologies:
        text_lower = text.lower()
        for tech in profile_technologies:
            if tech and tech.lower() in text_lower:
                score += 20
                break
    
    # Bare single-token generic searches collapse across CVs.
    if len(words) == 1 and len(text) <= 12:
        score -= 10
    return score


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


def select_diverse_queries(
    queries: list[str],
    *,
    max_items: int,
    profile_technologies: list[str] | None = None,
    pinned: list[str] | None = None,
) -> list[str]:
    """Pick up to max_items queries, preferring specific/varied terms over generics.

    ``pinned`` queries (e.g. user-selected domain + synonym expansions) are always
    kept first so truncation cannot drop the exact titles the user asked to search.
    Remaining slots prefer technology-/domain-specific queries.
    """
    if max_items <= 0:
        return []

    pinned_clean = dedupe_queries(list(pinned or []), max_items=max_items)
    remaining_budget = max_items - len(pinned_clean)
    if remaining_budget <= 0:
        return pinned_clean[:max_items]

    pinned_keys = {q.casefold() for q in pinned_clean}
    cleaned = [
        q
        for q in dedupe_queries(queries, max_items=max(len(queries), max_items))
        if q.casefold() not in pinned_keys
    ]
    if not cleaned:
        return pinned_clean

    if len(cleaned) <= remaining_budget:
        return dedupe_queries(pinned_clean + cleaned, max_items=max_items)

    ranked = sorted(
        enumerate(cleaned),
        key=lambda item: (-_query_specificity_score(item[1], profile_technologies), item[0]),
    )
    chosen: list[str] = []
    chosen_keys: set[str] = set()
    for _, query in ranked:
        if len(chosen) >= remaining_budget:
            break
        key = query.casefold()
        if key in chosen_keys:
            continue
        chosen.append(query)
        chosen_keys.add(key)

    order = {q.casefold(): index for index, q in enumerate(cleaned)}
    chosen.sort(key=lambda q: order.get(q.casefold(), 0))
    return dedupe_queries(pinned_clean + chosen, max_items=max_items)


def expand_domain_search_queries(domain: str, *, max_items: int = 16) -> list[str]:
    """Exact-domain fallback only.

    Real synonym/category expansion happens via ``domain_query_expander``
    (OpenAI) when the user selects domains for collect. This helper remains for
    offline / AI-unavailable paths so boards still search the selected label.
    """
    text = str(domain or "").strip()
    if not text:
        return []
    return [text][:max_items]


def inject_domain_query_expansions(
    entry: dict[str, Any],
    domain: str,
    *,
    queries: list[str] | None = None,
) -> dict[str, Any]:
    """Pin AI (or fallback) domain queries so they survive query-budget truncation.

    ``queries`` should be the flattened EN+HE list from
    ``expand_selected_domains_with_ai``. When omitted, only the exact domain
    label is pinned (no synonym dictionary).
    """
    enriched = dict(entry)
    expansions = dedupe_queries(
        [str(q).strip() for q in (queries or []) if str(q).strip()]
        or [str(domain or "").strip()],
        max_items=MAX_QUERIES_TOTAL,
    )
    if not expansions:
        return enriched

    en = [q for q in expansions if is_english_query(q)]
    he = [q for q in expansions if is_hebrew_query(q)]

    enriched["primary_role"] = domain
    enriched["priority_queries"] = list(expansions)
    enriched["queries_en"] = dedupe_queries(
        en + list(enriched.get("queries_en") or []),
        max_items=MAX_QUERIES_TOTAL,
    )
    enriched["search_queries"] = dedupe_queries(
        en + list(enriched.get("search_queries") or []),
        max_items=MAX_QUERIES_TOTAL,
    )
    enriched["queries_he"] = dedupe_queries(
        he + list(enriched.get("queries_he") or []),
        max_items=MAX_QUERIES_HE,
    )
    enriched["hebrew_search_queries"] = dedupe_queries(
        he + list(enriched.get("hebrew_search_queries") or []),
        max_items=MAX_QUERIES_HE,
    )
    enriched["queries"] = dedupe_queries(
        expansions + list(enriched.get("queries") or []),
        max_items=MAX_QUERIES_TOTAL,
    )
    return enriched


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

    # Role+technology combos first so truncated query budgets stay CV-specific.
    tech_role_queries: list[str] = []
    techs = [t for t in technologies if t and not _is_hebrew(t)][:5]
    for role in (preferred + alternative)[:4]:
        role_text = str(role or "").strip()
        if not role_text:
            continue
        for tech in techs:
            if tech.lower() in role_text.lower():
                continue
            tech_role_queries.append(f"{role_text} {tech}".strip())

    queries_en = dedupe_queries(
        tech_role_queries + preferred + alternative + keywords_en,
        max_items=MAX_QUERIES_EN,
    )
    queries_he = dedupe_queries(keywords_he, max_items=MAX_QUERIES_HE)
    if not queries_he:
        _, he_from_alt = split_keywords_by_script(alternative)
        queries_he = dedupe_queries(he_from_alt, max_items=MAX_QUERIES_HE)

    queries_mixed = build_mixed_queries(
        queries_en, queries_he, technologies=technologies, max_items=MAX_QUERIES_MIXED
    )

    # English titles first so English-first boards are not starved by Hebrew mixes.
    all_queries = dedupe_queries(
        tech_role_queries + queries_en + queries_mixed + queries_he,
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

    # Partition any leftover flat queries into language buckets.
    for raw in list(entry.get("queries") or []):
        text = str(raw or "").strip()
        if not text:
            continue
        if _is_hebrew(text) and re.search(r"[A-Za-z]", text):
            queries_mixed.append(text)
        elif _is_hebrew(text):
            queries_he.append(text)
        else:
            queries_en.append(text)

    queries_en = dedupe_queries(
        [q for q in queries_en if is_english_query(q)],
        max_items=MAX_QUERIES_EN,
    )
    queries_he = dedupe_queries(queries_he, max_items=MAX_QUERIES_HE)
    queries_mixed = dedupe_queries(queries_mixed, max_items=MAX_QUERIES_MIXED)
    all_queries = dedupe_queries(
        queries_en + queries_mixed + queries_he,
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
        "queries_en": queries_en,
        "queries_he": queries_he,
        "queries_mixed": queries_mixed,
        "search_queries": queries_en,
        "hebrew_search_queries": queries_he,
        "queries": all_queries,
        "exclude_keywords": exclusion,
    }


def _english_query_pool(entry: dict[str, Any]) -> list[str]:
    """English-only search terms for LinkedIn / GotFriends."""
    pool: list[str] = []
    for key in ("queries_en", "search_queries", "alternative_titles"):
        value = entry.get(key)
        if isinstance(value, list):
            pool.extend(str(item) for item in value)

    primary = str(entry.get("primary_role") or "").strip()
    if primary and is_english_query(primary):
        pool.insert(0, primary)

    # Recover English titles buried in the flat list (legacy strategies).
    for item in entry.get("queries") or []:
        text = str(item or "").strip()
        if is_english_query(text):
            pool.append(text)

    return dedupe_queries(pool, max_items=MAX_QUERIES_TOTAL)


def _bilingual_query_pool(entry: dict[str, Any]) -> list[str]:
    """Hebrew + English + mixed terms for Drushim."""
    pool: list[str] = []
    for key in (
        "queries_en",
        "search_queries",
        "queries_mixed",
        "queries_he",
        "hebrew_search_queries",
        "queries",
        "alternative_titles",
    ):
        value = entry.get(key)
        if isinstance(value, list):
            pool.extend(str(item) for item in value)

    primary = str(entry.get("primary_role") or "").strip()
    if primary:
        pool.insert(0, primary)
    return dedupe_queries(pool, max_items=MAX_QUERIES_TOTAL)


def queries_for_board(
    entry: dict[str, Any],
    board_id: str,
    *,
    max_items: int,
    profile_technologies: list[str] | None = None,
) -> list[str]:
    """Select search queries appropriate for a specific job board.

    LinkedIn, GotFriends, Indeed, Secret Tel Aviv, and Geektime list roles almost
    exclusively in English, so Hebrew/mixed terms may return near-zero results.
    Drushim and AllJobs remain bilingual.
    Uses dynamic profile technologies for query ranking.
    User-selected domain expansions in ``priority_queries`` are pinned first.
    """
    board = str(board_id or "").strip().lower()
    if max_items <= 0:
        return []

    pinned_raw = list(entry.get("priority_queries") or [])
    if board in ENGLISH_ONLY_BOARDS:
        pinned = [q for q in pinned_raw if is_english_query(q)]
        english = _english_query_pool(entry)
        selected = select_diverse_queries(
            english,
            max_items=max_items,
            profile_technologies=profile_technologies,
            pinned=pinned,
        )
        if selected:
            return selected
        # Absolute fallback: never send Hebrew to English-only boards.
        return []

    # Default / Drushim: keep bilingual capability.
    return select_diverse_queries(
        _bilingual_query_pool(entry),
        max_items=max_items,
        profile_technologies=profile_technologies,
        pinned=pinned_raw,
    )
