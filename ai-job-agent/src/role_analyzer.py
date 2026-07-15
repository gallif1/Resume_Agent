"""OpenAI-powered career role analysis and reusable matching strategy generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ai_client import (
    OpenAIAPIError,
    call_openai_json,
    clamp_score,
    normalize_string_list,
    truncate_text,
)
from candidate_summary import build_candidate_summary, flatten_skills
from config import (
    AI_MATCHING_STRATEGY_PATH,
    AI_ROLES_PATH,
    OPENAI_CV_SUMMARY_MAX_CHARS,
    OPENAI_MAX_COLLECTION_ROLES,
)
from rule_based_matcher import ROLE_EXTRA_KEYWORDS, SENIOR_KEYWORDS

MATCHING_STRATEGY_SYSTEM = """You are an expert technical recruiter and career advisor.
Analyze the candidate and produce a reusable job-matching strategy for local classification.

Return ONE JSON object with this exact structure:
{
  "candidate_summary": "2-3 sentence summary of the candidate",
  "best_fit_roles": [
    {
      "role": "Junior Backend Developer",
      "score": 90,
      "reason": "1-2 sentences",
      "missing_skills": ["Docker"],
      "realistic_for_application": true
    }
  ],
  "career_notes": "2-3 sentences on career direction",
  "job_categories": [
    {
      "category": "backend",
      "titles": ["backend developer", "python developer", "server developer", "מפתח backend", "מפתח תוכנה"],
      "must_have_keywords": ["python", "api", "sql", "backend", "צד שרת", "מפתח"],
      "nice_to_have_keywords": ["fastapi", "aws", "docker"],
      "negative_keywords": ["senior", "lead", "principal", "manager", "בכיר", "ראש צוות"],
      "score_weight": 1.0
    }
  ],
  "collection_queries": [
    {
      "category": "backend",
      "priority": 95,
      "primary_role": "Junior Backend Developer",
      "search_queries": ["Junior Backend Developer", "Backend Developer", "Python Developer", "FastAPI Developer", "Junior Software Developer"],
      "hebrew_search_queries": ["מפתח Backend", "מפתח Python", "מפתח תוכנה ג׳וניור"],
      "alternative_titles": ["Software Developer", "Server Developer"],
      "exclude_keywords": ["senior", "lead", "manager", "ראש צוות", "בכיר"]
    }
  ],
  "global_reject_rules": [
    "Reject jobs requiring 3+ years if candidate has no direct experience",
    "Reject senior/lead/manager roles",
    "Reject unrelated sales/customer success jobs unless technical"
  ],
  "seniority_filters": {
    "reject_keywords": ["senior", "lead", "principal", "manager", "director", "head of"],
    "prefer_keywords": ["junior", "entry", "graduate", "student"],
    "max_years_required_if_no_experience": 2
  },
  "skill_weights": {
    "must_have_match": 8,
    "nice_to_have_match": 3,
    "missing_must_have_penalty": 5,
    "negative_keyword_penalty": 15
  },
  "location_preferences": {
    "preferred_locations": ["Israel", "Tel Aviv"],
    "remote_ok": true,
    "remote_keywords": ["remote", "hybrid", "work from home"],
    "location_bonus": 10,
    "remote_bonus": 8
  },
  "application_priority_rules": [
    "Prioritize roles matching best_fit_roles with realistic_for_application=true",
    "Deprioritize roles with many missing must-have skills",
    "Skip roles with strong negative keyword matches"
  ]
}

Rules:
- Return 4-8 best_fit_roles ranked by fit.
- Return 3-6 job_categories covering the candidate's realistic job search.
- Return one collection_queries entry per job_category (use the same category name).
- collection_queries drive the actual job-board search, so each entry MUST contain
  concrete, ready-to-search job titles derived from the CV — NOT abstract skills.
- search_queries: 4-8 English job titles a recruiter would post for this candidate.
  Prefer distinctive role+skill / role+domain titles from THIS CV
  (e.g. "Python Backend Developer", not only generic "Software Engineer").
- hebrew_search_queries: 2-5 Hebrew equivalents of those titles (Israel job market),
  including mixed Hebrew+technology phrases when useful (e.g. "מפתח Python").
- alternative_titles: related/adjacent titles worth searching.
- Avoid emitting only broad titles that return the same top board results for every CV.
- exclude_keywords: words that should disqualify a result (seniority, unrelated roles),
  in both English and Hebrew.
- priority: 0-100 indicating how strongly the candidate fits that category.
- Keywords should be lowercase. IMPORTANT: many job postings are written in Hebrew,
  so titles, must_have_keywords, and negative_keywords MUST include Hebrew
  equivalents for role/domain words (e.g. "תמיכה טכנית" for "technical support",
  "רשתות" for "networking", "מפתח" for "developer"). Technology names (python,
  sql, aws) stay in English.
- Base analysis ONLY on provided candidate data.
- Consider Israel job market when relevant.
- Return valid JSON only."""


ROLE_ANALYSIS_SYSTEM = MATCHING_STRATEGY_SYSTEM


def _empty_ai_roles() -> dict[str, Any]:
    return {
        "analyzed_at": None,
        "source": "none",
        "candidate_summary": "",
        "career_notes": "",
        "best_fit_roles": [],
    }


def _empty_matching_strategy() -> dict[str, Any]:
    return {
        "analyzed_at": None,
        "source": "none",
        "candidate_summary": "",
        "career_notes": "",
        "best_fit_roles": [],
        "job_categories": [],
        "collection_queries": [],
        "global_reject_rules": [],
        "seniority_filters": {},
        "skill_weights": {},
        "location_preferences": {},
        "application_priority_rules": [],
    }


def normalize_role_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    role = str(entry.get("role", "") or "").strip()
    if not role:
        return None
    return {
        "role": role,
        "score": clamp_score(entry.get("score")),
        "reason": str(entry.get("reason", "") or "").strip(),
        "missing_skills": normalize_string_list(entry.get("missing_skills", []), max_items=8),
        "realistic_for_application": bool(entry.get("realistic_for_application", True)),
    }


def normalize_category_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    category = str(entry.get("category", "") or "").strip()
    if not category:
        return None
    return {
        "category": category,
        "titles": normalize_string_list(entry.get("titles", []), max_items=12),
        "must_have_keywords": normalize_string_list(entry.get("must_have_keywords", []), max_items=20),
        "nice_to_have_keywords": normalize_string_list(entry.get("nice_to_have_keywords", []), max_items=20),
        "negative_keywords": normalize_string_list(entry.get("negative_keywords", []), max_items=15),
        "score_weight": float(entry.get("score_weight", 1.0) or 1.0),
    }


def _dedupe_queries(queries: list[str], max_items: int = 16) -> list[str]:
    """Case-insensitive de-duplication that preserves order and original casing."""
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


def normalize_collection_query_entry(entry: Any, index: int = 0) -> dict[str, Any] | None:
    """Normalize one collection_queries entry into a board-ready search plan.

    Preserves English / Hebrew / mixed buckets so LinkedIn/GotFriends can search
    in English while Drushim stays bilingual.
    """
    if not isinstance(entry, dict):
        return None

    from query_builder import (
        is_english_query,
        is_hebrew_query,
        normalize_collection_entry,
    )

    # Reuse the richer normalizer when language buckets (or recover-able fields) exist.
    rich = normalize_collection_entry(entry, profile={})
    if rich.get("queries") or rich.get("queries_en") or rich.get("queries_he"):
        try:
            priority = int(round(float(entry.get("priority", rich.get("priority", 50)))))
        except (TypeError, ValueError):
            priority = int(rich.get("priority", 50) or 50)
        rich["priority"] = max(0, min(100, priority))
        if not rich.get("category"):
            rich["category"] = f"category_{index}"
        # Keep a non-Hebrew primary_role when possible for English boards.
        primary = str(rich.get("primary_role") or "").strip()
        if primary and not is_english_query(primary):
            en = rich.get("queries_en") or []
            if en:
                rich["primary_role"] = en[0]
        if not rich.get("queries") and not rich.get("queries_en") and not rich.get("queries_he"):
            return None
        return rich

    primary_role = str(entry.get("primary_role", "") or "").strip()
    category = str(entry.get("category", "") or "").strip()
    if not category and primary_role:
        category = primary_role.lower().replace(" ", "_")[:40]
    if not category:
        category = f"category_{index}"

    queries_en: list[str] = []
    queries_he: list[str] = []
    queries_mixed: list[str] = []
    if primary_role:
        if is_english_query(primary_role):
            queries_en.append(primary_role)
        elif is_hebrew_query(primary_role):
            queries_he.append(primary_role)

    for key in ("queries", "search_queries", "hebrew_search_queries", "queries_mixed", "alternative_titles"):
        value = entry.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            text = str(item or "").strip()
            if not text:
                continue
            if is_hebrew_query(text) and any(ch.isascii() and ch.isalpha() for ch in text):
                queries_mixed.append(text)
            elif is_hebrew_query(text):
                queries_he.append(text)
            else:
                queries_en.append(text)

    queries_en = _dedupe_queries(queries_en, max_items=8)
    queries_he = _dedupe_queries(queries_he, max_items=5)
    queries_mixed = _dedupe_queries(queries_mixed, max_items=4)
    queries = _dedupe_queries(queries_en + queries_mixed + queries_he, max_items=16)
    if not queries:
        return None

    try:
        priority = int(round(float(entry.get("priority", 50))))
    except (TypeError, ValueError):
        priority = 50
    priority = max(0, min(100, priority))

    english_primary = primary_role if is_english_query(primary_role) else (
        queries_en[0] if queries_en else primary_role or queries[0]
    )

    return {
        "category": category,
        "priority": priority,
        "primary_role": english_primary,
        "queries_en": queries_en,
        "queries_he": queries_he,
        "queries_mixed": queries_mixed,
        "search_queries": queries_en,
        "hebrew_search_queries": queries_he,
        "queries": queries,
        "exclude_keywords": normalize_string_list(entry.get("exclude_keywords", []), max_items=15),
    }


def _collection_queries_from_categories(
    categories: list[dict[str, Any]],
    roles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive collection queries from job_categories/best_fit_roles when the
    model did not return a dedicated collection_queries block."""
    role_scores = {r.get("role", "").lower(): r.get("score", 50) for r in roles}
    plan: list[dict[str, Any]] = []

    from query_builder import is_english_query, is_hebrew_query

    for index, category in enumerate(categories):
        titles = list(category.get("titles", []))
        queries = _dedupe_queries(titles, max_items=16)
        if not queries:
            continue
        queries_en = [q for q in queries if is_english_query(q)]
        queries_he = [q for q in queries if is_hebrew_query(q) and q not in queries_en]
        priority = 0
        for title in titles:
            priority = max(priority, int(role_scores.get(title.lower(), 0) or 0))
        if not priority:
            priority = max(40, 90 - index * 10)
        primary = queries_en[0] if queries_en else queries[0]
        plan.append({
            "category": category.get("category") or f"category_{index}",
            "priority": priority,
            "primary_role": primary,
            "queries_en": queries_en,
            "queries_he": queries_he,
            "queries_mixed": [],
            "search_queries": queries_en,
            "hebrew_search_queries": queries_he,
            "queries": _dedupe_queries(queries_en + queries_he, max_items=16),
            "exclude_keywords": normalize_string_list(
                category.get("negative_keywords", []), max_items=15
            ),
        })

    if plan:
        return plan

    for index, role_entry in enumerate(roles):
        role = str(role_entry.get("role", "") or "").strip()
        if not role:
            continue
        en = [role] if is_english_query(role) else []
        he = [role] if is_hebrew_query(role) and not en else []
        plan.append({
            "category": role.lower().replace(" ", "_")[:40] or f"role_{index}",
            "priority": int(role_entry.get("score", 50) or 50),
            "primary_role": role if en else (en[0] if en else role),
            "queries_en": en,
            "queries_he": he,
            "queries_mixed": [],
            "search_queries": en,
            "hebrew_search_queries": he,
            "queries": [role],
            "exclude_keywords": list(SENIOR_KEYWORDS[:6]),
        })
    return plan


def normalize_collection_queries(
    data: dict[str, Any],
    categories: list[dict[str, Any]],
    roles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for index, entry in enumerate(data.get("collection_queries", []) or []):
        normalized = normalize_collection_query_entry(entry, index)
        if normalized:
            plan.append(normalized)

    if not plan:
        plan = _collection_queries_from_categories(categories, roles)

    plan.sort(key=lambda item: item["priority"], reverse=True)
    return plan[:10]


def normalize_matching_strategy(
    data: dict[str, Any],
    candidate_summary: str = "",
) -> dict[str, Any]:
    roles: list[dict[str, Any]] = []
    for entry in data.get("best_fit_roles", []):
        normalized = normalize_role_entry(entry)
        if normalized:
            roles.append(normalized)
    roles.sort(key=lambda item: item["score"], reverse=True)

    categories: list[dict[str, Any]] = []
    for entry in data.get("job_categories", []):
        normalized = normalize_category_entry(entry)
        if normalized:
            categories.append(normalized)

    seniority = data.get("seniority_filters", {})
    if not isinstance(seniority, dict):
        seniority = {}

    skill_weights = data.get("skill_weights", {})
    if not isinstance(skill_weights, dict):
        skill_weights = {}

    location_prefs = data.get("location_preferences", {})
    if not isinstance(location_prefs, dict):
        location_prefs = {}

    capped_categories = categories[:8]
    capped_roles = roles[:10]

    return {
        "analyzed_at": data.get("analyzed_at") or datetime.now(timezone.utc).isoformat(),
        "source": data.get("source", "openai"),
        "candidate_summary": candidate_summary or str(data.get("candidate_summary", "") or ""),
        "career_notes": str(data.get("career_notes", "") or "").strip(),
        "best_fit_roles": capped_roles,
        "job_categories": capped_categories,
        "collection_queries": normalize_collection_queries(data, capped_categories, capped_roles),
        "global_reject_rules": normalize_string_list(data.get("global_reject_rules", []), max_items=12),
        "seniority_filters": {
            "reject_keywords": normalize_string_list(
                seniority.get("reject_keywords", SENIOR_KEYWORDS), max_items=15
            ),
            "prefer_keywords": normalize_string_list(seniority.get("prefer_keywords", []), max_items=10),
            "max_years_required_if_no_experience": int(
                seniority.get("max_years_required_if_no_experience", 2) or 2
            ),
        },
        "skill_weights": {
            "must_have_match": int(skill_weights.get("must_have_match", 8) or 8),
            "nice_to_have_match": int(skill_weights.get("nice_to_have_match", 3) or 3),
            "missing_must_have_penalty": int(skill_weights.get("missing_must_have_penalty", 5) or 5),
            "negative_keyword_penalty": int(skill_weights.get("negative_keyword_penalty", 15) or 15),
        },
        "location_preferences": {
            "preferred_locations": normalize_string_list(
                location_prefs.get("preferred_locations", []), max_items=8
            ),
            "remote_ok": bool(location_prefs.get("remote_ok", True)),
            "remote_keywords": normalize_string_list(
                location_prefs.get("remote_keywords", ["remote", "hybrid"]), max_items=10
            ),
            "location_bonus": int(location_prefs.get("location_bonus", 10) or 10),
            "remote_bonus": int(location_prefs.get("remote_bonus", 8) or 8),
        },
        "application_priority_rules": normalize_string_list(
            data.get("application_priority_rules", []), max_items=10
        ),
    }


def normalize_ai_roles(data: dict[str, Any], candidate_summary: str = "") -> dict[str, Any]:
    strategy = normalize_matching_strategy(data, candidate_summary)
    return {
        "analyzed_at": strategy["analyzed_at"],
        "source": strategy["source"],
        "candidate_summary": strategy["candidate_summary"],
        "career_notes": strategy["career_notes"],
        "best_fit_roles": strategy["best_fit_roles"],
    }


def _unique_role_keywords(keywords: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for keyword in keywords:
        key = keyword.lower().replace("-", "").replace(" ", "")
        if not key:
            continue
        if any(key in existing or existing in key for existing in seen):
            continue
        seen.add(key)
        result.append(keyword.lower())
    return result[:5]


def _category_from_role(role: str, index: int) -> dict[str, Any]:
    role_l = role.lower()
    keywords: list[str] = []
    for key, extras in ROLE_EXTRA_KEYWORDS.items():
        if key in role_l:
            keywords = list(extras)
            break
    if not keywords:
        keywords = [word for word in role_l.split() if len(word) > 2]

    must_have = _unique_role_keywords(keywords) or [role_l]

    return {
        "category": role_l.replace(" ", "_").replace("(", "").replace(")", "")[:40] or f"role_{index}",
        "titles": [role_l],
        "must_have_keywords": must_have,
        "nice_to_have_keywords": [],
        "negative_keywords": list(SENIOR_KEYWORDS[:6]),
        "score_weight": 1.0,
    }


def fallback_matching_strategy(profile: dict[str, Any], cv_profile: dict[str, Any]) -> dict[str, Any]:
    """Build matching strategy from profile + CV when OpenAI is unavailable."""
    candidate_summary = build_candidate_summary(profile, cv_profile)
    seen_roles: set[str] = set()
    roles: list[dict[str, Any]] = []
    categories: list[dict[str, Any]] = []

    def add_role(role: str, score: int, reason: str) -> None:
        key = role.lower()
        if key in seen_roles:
            return
        seen_roles.add(key)
        roles.append({
            "role": role,
            "score": clamp_score(score),
            "reason": reason,
            "missing_skills": [],
            "realistic_for_application": True,
        })
        categories.append(_category_from_role(role, len(categories)))

    for index, role in enumerate(cv_profile.get("best_fit_roles", [])[:6]):
        add_role(str(role), 85 - index * 5, "Suggested from CV analysis (rule-based fallback)")

    insights = cv_profile.get("ai_insights", {})
    if isinstance(insights, dict):
        for index, role in enumerate(insights.get("recommended_job_types", [])[:4]):
            add_role(str(role), 75 - index * 5, "Recommended job type from CV insights (fallback)")

    for index, role in enumerate(profile.get("target_roles", [])):
        add_role(str(role), 70 - index * 3, "Listed in profile.json target_roles (fallback)")

    roles.sort(key=lambda item: item["score"], reverse=True)

    return normalize_matching_strategy({
        "source": "fallback",
        "career_notes": "OpenAI unavailable — using profile and CV role suggestions.",
        "best_fit_roles": roles,
        "job_categories": categories[:8],
        "global_reject_rules": [
            "Reject senior/lead/manager roles",
            "Reject roles with no overlap to target roles or skills",
        ],
        "seniority_filters": {
            "reject_keywords": SENIOR_KEYWORDS,
            "prefer_keywords": ["junior", "entry", "graduate", "student"],
            "max_years_required_if_no_experience": 2,
        },
        "location_preferences": {
            "preferred_locations": [profile.get("location", "Israel")],
            "remote_ok": bool(profile.get("remote", True)),
        },
        "application_priority_rules": [
            "Prioritize roles from profile target_roles",
            "Skip senior titles",
        ],
    }, candidate_summary)


def fallback_role_analysis(profile: dict[str, Any], cv_profile: dict[str, Any]) -> dict[str, Any]:
    return normalize_ai_roles(fallback_matching_strategy(profile, cv_profile))


def _build_analysis_prompt(profile: dict[str, Any], cv_profile: dict[str, Any]) -> str:
    candidate_summary = build_candidate_summary(profile, cv_profile)
    skills = flatten_skills(cv_profile)
    experience = cv_profile.get("experience", {}) if isinstance(cv_profile.get("experience"), dict) else {}
    education = cv_profile.get("education", {}) if isinstance(cv_profile.get("education"), dict) else {}
    projects = cv_profile.get("projects", [])
    project_blob = ""
    if isinstance(projects, list):
        project_blob = "\n".join(f"- {p}" for p in projects[:5])

    contact = cv_profile.get("contact", {}) if isinstance(cv_profile.get("contact"), dict) else {}
    universal = cv_profile.get("universal_profile", {}) if isinstance(cv_profile.get("universal_profile"), dict) else {}
    location_prefs = universal.get("location_preferences", {}) if isinstance(universal.get("location_preferences"), dict) else {}
    preferred_locations = location_prefs.get("preferred_locations") or []
    location = (
        str(contact.get("location") or "").strip()
        or (str(preferred_locations[0]).strip() if preferred_locations else "")
        or str(profile.get("location") or "").strip()
    )
    remote_ok = location_prefs.get("remote_ok", profile.get("remote", False))
    target_roles = profile.get("target_roles") or cv_profile.get("best_fit_roles") or []

    return f"""Analyze this candidate and return the JSON object described.

--- CANDIDATE SUMMARY ---
{candidate_summary}

--- CV-DERIVED PREFERENCES ---
Location: {location}
Remote OK: {remote_ok}
Target roles: {', '.join(target_roles[:6])}

--- EXPERIENCE DETAIL ---
Titles: {', '.join(experience.get('job_titles', []))}
Companies: {', '.join(experience.get('companies', []))}
Seniority: {experience.get('seniority_level', 'unknown')}
Years: {experience.get('years_of_experience_estimate', 'unknown')}

--- EDUCATION ---
Degrees: {', '.join(education.get('degrees', []))}
Fields: {', '.join(education.get('fields_of_study', []))}
Institutions: {', '.join(education.get('institutions', []))}

--- SKILLS ({len(skills)} total) ---
{', '.join(skills)}

--- PROJECTS ---
{truncate_text(project_blob, 1200) or '(none listed)'}
"""


def analyze_matching_strategy_with_openai(
    profile: dict[str, Any],
    cv_profile: dict[str, Any],
) -> dict[str, Any]:
    candidate_summary = build_candidate_summary(profile, cv_profile)
    user_prompt = _build_analysis_prompt(profile, cv_profile)

    cache_payload = (
        f"matching_strategy_v4_specific_queries\n"
        f"{user_prompt[:OPENAI_CV_SUMMARY_MAX_CHARS]}"
    )
    raw = call_openai_json(
        MATCHING_STRATEGY_SYSTEM,
        user_prompt,
        cache_namespace="matching_strategy",
        cache_payload=cache_payload,
    )
    raw["source"] = "openai"
    return normalize_matching_strategy(raw, candidate_summary)


def analyze_roles_with_openai(profile: dict[str, Any], cv_profile: dict[str, Any]) -> dict[str, Any]:
    return normalize_ai_roles(analyze_matching_strategy_with_openai(profile, cv_profile))


def analyze_roles(profile: dict[str, Any], cv_profile: dict[str, Any]) -> dict[str, Any]:
    """Run AI role analysis. Raises OpenAIAPIError if the API is unavailable."""
    return analyze_roles_with_openai(profile, cv_profile)


def analyze_matching_strategy(profile: dict[str, Any], cv_profile: dict[str, Any]) -> dict[str, Any]:
    """Generate full reusable matching strategy. Raises OpenAIAPIError if unavailable."""
    return analyze_matching_strategy_with_openai(profile, cv_profile)


def save_ai_roles(data: dict[str, Any], path=AI_ROLES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_matching_strategy(data: dict[str, Any], path=AI_MATCHING_STRATEGY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_ai_roles(path=AI_ROLES_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("best_fit_roles"):
            return None
        return normalize_ai_roles(data, str(data.get("candidate_summary", "") or ""))
    except (json.JSONDecodeError, OSError):
        return None


def _strategy_from_roles(roles_data: dict[str, Any]) -> dict[str, Any]:
    """Build a usable strategy from role-only data (legacy ai_roles.json)."""
    normalized = normalize_matching_strategy(
        roles_data, str(roles_data.get("candidate_summary", "") or "")
    )
    if normalized.get("job_categories"):
        return normalized

    categories = []
    for index, role_entry in enumerate(normalized.get("best_fit_roles", [])):
        role = role_entry.get("role", "")
        if role:
            categories.append(_category_from_role(role, index))

    normalized["job_categories"] = categories
    normalized["collection_queries"] = _collection_queries_from_categories(
        categories, normalized.get("best_fit_roles", [])
    )
    if not normalized.get("global_reject_rules"):
        normalized["global_reject_rules"] = [
            "Reject senior/lead/manager roles",
            "Reject roles with no overlap to target roles or skills",
        ]
    return normalized


def load_matching_strategy(path=AI_MATCHING_STRATEGY_PATH) -> dict[str, Any] | None:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("best_fit_roles"):
                normalized = normalize_matching_strategy(
                    data, str(data.get("candidate_summary", "") or "")
                )
                if not normalized.get("job_categories"):
                    return _strategy_from_roles(normalized)
                return normalized
        except (json.JSONDecodeError, OSError):
            pass

    roles = load_ai_roles()
    if roles:
        return _strategy_from_roles(roles)

    return None


def get_collection_roles(
    ai_roles: dict[str, Any] | None,
    profile: dict[str, Any],
    *,
    max_roles: int | None = None,
) -> list[str]:
    """Roles to search on Drushim — AI-ranked, preferring realistic fits."""
    limit = max_roles or OPENAI_MAX_COLLECTION_ROLES
    if ai_roles and ai_roles.get("best_fit_roles"):
        ranked = ai_roles["best_fit_roles"]
        realistic = [r for r in ranked if r.get("realistic_for_application", True)]
        pool = realistic or ranked
        roles = [r["role"] for r in pool[:limit] if r.get("role")]
        if roles:
            return roles

    return list(profile.get("target_roles", []))[:limit]


def get_collection_query_plan(strategy: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the AI-driven collection plan (one entry per category with queries).

    Each entry contains: category, priority, primary_role, queries, exclude_keywords.
    Falls back to deriving queries from job_categories/best_fit_roles when the
    strategy has no dedicated collection_queries block.
    """
    if not strategy:
        return []

    plan = strategy.get("collection_queries")
    if plan:
        normalized: list[dict[str, Any]] = []
        for index, entry in enumerate(plan):
            item = normalize_collection_query_entry(entry, index)
            if item:
                normalized.append(item)
        if normalized:
            normalized.sort(key=lambda item: item["priority"], reverse=True)
            return normalized

    return _collection_queries_from_categories(
        strategy.get("job_categories", []),
        strategy.get("best_fit_roles", []),
    )


def collection_plan_from_roles(roles: list[str]) -> list[dict[str, Any]]:
    """Build a minimal collection plan from plain role-title strings (last-resort)."""
    from query_builder import is_english_query, is_hebrew_query

    plan: list[dict[str, Any]] = []
    for index, role in enumerate(roles):
        role = str(role or "").strip()
        if not role:
            continue
        en = [role] if is_english_query(role) else []
        he = [role] if is_hebrew_query(role) and not en else []
        plan.append({
            "category": role.lower().replace(" ", "_")[:40] or f"role_{index}",
            "priority": max(40, 90 - index * 10),
            "primary_role": role if en else (en[0] if en else role),
            "queries_en": en,
            "queries_he": he,
            "queries_mixed": [],
            "search_queries": en,
            "hebrew_search_queries": he,
            "queries": [role],
            "exclude_keywords": list(SENIOR_KEYWORDS[:6]),
        })
    return plan
