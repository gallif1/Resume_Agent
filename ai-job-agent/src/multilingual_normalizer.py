"""Extensible multilingual normalization — maps Hebrew/English terms to canonical concepts."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from config import SYNONYM_DICTIONARY_PATH
from job_classifier import KEYWORD_SYNONYMS
from skills import SKILL_CATEGORIES

_HEBREW_CHARS_RE = re.compile(r"[\u0590-\u05FF]")
_HEBREW_PREFIXES = "ובלכשמה"

# Cross-term equivalences (moved from skill_normalizer for central management).
BUILTIN_EQUIVALENCES: dict[str, str] = {
    "office 365": "Microsoft 365",
    "microsoft 365": "Microsoft 365",
    "m365": "Microsoft 365",
    "o365": "Microsoft 365",
    "ad": "Active Directory",
    "active directory": "Active Directory",
    "help desk": "Technical Support",
    "helpdesk": "Technical Support",
    "help-desk": "Technical Support",
    "technical support": "Technical Support",
    "it support": "Technical Support",
    "תמיכה טכנית": "Technical Support",
    "soc analyst": "Security Analyst",
    "security analyst": "Security Analyst",
    "soc": "SOC",
    "microsoft office": "Microsoft Office",
    "ms office": "Microsoft Office",
    "amazon web services": "AWS",
    "google cloud platform": "GCP",
    "k8s": "Kubernetes",
    "node": "Node.js",
    "nodejs": "Node.js",
    "reactjs": "React",
    "react.js": "React",
    "postgres": "PostgreSQL",
    "mongo": "MongoDB",
    "dotnet": ".NET",
    ".net": ".NET",
    "אנגלית": "English",
    "עברית": "Hebrew",
    "ערבית": "Arabic",
    "סייבר": "Cybersecurity",
    "אבטחת מידע": "Cybersecurity",
}

# Session-scoped profile terms registered at match time.
_profile_terms: dict[str, str] = {}


def _clean(term: str) -> str:
    return re.sub(r"\s+", " ", (term or "").strip().lower())


def _strip_hebrew_prefixes(word: str) -> str:
    if not _HEBREW_CHARS_RE.search(word):
        return word
    stripped = word
    while stripped and stripped[0] in _HEBREW_PREFIXES:
        stripped = stripped[1:]
    return stripped or word


def _load_json_dictionary() -> dict[str, Any]:
    path = SYNONYM_DICTIONARY_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


@lru_cache(maxsize=1)
def _alias_lookup() -> dict[str, str]:
    """Map every alias (lowercase) to its canonical English concept."""
    lookup: dict[str, str] = {}

    for skills in SKILL_CATEGORIES.values():
        for canonical, aliases in skills.items():
            lookup[_clean(canonical)] = canonical
            for alias in aliases:
                lookup[_clean(alias)] = canonical

    for keyword, synonyms in KEYWORD_SYNONYMS.items():
        canonical = keyword.title() if keyword.islower() else keyword
        lookup[_clean(keyword)] = canonical
        for synonym in synonyms:
            lookup[_clean(synonym)] = canonical

    for alias, canonical in BUILTIN_EQUIVALENCES.items():
        lookup[_clean(alias)] = canonical

    for _key, entry in _load_json_dictionary().items():
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("canonical") or "").strip()
        if not canonical:
            continue
        lookup[_clean(canonical)] = canonical
        for field in ("synonyms_en", "synonyms_he"):
            for synonym in entry.get(field) or []:
                text = str(synonym).strip()
                if text:
                    lookup[_clean(text)] = canonical

    return lookup


@lru_cache(maxsize=1)
def _canonical_to_aliases() -> dict[str, set[str]]:
    """Map canonical concepts to all known alias forms."""
    mapping: dict[str, set[str]] = {}

    def add(canonical: str, alias: str) -> None:
        canonical = canonical.strip()
        alias = alias.strip()
        if not canonical or not alias:
            return
        mapping.setdefault(canonical, set()).add(alias)
        mapping.setdefault(canonical, set()).add(canonical)

    lookup = _alias_lookup()
    for alias, canonical in lookup.items():
        add(canonical, alias)

    for _key, entry in _load_json_dictionary().items():
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("canonical") or "").strip()
        if not canonical:
            continue
        for field in ("synonyms_en", "synonyms_he"):
            for synonym in entry.get(field) or []:
                add(canonical, str(synonym))

    for alias, canonical in _profile_terms.items():
        add(canonical, alias)

    return mapping


def clear_profile_terms() -> None:
    """Clear session-scoped profile terms (for tests)."""
    global _profile_terms
    _profile_terms = {}
    _canonical_to_aliases.cache_clear()


def register_profile_terms(universal_profile: dict[str, Any] | None) -> None:
    """Register CV-specific EN/HE terms from the universal profile for this session."""
    global _profile_terms
    _profile_terms = {}
    if not isinstance(universal_profile, dict):
        return

    term_groups = [
        universal_profile.get("canonical_roles") or [],
        universal_profile.get("canonical_skills") or [],
        universal_profile.get("technologies_tools") or [],
        universal_profile.get("domain_keywords") or [],
        universal_profile.get("preferred_role_titles") or [],
        universal_profile.get("alternative_role_titles") or [],
        universal_profile.get("search_keywords_en") or [],
        universal_profile.get("search_keywords_he") or [],
    ]

    for group in term_groups:
        if not isinstance(group, list):
            continue
        for term in group:
            text = str(term or "").strip()
            if not text:
                continue
            # Profile-specific terms are their own canonical concept.
            canonical = text
            _profile_terms[_clean(text)] = canonical

    _canonical_to_aliases.cache_clear()


def to_canonical(term: str) -> str:
    """Map any term to its canonical English concept."""
    cleaned = _clean(term)
    if not cleaned:
        return ""

    if cleaned in _profile_terms:
        return _profile_terms[cleaned]

    lookup = _alias_lookup()
    if cleaned in lookup:
        return lookup[cleaned]

    stripped = _strip_hebrew_prefixes(cleaned)
    if stripped != cleaned and stripped in lookup:
        return lookup[stripped]

    for alias, canonical in sorted(lookup.items(), key=lambda item: len(item[0]), reverse=True):
        if len(alias) >= 3 and alias in cleaned:
            return canonical

    return term.strip()


def expand_synonyms(canonical: str) -> set[str]:
    """Return all known variants for a canonical concept."""
    canon = to_canonical(canonical) or canonical.strip()
    aliases = _canonical_to_aliases().get(canon, set())
    if aliases:
        return set(aliases)
    return {canon.lower(), canon}


def _text_contains_term(text: str, term: str) -> bool:
    text_l = _clean(text)
    term_l = _clean(term)
    if not term_l:
        return False
    if term_l in text_l:
        return True

    canonical = to_canonical(term)
    for variant in expand_synonyms(canonical):
        variant_l = _clean(variant)
        if len(variant_l) < 2:
            continue
        if variant_l in text_l:
            return True
        if _HEBREW_CHARS_RE.search(variant_l):
            stripped = _strip_hebrew_prefixes(variant_l)
            if stripped in text_l:
                return True
    return False


def terms_overlap(candidate_terms: list[str], job_text: str) -> tuple[list[str], list[str]]:
    """Return (matched, missing) candidate terms found in job text."""
    matched: list[str] = []
    missing: list[str] = []
    for term in candidate_terms:
        text = str(term or "").strip()
        if not text:
            continue
        if _text_contains_term(job_text, text):
            matched.append(to_canonical(text) or text)
        else:
            missing.append(to_canonical(text) or text)
    return matched, missing


def title_similarity(title_a: str, title_b: str) -> float:
    """Token overlap score between two titles via canonical forms (0.0–1.0)."""
    def tokens(text: str) -> list[str]:
        raw = re.split(r"[^\w\u0590-\u05FF]+", text.lower(), flags=re.UNICODE)
        return [t for t in raw if len(t) >= 2]

    tokens_a = tokens(title_a)
    tokens_b = tokens(title_b)
    if not tokens_a or not tokens_b:
        return 0.0

    canon_a = {to_canonical(t) or t for t in tokens_a}
    canon_b = {to_canonical(t) or t for t in tokens_b}
    canon_a.discard("")
    canon_b.discard("")

    if not canon_a or not canon_b:
        return 0.0

    overlap = len(canon_a & canon_b)
    return overlap / max(len(canon_a), len(canon_b))


def best_title_similarity(title: str, role_titles: list[str]) -> float:
    """Best similarity score between a title and a list of role titles."""
    if not title or not role_titles:
        return 0.0
    return max((title_similarity(title, role) for role in role_titles), default=0.0)
