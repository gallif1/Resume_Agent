"""Local job classification using AI-generated matching strategy — no OpenAI calls."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ai_client import VALID_ACTIONS, VALID_DECISIONS, clamp_score
from role_analyzer import fallback_matching_strategy, load_matching_strategy

# Bump when the scoring algorithm changes, so already-matched jobs are re-scored.
CLASSIFIER_VERSION = "v2"

# Weight of the AI-strategy score in the final blend (the rest comes from the
# profile/CV rule-based score).
STRATEGY_BLEND_WEIGHT = 0.6

# Hebrew equivalents for common strategy keywords, so Hebrew-language postings
# (Drushim) are scored the same as English ones. Technology names (python, sql)
# appear in English even in Hebrew ads, so they need no synonyms.
KEYWORD_SYNONYMS: dict[str, list[str]] = {
    "backend": ["צד שרת"],
    "developer": ["מפתח", "מפתחת", "תוכניתן", "תוכניתנית"],
    "software": ["תוכנה"],
    "engineer": ["מהנדס", "מהנדסת"],
    "support": ["תמיכה"],
    "technical": ["טכני", "טכנית", "טכנאי", "טכנאית"],
    "specialist": ["מומחה", "מומחית"],
    "technical support": ["תמיכה טכנית"],
    "helpdesk": ["תמיכה טכנית"],
    "help desk": ["תמיכה טכנית"],
    "troubleshooting": ["פתרון תקלות", "טיפול בתקלות", "אבחון תקלות", "תקלות"],
    "networking": ["רשתות", "תקשורת נתונים"],
    "network": ["רשת", "רשתות"],
    "cybersecurity": ["סייבר", "אבטחת מידע"],
    "security": ["אבטחה", "אבטחת מידע", "סייבר"],
    "monitoring": ["ניטור", "בקרה"],
    "incident response": ["תגובה לאירועים", "טיפול באירועים"],
    "analyst": ["אנליסט", "אנליסטית"],
    "cloud": ["ענן"],
    "database": ["בסיס נתונים", "בסיסי נתונים"],
    "junior": ["ג'וניור", "ג׳וניור", "זוטר", "זוטרה", "ללא ניסיון"],
    "entry": ["ללא ניסיון", "כניסה לתחום"],
    "entry level": ["ללא ניסיון", "כניסה לתחום"],
    "graduate": ["בוגר", "בוגרת"],
    "student": ["סטודנט", "סטודנטית"],
    "senior": ["sr", "בכיר", "בכירה"],
    "manager": ["מנהל", "מנהלת"],
    "lead": ["leader", "team lead", "ראש צוות", "מוביל", "מובילה"],
    "principal": [],
    "director": ["סמנכ\"ל"],
    "remote": ["מהבית", "עבודה מהבית", "עבודה מרחוק"],
    "hybrid": ["היברידי", "היברידית", "היברידיות"],
    "work from home": ["עבודה מהבית", "מהבית"],
    "israel": ["ישראל"],
    "tel aviv": ["תל אביב"],
}

_HEBREW_CHARS_RE = re.compile(r"[א-ת]")
# Common single-letter Hebrew prefixes (ו, ב, ל, כ, ש, מ, ה) attached to words.
_HEBREW_PREFIXES = "ובלכשמה"


@dataclass
class ClassificationResult:
    match_score: int
    category: str
    decision: str
    recommended_action: str
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    rejection_reason: str | None = None
    match_reason: str = ""
    fallback_score: int | None = None

    def to_db_fields(self, *, strategy_hash: str = "") -> dict[str, Any]:
        import json

        reason_parts = [
            f"[local] {self.decision} ({self.match_score}) -> {self.recommended_action}",
            f"category: {self.category}",
        ]
        if self.matched_keywords:
            reason_parts.append(f"matched: {', '.join(self.matched_keywords[:8])}")
        if self.missing_keywords:
            reason_parts.append(f"missing: {', '.join(self.missing_keywords[:6])}")
        if self.rejection_reason:
            reason_parts.append(f"rejected: {self.rejection_reason}")

        return {
            "match_score": self.match_score,
            "match_reason": "; ".join(reason_parts),
            "match_method": "local",
            "ai_decision": self.decision,
            "ai_strengths": json.dumps(self.matched_keywords, ensure_ascii=False),
            "ai_missing_skills": json.dumps(self.missing_keywords, ensure_ascii=False),
            "ai_recommended_action": self.recommended_action,
            "ai_explanation": self.match_reason,
            "fallback_score": self.fallback_score,
            "match_category": self.category,
            "matched_keywords": json.dumps(self.matched_keywords, ensure_ascii=False),
            "missing_keywords": json.dumps(self.missing_keywords, ensure_ascii=False),
            "rejection_reason": self.rejection_reason,
            "candidate_strategy_hash": strategy_hash,
        }


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def _job_text(job: dict[str, Any]) -> str:
    title = job.get("title") or ""
    short = job.get("description") or ""
    full = job.get("full_description") or ""
    return f"{title}\n{short}\n{full}"


def _match_single_keyword(text: str, keyword_l: str) -> bool:
    if " " in keyword_l:
        return keyword_l in text
    if _HEBREW_CHARS_RE.search(keyword_l):
        # Allow a single attached Hebrew prefix letter (e.g. "למנהל", "ומפתח").
        pattern = rf"(?<![א-ת])[{_HEBREW_PREFIXES}]?{re.escape(keyword_l)}(?![א-ת])"
        return bool(re.search(pattern, text))
    return bool(re.search(rf"\b{re.escape(keyword_l)}\b", text))


def _contains_keyword(text: str, keyword: str) -> bool:
    keyword_l = _normalize(keyword)
    if not keyword_l:
        return False
    if _match_single_keyword(text, keyword_l):
        return True
    for synonym in KEYWORD_SYNONYMS.get(keyword_l, []):
        if _match_single_keyword(text, _normalize(synonym)):
            return True
    return False


def _title_category_score(title_l: str, category: dict[str, Any]) -> int:
    titles = category.get("titles", [])
    if not titles:
        return 0

    best = 0
    for title_pattern in titles:
        pattern_l = _normalize(title_pattern)
        if not pattern_l:
            continue
        if pattern_l in title_l:
            best = max(best, 30)
            continue
        words = [word for word in pattern_l.split() if len(word) > 2]
        if not words:
            continue
        matches = sum(1 for word in words if _contains_keyword(title_l, word))
        ratio = matches / len(words)
        if ratio >= 0.66:
            best = max(best, 25)
        elif ratio >= 0.33:
            best = max(best, 15)
        elif matches:
            best = max(best, 8)
    return best


def _keyword_lists(
    text_l: str,
    must_have: list[str],
    nice_to_have: list[str],
    weights: dict[str, int],
) -> tuple[list[str], list[str], int]:
    matched: list[str] = []
    missing: list[str] = []
    score = 0

    nice_pts = weights.get("nice_to_have_match", 3)

    unique_must = _dedupe_keywords(must_have)
    for keyword in unique_must:
        if _contains_keyword(text_l, keyword):
            matched.append(keyword)
        else:
            missing.append(keyword)

    if unique_must:
        ratio = len(matched) / len(unique_must)
        score += round(ratio * 40)
        if ratio < 0.25:
            score -= 5

    for keyword in nice_to_have:
        if _contains_keyword(text_l, keyword):
            matched.append(keyword)
            score += nice_pts

    return matched, missing, score


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    """Collapse redundant variants like backend / back-end / back end."""
    normalized_groups: list[tuple[str, str]] = []
    for keyword in keywords:
        key = _normalize(keyword).replace("-", "").replace(" ", "")
        if not key:
            continue
        if any(key in existing or existing in key for existing, _ in normalized_groups):
            continue
        normalized_groups.append((key, keyword.lower()))

    deduped = [original for _, original in normalized_groups]
    return deduped[:8]


def _unique_lower(keywords: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for keyword in keywords:
        key = _normalize(keyword)
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _negative_keyword_hits(text_l: str, keywords: list[str]) -> list[str]:
    return [kw for kw in _unique_lower(keywords) if _contains_keyword(text_l, kw)]


# Phrases indicating the years requirement is optional, not mandatory.
_ADVANTAGE_MARKERS = [
    "advantage",
    "a plus",
    "nice to have",
    "preferred",
    "bonus",
    "יתרון",
    "עדיפות",
    "לא חובה",
]

# Ranges like "1-3 years" / "1-3 שנות ניסיון" — the LOWER bound is the requirement.
_YEARS_RANGE_PATTERNS = [
    r"(\d+)\s*[-–—]\s*\d+\+?\s*(?:years?|yrs?)",
    r"(\d+)\s*[-–—]\s*\d+\+?\s*שנות",
]

_YEARS_SINGLE_PATTERNS = [
    r"(\d+)\+?\s*(?:years?|yrs?)\s+(?:of\s+)?experience",
    r"(\d+)\+?\s*שנות?\s+ניסיון",
    r"ניסיון של\s+(\d+)\+?",
]


def _is_advantage_context(text_l: str, start: int, end: int) -> bool:
    """True when the years mention sits near 'advantage'-style wording."""
    context = text_l[max(0, start - 80):min(len(text_l), end + 80)]
    return any(marker in context for marker in _ADVANTAGE_MARKERS)


def _years_required_reject(text_l: str, max_years: int, candidate_years: Any) -> str | None:
    if candidate_years not in (None, "", "unknown", 0, "0"):
        try:
            if float(candidate_years) > 0:
                return None
        except (TypeError, ValueError):
            pass

    range_spans: list[tuple[int, int]] = []
    for pattern in _YEARS_RANGE_PATTERNS:
        for match in re.finditer(pattern, text_l):
            range_spans.append(match.span())
            if _is_advantage_context(text_l, *match.span()):
                continue
            years = int(match.group(1))
            if years > max_years:
                return f"Requires {years}+ years experience"

    for pattern in _YEARS_SINGLE_PATTERNS:
        for match in re.finditer(pattern, text_l):
            start, end = match.span()
            # Skip numbers already consumed as part of a range ("1-3 years").
            if any(start < r_end and end > r_start for r_start, r_end in range_spans):
                continue
            if _is_advantage_context(text_l, start, end):
                continue
            years = int(match.group(1))
            if years > max_years:
                return f"Requires {years}+ years experience"

    return None


def _location_bonus(text_l: str, location: str, prefs: dict[str, Any]) -> int:
    bonus = 0
    location_l = _normalize(location)
    combined = f"{text_l} {location_l}"

    for pref in prefs.get("preferred_locations", []):
        if _contains_keyword(combined, pref):
            bonus += prefs.get("location_bonus", 10)
            break

    if prefs.get("remote_ok"):
        for keyword in prefs.get("remote_keywords", []):
            if _contains_keyword(combined, keyword):
                bonus += prefs.get("remote_bonus", 8)
                break

    return bonus


def score_to_decision(score: int) -> str:
    """Public mapping from a 0-100 score to a decision label."""
    return _score_to_decision(score)


def score_to_action(score: int) -> str:
    """Public mapping from a 0-100 score to a recommended action."""
    decision = _score_to_decision(score)
    return _score_to_action(score, decision)


def _score_to_decision(score: int) -> str:
    if score >= 80:
        return "HIGH_MATCH"
    if score >= 55:
        return "MEDIUM_MATCH"
    if score >= 30:
        return "LOW_MATCH"
    return "REJECT"


def _score_to_action(score: int, decision: str) -> str:
    if decision == "REJECT":
        return "SKIP"
    if score >= 75:
        return "APPLY_NOW"
    if score >= 50:
        return "APPLY_IF_DESPERATE"
    return "SKIP"


def _check_global_reject_rules(
    text_l: str,
    title_l: str,
    rules: list[str],
    *,
    candidate_years: Any = None,
    max_years: int = 2,
) -> str | None:
    sales_terms = ["sales", "customer success", "account executive", "מכירות"]
    tech_terms = ["developer", "engineer", "python", "backend", "support", "analyst", "מפתח", "תמיכה"]

    for rule in rules:
        rule_l = _normalize(rule)
        if "senior" in rule_l or "lead" in rule_l or "manager" in rule_l:
            senior_hits = _negative_keyword_hits(
                title_l,
                ["senior", "lead", "principal", "manager", "director", "head of", "בכיר", "מנהל"],
            )
            if senior_hits:
                return f"Global rule: senior/lead role ({', '.join(senior_hits)})"

        if "sales" in rule_l or "customer success" in rule_l:
            if any(term in text_l or term in title_l for term in sales_terms):
                if not any(term in text_l or term in title_l for term in tech_terms):
                    return "Global rule: unrelated sales/customer success role"

        if "3+ years" in rule_l or "years" in rule_l:
            reject = _years_required_reject(text_l, max_years=max_years, candidate_years=candidate_years)
            if reject:
                return f"Global rule: {reject}"

    return None


def classify_job(
    job: dict[str, Any],
    strategy: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    cv_profile: dict[str, Any] | None = None,
) -> ClassificationResult:
    """Classify and score a job locally using the AI-generated strategy."""
    text = _job_text(job)
    text_l = _normalize(text)
    title_l = _normalize(job.get("title") or "")
    weights = strategy.get("skill_weights", {})
    seniority = strategy.get("seniority_filters", {})
    location_prefs = strategy.get("location_preferences", {})

    categories = strategy.get("job_categories", [])
    if not categories:
        return ClassificationResult(
            match_score=0,
            category="uncategorized",
            decision="REJECT",
            recommended_action="SKIP",
            rejection_reason="No job categories in matching strategy",
            match_reason="No categories defined — run analyze_roles.py",
        )

    candidate_years = None
    if cv_profile and isinstance(cv_profile.get("experience"), dict):
        candidate_years = cv_profile["experience"].get("years_of_experience_estimate")

    global_reject = _check_global_reject_rules(
        text_l,
        title_l,
        strategy.get("global_reject_rules", []),
        candidate_years=candidate_years,
        max_years=int(seniority.get("max_years_required_if_no_experience", 2) or 2),
    )
    if global_reject:
        return ClassificationResult(
            match_score=0,
            category="rejected",
            decision="REJECT",
            recommended_action="SKIP",
            rejection_reason=global_reject,
            match_reason=global_reject,
        )

    best_category: dict[str, Any] | None = None
    best_score = -999
    best_matched: list[str] = []
    best_missing: list[str] = []
    best_details = ""

    for category in categories:
        title_score = _title_category_score(title_l, category)
        matched, missing, keyword_score = _keyword_lists(
            text_l,
            category.get("must_have_keywords", []),
            category.get("nice_to_have_keywords", []),
            weights,
        )
        # Full penalty only for negative keywords in the TITLE; a mention in the
        # description (e.g. "mentored by senior engineers") gets a reduced penalty.
        neg_keywords = _unique_lower(
            category.get("negative_keywords", []) + seniority.get("reject_keywords", [])
        )
        title_neg_hits = [kw for kw in neg_keywords if _contains_keyword(title_l, kw)]
        desc_neg_hits = [
            kw for kw in neg_keywords
            if kw not in title_neg_hits and _contains_keyword(text_l, kw)
        ]
        neg_hits = title_neg_hits + desc_neg_hits
        full_penalty = weights.get("negative_keyword_penalty", 15)
        desc_penalty = max(1, round(full_penalty / 3))
        neg_penalty = full_penalty * len(title_neg_hits) + desc_penalty * len(desc_neg_hits)

        prefer_bonus = 0
        for keyword in seniority.get("prefer_keywords", []):
            if _contains_keyword(text_l, keyword):
                prefer_bonus += 5

        loc_bonus = _location_bonus(text_l, job.get("location") or "", location_prefs)
        weight = float(category.get("score_weight", 1.0) or 1.0)

        raw_score = title_score + keyword_score + prefer_bonus + loc_bonus - neg_penalty
        score = round(max(0, min(100, raw_score * weight)))

        if score > best_score:
            best_score = score
            best_category = category
            best_matched = matched
            best_missing = missing
            best_details = (
                f"title={title_score}, keywords={keyword_score}, "
                f"location={loc_bonus}, penalties={neg_penalty}"
            )
            if neg_hits:
                best_details += f", negative={','.join(neg_hits)}"

    assert best_category is not None

    years_reject = _years_required_reject(
        text_l,
        int(seniority.get("max_years_required_if_no_experience", 2) or 2),
        candidate_years,
    )
    if years_reject:
        return ClassificationResult(
            match_score=max(0, best_score - 25),
            category=best_category.get("category", "unknown"),
            decision="REJECT",
            recommended_action="SKIP",
            matched_keywords=best_matched,
            missing_keywords=best_missing,
            rejection_reason=years_reject,
            match_reason=f"{years_reject}; {best_details}",
        )

    senior_hits = _negative_keyword_hits(title_l, seniority.get("reject_keywords", []))
    if senior_hits and best_score < 70:
        return ClassificationResult(
            match_score=max(0, best_score - 20),
            category=best_category.get("category", "unknown"),
            decision="REJECT",
            recommended_action="SKIP",
            matched_keywords=best_matched,
            missing_keywords=best_missing,
            rejection_reason=f"Seniority filter: {', '.join(senior_hits)}",
            match_reason=f"Senior title penalty; {best_details}",
        )

    decision = _score_to_decision(best_score)
    if decision not in VALID_DECISIONS:
        decision = _score_to_decision(best_score)
    action = _score_to_action(best_score, decision)
    if action not in VALID_ACTIONS:
        action = "SKIP"

    return ClassificationResult(
        match_score=clamp_score(best_score),
        category=str(best_category.get("category", "unknown")),
        decision=decision,
        recommended_action=action,
        matched_keywords=best_matched,
        missing_keywords=best_missing,
        match_reason=best_details,
    )


def classify_job_with_strategy(
    job: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    cv_profile: dict[str, Any] | None = None,
    strategy: dict[str, Any] | None = None,
) -> ClassificationResult:
    """Load strategy (or fallback), classify a job, and calibrate score with profile/CV fit."""
    active_strategy = strategy or load_matching_strategy()
    if not active_strategy and profile is not None and cv_profile is not None:
        active_strategy = fallback_matching_strategy(profile, cv_profile)
    if not active_strategy:
        return ClassificationResult(
            match_score=0,
            category="uncategorized",
            decision="REJECT",
            recommended_action="SKIP",
            rejection_reason="No matching strategy available",
            match_reason="Run: python src/analyze_roles.py",
        )

    result = classify_job(job, active_strategy, profile=profile, cv_profile=cv_profile)
    if result.rejection_reason or profile is None or cv_profile is None:
        return result

    from rule_based_matcher import score_job_fallback

    fallback_score, _ = score_job_fallback(job, profile, cv_profile)
    result.fallback_score = fallback_score

    strategy_score = result.match_score
    blended = round(
        STRATEGY_BLEND_WEIGHT * strategy_score + (1 - STRATEGY_BLEND_WEIGHT) * fallback_score
    )
    result.match_score = clamp_score(blended)
    result.decision = _score_to_decision(result.match_score)
    result.recommended_action = _score_to_action(result.match_score, result.decision)
    result.match_reason = (
        f"{result.match_reason}; blended(strategy={strategy_score}, profile_cv={fallback_score})"
    )
    return result
