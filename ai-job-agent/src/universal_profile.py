"""Universal candidate profile — single AI extraction for search and matching."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any

from ai_client import OpenAIAPIError, call_openai_json, clamp_score, is_ai_available, normalize_string_list, truncate_text
from candidate_summary import flatten_skills
from config import OPENAI_CV_MAX_CHARS, OPENAI_MODEL, OPENAI_API_KEY
from query_builder import build_collection_queries, split_keywords_by_script
from rule_based_matcher import SENIOR_KEYWORDS

SENIORITY_LEVELS = frozenset({
    "intern", "student", "junior", "mid", "senior", "lead", "manager", "unknown",
})

UNIVERSAL_PROFILE_SYSTEM = """You are an expert career analyst for candidates in ANY professional field
(healthcare, education, finance, law, marketing, logistics, hospitality, engineering, IT, etc.).

Analyze the candidate and return ONE JSON object with exactly these keys:

{
  "canonical_roles": ["English role titles the candidate fits"],
  "canonical_skills": ["English skill/concept names"],
  "technologies_tools": ["tools/technologies ONLY if relevant to this CV; else []"],
  "domain_keywords": ["industry/domain terms in English"],
  "seniority_level": "intern|student|junior|mid|senior|lead|manager|unknown",
  "years_of_experience": 0,
  "education": ["degree/field strings"],
  "certifications": ["certification names"],
  "languages": ["Hebrew", "English", ...],
  "preferred_role_titles": ["ALL distinct English job titles relevant to this CV"],
  "alternative_role_titles": ["related/adjacent English titles worth searching"],
  "search_keywords_en": ["English search terms for job boards"],
  "search_keywords_he": ["Hebrew search terms for Israeli job boards"],
  "exclusion_keywords": ["terms that disqualify irrelevant jobs, EN and HE"],
  "collection_queries": [
    {
      "category": "short_category_slug",
      "priority": 90,
      "primary_role": "Primary Role Title",
      "search_queries": ["distinct English job titles for this category"],
      "hebrew_search_queries": ["Hebrew titles when relevant"],
      "alternative_titles": ["related titles"],
      "exclude_keywords": ["senior", "manager", "בכיר"]
    }
  ],
  "location_preferences": {
    "preferred_locations": ["Israel", "Tel Aviv"],
    "remote_ok": true
  },
  "candidate_summary": "2-3 sentence summary",
  "career_notes": "1-2 sentences on career direction"
}

Rules:
- Work for ANY profession equally — healthcare, education, finance, law, marketing,
  logistics, hospitality, trades, engineering, IT, creative, public sector, etc.
  Do not assume or favor software/IT unless the CV supports it.
- All canonical_roles, canonical_skills, preferred_role_titles must be in English.
- search_keywords_he must include Hebrew equivalents for role/domain words when relevant.
- technologies_tools: include only when the CV mentions specific tools; otherwise return [].
- collection_queries: one entry per realistic job-search category; concrete searchable titles.
- CRITICAL — preferred_role_titles: return EVERY distinct, highly relevant English job title
  the candidate can realistically search for. Cover ALL career tracks evidenced by past
  job titles, skills, education, projects, certifications, or stated interests.
  There is NO fixed count limit — include as many titles as are genuinely relevant.
  Do not drop secondary tracks just because one track looks strongest.
- alternative_role_titles: additional adjacent/related titles (including local-market
  variants expressed in English) that are still worth searching.
- Include past job titles from the CV when they remain realistic search targets.
- search_queries per category: several DISTINCT, highly relevant titles derived from
  THIS CV (not near-duplicates). Prefer skill-/domain-specific phrases over bare generics
  (avoid only "Software Engineer" / "Developer" / "מפתח" when the CV has specialties).
- Different CVs must produce different titles reflecting their specialties.
- Base analysis ONLY on provided candidate data. Do not invent employers or degrees.
- Consider the Israel job market when bilingual keywords are useful.
- Return valid JSON only, no markdown."""

VISION_SYSTEM = UNIVERSAL_PROFILE_SYSTEM + """

You are reading resume PAGE IMAGES (photo/scan). OCR all visible text including Hebrew,
then fill the same JSON schema."""


def _empty_universal_profile() -> dict[str, Any]:
    return {
        "canonical_roles": [],
        "canonical_skills": [],
        "technologies_tools": [],
        "domain_keywords": [],
        "seniority_level": "unknown",
        "years_of_experience": None,
        "education": [],
        "certifications": [],
        "languages": [],
        "preferred_role_titles": [],
        "alternative_role_titles": [],
        "search_keywords_en": [],
        "search_keywords_he": [],
        "exclusion_keywords": [],
        "collection_queries": [],
        "location_preferences": {
            "preferred_locations": [],
            "remote_ok": True,
        },
        "candidate_summary": "",
        "career_notes": "",
        "extracted_at": None,
        "source": "none",
    }


def _normalize_seniority(value: Any) -> str:
    seniority = str(value or "unknown").strip().lower()
    return seniority if seniority in SENIORITY_LEVELS else "unknown"


def _normalize_years(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def normalize_universal_profile(data: dict[str, Any], *, source: str = "openai") -> dict[str, Any]:
    """Validate and normalize universal profile from AI or fallback."""
    profile = _empty_universal_profile()
    if not isinstance(data, dict):
        profile["source"] = source
        profile["extracted_at"] = datetime.now(timezone.utc).isoformat()
        return profile

    profile["canonical_roles"] = normalize_string_list(data.get("canonical_roles"), max_items=None)
    profile["canonical_skills"] = normalize_string_list(data.get("canonical_skills"), max_items=40)
    profile["technologies_tools"] = normalize_string_list(data.get("technologies_tools"), max_items=30)
    profile["domain_keywords"] = normalize_string_list(data.get("domain_keywords"), max_items=20)
    profile["seniority_level"] = _normalize_seniority(data.get("seniority_level"))
    profile["years_of_experience"] = _normalize_years(data.get("years_of_experience"))
    profile["education"] = normalize_string_list(data.get("education"), max_items=10)
    profile["certifications"] = normalize_string_list(data.get("certifications"), max_items=15)
    profile["languages"] = normalize_string_list(data.get("languages"), max_items=10)
    profile["preferred_role_titles"] = normalize_string_list(
        data.get("preferred_role_titles"), max_items=None
    )
    profile["alternative_role_titles"] = normalize_string_list(
        data.get("alternative_role_titles"), max_items=None
    )
    profile["search_keywords_en"] = normalize_string_list(data.get("search_keywords_en"), max_items=20)
    profile["search_keywords_he"] = normalize_string_list(data.get("search_keywords_he"), max_items=15)
    profile["exclusion_keywords"] = normalize_string_list(data.get("exclusion_keywords"), max_items=15)

    location = data.get("location_preferences")
    if isinstance(location, dict):
        profile["location_preferences"] = {
            "preferred_locations": normalize_string_list(
                location.get("preferred_locations"), max_items=8
            ),
            "remote_ok": bool(location.get("remote_ok", True)),
        }

    raw_queries = data.get("collection_queries")
    if isinstance(raw_queries, list):
        profile["collection_queries"] = [
            q for q in raw_queries if isinstance(q, dict)
        ][:6]

    profile["candidate_summary"] = str(data.get("candidate_summary") or "").strip()
    profile["career_notes"] = str(data.get("career_notes") or "").strip()
    profile["extracted_at"] = datetime.now(timezone.utc).isoformat()
    profile["source"] = source

    profile["collection_queries"] = build_collection_queries(profile)
    return profile


def _build_hints(rule_based: dict[str, Any]) -> str:
    """Compact rule-based hints to reduce AI hallucination."""
    experience = rule_based.get("experience") or {}
    education = rule_based.get("education") or {}
    skills = flatten_skills(rule_based)

    lines = [
        f"Job titles (rule-based): {', '.join(experience.get('job_titles', [])[:6])}",
        f"Companies: {', '.join(experience.get('companies', [])[:4])}",
        f"Seniority (rule-based): {experience.get('seniority_level', 'unknown')}",
        f"Years (rule-based): {experience.get('years_of_experience_estimate', 'unknown')}",
        f"Degrees: {', '.join(education.get('degrees', [])[:3])}",
        f"Fields: {', '.join(education.get('fields_of_study', [])[:3])}",
        f"Certifications: {', '.join(rule_based.get('certifications', [])[:5])}",
        f"Skills ({len(skills)}): {', '.join(skills[:30])}",
        f"Suggested roles: {', '.join(rule_based.get('best_fit_roles', [])[:6])}",
    ]
    contact = rule_based.get("contact") or {}
    if contact.get("name"):
        lines.append(f"Candidate name: {contact['name']}")
    if contact.get("email"):
        lines.append(f"Email: {contact['email']}")
    if contact.get("phone"):
        lines.append(f"Phone: {contact['phone']}")
    if contact.get("location"):
        lines.append(f"Location: {contact['location']}")
    return "\n".join(lines)


def _build_user_prompt(raw_text: str, rule_based: dict[str, Any]) -> str:
    hints = _build_hints(rule_based)
    resume_text = truncate_text(raw_text, OPENAI_CV_MAX_CHARS)
    return (
        "Analyze this candidate and return the universal profile JSON.\n\n"
        f"--- RULE-BASED HINTS (use as ground truth, do not contradict) ---\n{hints}\n\n"
        f"--- RESUME TEXT ---\n{resume_text or '(no extractable text — use hints only)'}"
    )


def extract_universal_profile_with_openai(
    raw_text: str,
    rule_based: dict[str, Any],
) -> dict[str, Any]:
    """Single OpenAI call to extract universal candidate profile."""
    user_prompt = _build_user_prompt(raw_text, rule_based)
    cache_payload = f"universal_profile_v2_specific_queries\n{user_prompt[:OPENAI_CV_MAX_CHARS]}"

    raw = call_openai_json(
        UNIVERSAL_PROFILE_SYSTEM,
        user_prompt,
        cache_namespace="universal_profile",
        cache_payload=cache_payload,
    )
    return normalize_universal_profile(raw, source="openai")


def extract_universal_profile_vision(
    image_pages: list[bytes],
    rule_based: dict[str, Any],
) -> dict[str, Any]:
    """Extract universal profile from resume images (one OpenAI vision call)."""
    if not is_ai_available():
        raise RuntimeError("OPENAI_API_KEY is not set in .env")
    if not image_pages:
        raise ValueError("No resume images to analyze")

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    hints = _build_hints(rule_based)

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Read every page of this resume and return the universal profile JSON.\n\n"
                f"--- RULE-BASED HINTS ---\n{hints}"
            ),
        }
    ]
    for image_bytes in image_pages[:3]:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"},
            }
        )

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": VISION_SYSTEM},
            {"role": "user", "content": content},
        ],
    )

    text = response.choices[0].message.content or ""
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text.strip())
    if fenced:
        text = fenced.group(1).strip()
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("Vision response is not a JSON object")
    return normalize_universal_profile(raw, source="openai")


def build_universal_profile_fallback(rule_based: dict[str, Any]) -> dict[str, Any]:
    """Rule-based universal profile when OpenAI is unavailable."""
    experience = rule_based.get("experience") or {}
    education = rule_based.get("education") or {}
    skills = flatten_skills(rule_based)

    roles = list(rule_based.get("best_fit_roles") or [])
    job_titles = list(experience.get("job_titles") or [])
    canonical_roles = []
    seen: set[str] = set()
    for role in roles + job_titles:
        text = str(role).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            canonical_roles.append(text)

    education_items = []
    for key in ("degrees", "fields_of_study", "institutions"):
        for item in education.get(key) or []:
            if item and str(item) not in education_items:
                education_items.append(str(item))

    all_keywords = canonical_roles + skills[:15]
    keywords_en, keywords_he = split_keywords_by_script(all_keywords)

    seniority = _normalize_seniority(experience.get("seniority_level"))
    exclusion = list(SENIOR_KEYWORDS[:6]) if seniority in ("junior", "student", "intern") else []

    contact = rule_based.get("contact") or {}
    locations = [contact["location"]] if contact.get("location") else ["Israel"]

    data = {
        "canonical_roles": canonical_roles,
        "canonical_skills": skills[:30],
        "technologies_tools": [],
        "domain_keywords": [],
        "seniority_level": seniority,
        "years_of_experience": experience.get("years_of_experience_estimate"),
        "education": education_items,
        "certifications": list(rule_based.get("certifications") or [])[:10],
        "languages": list((rule_based.get("skills") or {}).get("languages") or [])[:5],
        "preferred_role_titles": canonical_roles,
        "alternative_role_titles": [],
        "search_keywords_en": keywords_en[:15],
        "search_keywords_he": keywords_he[:10],
        "exclusion_keywords": exclusion,
        "location_preferences": {
            "preferred_locations": locations,
            "remote_ok": True,
        },
        "candidate_summary": "",
        "career_notes": "Rule-based profile — OpenAI unavailable.",
    }
    return normalize_universal_profile(data, source="rules_fallback")


def extract_universal_profile(
    raw_text: str,
    rule_based: dict[str, Any],
    *,
    use_ai: bool = True,
) -> dict[str, Any]:
    """Extract universal profile — one AI call or rule-based fallback."""
    if not use_ai or not is_ai_available():
        return build_universal_profile_fallback(rule_based)

    try:
        return extract_universal_profile_with_openai(raw_text, rule_based)
    except (OpenAIAPIError, Exception):
        return build_universal_profile_fallback(rule_based)


def _dedupe_role_titles(*groups: list[Any], max_items: int | None = None) -> list[str]:
    """Merge role-title lists in priority order with case-insensitive dedupe.

    When ``max_items`` is ``None``, all unique titles are kept.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group or []:
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text)
            if max_items is not None and len(merged) >= max_items:
                return merged
    return merged


def apply_universal_profile_to_cv(cv_profile: dict[str, Any], universal: dict[str, Any]) -> dict[str, Any]:
    """Merge universal profile into cv_profile for backward compatibility.

    Preserves rule-based ``best_fit_roles`` and experience job titles alongside
    AI preferred/alternative titles so secondary career tracks are not dropped
    when the model focuses on the strongest track.
    """
    cv_profile = dict(cv_profile)
    cv_profile["universal_profile"] = universal

    preferred = universal.get("preferred_role_titles") or []
    alternative = universal.get("alternative_role_titles") or []
    rule_roles = list(cv_profile.get("best_fit_roles") or [])
    experience = dict(cv_profile.get("experience") or {})
    job_titles = list(experience.get("job_titles") or [])

    combined_roles = _dedupe_role_titles(
        preferred, alternative, rule_roles, job_titles, max_items=None
    )
    if combined_roles:
        cv_profile["best_fit_roles"] = combined_roles
        # Keep universal preferred list complete for downstream strategy building.
        universal = dict(universal)
        universal["preferred_role_titles"] = _dedupe_role_titles(
            preferred, rule_roles, job_titles, max_items=None
        )
        preferred_keys = {p.casefold() for p in universal["preferred_role_titles"]}
        universal["alternative_role_titles"] = _dedupe_role_titles(
            alternative,
            [r for r in combined_roles if r.casefold() not in preferred_keys],
            max_items=None,
        )
        cv_profile["universal_profile"] = universal

    if universal.get("seniority_level"):
        experience["seniority_level"] = universal["seniority_level"]
    if universal.get("years_of_experience") is not None:
        experience["years_of_experience_estimate"] = universal["years_of_experience"]
    cv_profile["experience"] = experience

    insights = dict(cv_profile.get("ai_insights") or {})
    if universal.get("candidate_summary"):
        insights["professional_summary"] = universal["candidate_summary"]
    if universal.get("career_notes"):
        insights["career_trajectory"] = universal["career_notes"]
    if combined_roles:
        insights["recommended_job_types"] = list(combined_roles)
    elif universal.get("preferred_role_titles"):
        insights["recommended_job_types"] = list(universal["preferred_role_titles"])
    if universal.get("canonical_skills"):
        insights["skills_to_highlight"] = list(universal["canonical_skills"])[:10]
    cv_profile["ai_insights"] = insights

    if universal.get("certifications"):
        existing = list(cv_profile.get("certifications") or [])
        for cert in universal["certifications"]:
            if cert not in existing:
                existing.append(cert)
        cv_profile["certifications"] = existing[:15]

    return cv_profile


def build_matching_strategy_from_profile(
    universal: dict[str, Any],
    profile: dict[str, Any] | None = None,
    cv_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ai_matching_strategy.json shape from universal profile (no AI)."""
    from role_analyzer import normalize_matching_strategy

    profile = profile or {}
    cv_profile = cv_profile or {}
    preferred = list(
        universal.get("preferred_role_titles") or universal.get("canonical_roles") or []
    )
    alternative = list(universal.get("alternative_role_titles") or [])
    rule_roles = [
        str(r).strip()
        for r in (cv_profile.get("best_fit_roles") or [])
        if str(r or "").strip()
    ]
    job_titles = [
        str(t).strip()
        for t in ((cv_profile.get("experience") or {}).get("job_titles") or [])
        if str(t or "").strip()
    ]
    preferred = _dedupe_role_titles(preferred, rule_roles, job_titles, max_items=None)
    preferred_keys = {p.casefold() for p in preferred}
    alternative = _dedupe_role_titles(
        alternative,
        [r for r in rule_roles + job_titles if r.casefold() not in preferred_keys],
        max_items=None,
    )

    best_fit_roles: list[dict[str, Any]] = []
    for index, role in enumerate(preferred):
        best_fit_roles.append({
            "role": str(role),
            "score": clamp_score(90 - min(index, 15) * 3),
            "reason": universal.get("candidate_summary") or "From universal candidate profile",
            "missing_skills": [],
            "realistic_for_application": True,
        })
    for index, role in enumerate(alternative):
        best_fit_roles.append({
            "role": str(role),
            "score": clamp_score(72 - min(index, 10) * 3),
            "reason": "Alternative / secondary career track from CV profile",
            "missing_skills": [],
            "realistic_for_application": True,
        })

    job_categories: list[dict[str, Any]] = []
    skills = universal.get("canonical_skills") or []
    domain_kw = universal.get("domain_keywords") or []
    tech_kw = universal.get("technologies_tools") or []
    search_en = [k.lower() for k in (universal.get("search_keywords_en") or [])]
    search_he = list(universal.get("search_keywords_he") or [])

    # One category per primary track; include adjacent titles for domain chips.
    category_roles = preferred or alternative
    for index, role in enumerate(category_roles):
        role_l = str(role).lower()
        must_have = list({role_l, *search_en[:6], *search_he[:4], *[s.lower() for s in skills[:5]]})
        nice_to_have = [s.lower() for s in (tech_kw + domain_kw)[:8]]
        sibling = [
            t for t in (preferred + alternative + search_he)
            if str(t).strip() and str(t).casefold() != role.casefold()
        ][:6]
        job_categories.append({
            "category": role_l.replace(" ", "_")[:40] or f"category_{index}",
            "titles": _dedupe_role_titles([role], sibling, search_he[:3], max_items=None),
            "must_have_keywords": must_have[:12],
            "nice_to_have_keywords": nice_to_have,
            "negative_keywords": list(universal.get("exclusion_keywords") or SENIOR_KEYWORDS[:6]),
            "score_weight": 1.0,
        })

    if not job_categories and preferred:
        role = str(preferred[0])
        job_categories.append({
            "category": role.lower().replace(" ", "_")[:40],
            "titles": [role.lower(), role],
            "must_have_keywords": search_en[:8] + search_he[:4],
            "nice_to_have_keywords": [s.lower() for s in skills[:6]],
            "negative_keywords": list(universal.get("exclusion_keywords") or []),
            "score_weight": 1.0,
        })

    # Ensure collection_queries see the enriched preferred list.
    enriched_universal = dict(universal)
    enriched_universal["preferred_role_titles"] = preferred
    enriched_universal["alternative_role_titles"] = alternative
    collection_queries = build_collection_queries(enriched_universal)
    location_prefs = universal.get("location_preferences") or {}
    seniority = universal.get("seniority_level") or "unknown"

    seniority_filters: dict[str, Any] = {
        "reject_keywords": list(universal.get("exclusion_keywords") or SENIOR_KEYWORDS[:8]),
        "prefer_keywords": [],
        "max_years_required_if_no_experience": 2,
    }
    if seniority in ("junior", "student", "intern"):
        seniority_filters["prefer_keywords"] = ["junior", "entry", "graduate", "student", "ג'וניור"]

    locations = list(location_prefs.get("preferred_locations") or [])
    if not locations and profile.get("location"):
        locations = [profile["location"]]

    raw_strategy = {
        "source": universal.get("source", "universal_profile"),
        "candidate_summary": universal.get("candidate_summary") or "",
        "career_notes": universal.get("career_notes") or "",
        "best_fit_roles": best_fit_roles,
        "job_categories": job_categories,
        "collection_queries": collection_queries,
        "global_reject_rules": [
            "Reject jobs with strong exclusion keyword matches",
            "Reject roles with no overlap to candidate skills or titles",
        ],
        "seniority_filters": seniority_filters,
        "location_preferences": {
            "preferred_locations": locations or ["Israel"],
            "remote_ok": bool(location_prefs.get("remote_ok", profile.get("remote", True))),
            "remote_keywords": ["remote", "hybrid", "מהבית", "עבודה מהבית"],
        },
        "application_priority_rules": [
            "Prioritize preferred_role_titles with highest skill overlap",
            "Skip roles matching exclusion_keywords",
        ],
    }

    return normalize_matching_strategy(
        raw_strategy,
        universal.get("candidate_summary") or "",
    )


def get_universal_profile(cv_profile: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return universal_profile from cv_profile if present."""
    if not isinstance(cv_profile, dict):
        return None
    universal = cv_profile.get("universal_profile")
    return universal if isinstance(universal, dict) else None
