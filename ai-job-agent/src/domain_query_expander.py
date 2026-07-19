"""AI expansion of user-selected search domains into board query variants.

When a user marks domains in Step 2, this module asks the LLM to invent
realistic job-board titles / synonyms for each domain (any profession), then
those queries drive LinkedIn/Drushim/etc. collection.
"""

from __future__ import annotations

import json
from typing import Any

from ai_client import (
    OpenAIAPIError,
    call_openai_json,
    is_ai_available,
    normalize_string_list,
)
from config import OPENAI_MODEL

DOMAIN_EXPAND_SYSTEM = """You are a job-search strategist for the Israeli and global market.
Your job: given one or more user-selected search domains, invent the concrete
job-board search queries that will find the most relevant open positions.

Work for ANY profession equally (healthcare, education, finance, law, marketing,
logistics, hospitality, trades, engineering, IT, creative, public sector, etc.).
Do NOT favor software/IT unless the selected domain is in that field.

Return ONE JSON object:
{
  "expansions": [
    {
      "domain": "exact domain string from the user",
      "search_queries_en": ["distinct English titles to search on LinkedIn-style boards"],
      "search_queries_he": ["Hebrew titles useful on Israeli boards, or []"]
    }
  ]
}

Rules:
- One expansion object per input domain (same order / same domain string).
- search_queries_en: 5-10 DISTINCT, highly searchable job titles employers would post.
  Always include the original domain as one entry.
  Add realistic synonyms and adjacent titles for THAT domain
  (e.g. role heads like Specialist / Engineer / Coordinator / Analyst / Technician
  only when they make sense for the domain — invent from reasoning, not a fixed list).
- search_queries_he: 0-5 Hebrew equivalents when the Israeli market uses Hebrew titles.
- Do not invent unrelated career tracks. Stay close to the selected domain.
- Prefer titles that catch missed listings (different wording, same intent).
- Return valid JSON only, no markdown."""


def _fallback_expansion(domain: str) -> dict[str, list[str]]:
    """When AI is unavailable: search the exact domain only."""
    text = str(domain or "").strip()
    if not text:
        return {"search_queries_en": [], "search_queries_he": []}
    # Keep Hebrew domains in the Hebrew bucket so bilingual boards still search them.
    has_hebrew = any("\u0590" <= ch <= "\u05FF" for ch in text)
    if has_hebrew:
        return {"search_queries_en": [], "search_queries_he": [text]}
    return {"search_queries_en": [text], "search_queries_he": []}


def _normalize_expansions(
    raw: dict[str, Any],
    domains: list[str],
) -> dict[str, dict[str, list[str]]]:
    """Map domain → {search_queries_en, search_queries_he} with safe fallbacks."""
    by_domain: dict[str, dict[str, list[str]]] = {}
    rows = raw.get("expansions") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        rows = []

    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("domain") or "").strip()
        if key:
            indexed[key.casefold()] = row

    for domain in domains:
        text = str(domain or "").strip()
        if not text:
            continue
        row = indexed.get(text.casefold(), {})
        en = normalize_string_list(row.get("search_queries_en"), max_items=12)
        he = normalize_string_list(row.get("search_queries_he"), max_items=6)
        # Always keep the exact selected label.
        has_hebrew = any("\u0590" <= ch <= "\u05FF" for ch in text)
        if has_hebrew:
            if text not in he:
                he = [text, *he]
        else:
            if text not in en and text.casefold() not in {q.casefold() for q in en}:
                en = [text, *en]
        if not en and not he:
            fallback = _fallback_expansion(text)
            en = fallback["search_queries_en"]
            he = fallback["search_queries_he"]
        by_domain[text] = {"search_queries_en": en, "search_queries_he": he}
    return by_domain


def expand_selected_domains_with_ai(
    domains: list[str],
    *,
    candidate_summary: str = "",
    use_ai: bool = True,
) -> dict[str, dict[str, list[str]]]:
    """Expand user-selected domains into EN/HE board search queries via OpenAI.

    Returns ``{domain: {"search_queries_en": [...], "search_queries_he": [...]}}``.
    Falls back to exact-domain-only queries when AI is unavailable or fails.
    """
    cleaned = [str(d).strip() for d in domains if str(d).strip()]
    if not cleaned:
        return {}

    if not use_ai or not is_ai_available():
        return {d: _fallback_expansion(d) for d in cleaned}

    payload = {
        "selected_domains": cleaned,
        "candidate_summary": (candidate_summary or "").strip()[:1200],
    }
    user_prompt = (
        "Expand these user-selected job-search domains into concrete board queries.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        raw = call_openai_json(
            DOMAIN_EXPAND_SYSTEM,
            user_prompt,
            model=OPENAI_MODEL,
            temperature=0.3,
            max_tokens=1600,
            cache_key=None,  # domains are short; always fresh for the selected set
        )
    except (OpenAIAPIError, Exception) as exc:
        print(f"  AI domain expansion unavailable ({exc}) — using exact domain labels")
        return {d: _fallback_expansion(d) for d in cleaned}

    return _normalize_expansions(raw if isinstance(raw, dict) else {}, cleaned)


def flatten_expansion_queries(expansion: dict[str, list[str]] | None) -> list[str]:
    """EN then HE queries for pinning onto a collection plan entry."""
    if not expansion:
        return []
    en = list(expansion.get("search_queries_en") or [])
    he = list(expansion.get("search_queries_he") or [])
    seen: set[str] = set()
    out: list[str] = []
    for q in en + he:
        key = q.casefold()
        if not q or key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out
