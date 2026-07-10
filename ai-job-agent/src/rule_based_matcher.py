"""Legacy keyword/skill-based job scoring — used as fallback when OpenAI is unavailable."""

from __future__ import annotations

from skills import detect_skills

SENIOR_KEYWORDS = [
    "senior",
    "sr.",
    "lead",
    "principal",
    "manager",
    "director",
    "head of",
    "בכיר",
    "מנהל",
    "ראש צוות",
]

JUNIOR_KEYWORDS = [
    "junior",
    "jr.",
    "entry",
    "graduate",
    "ללא ניסיון",
    "זוטר",
    "התחלתי",
]

REMOTE_KEYWORDS = [
    "remote",
    "מהבית",
    "עבודה מהבית",
    "hybrid",
    "היברידי",
]

ISRAEL_LOCATION_HINTS = [
    "israel",
    "ישראל",
    "תל אביב",
    "ירושלים",
    "חיפה",
    "באר שבע",
    "נתניה",
    "ראשון",
    "פתח תקווה",
    "רמת גן",
    "הרצליה",
    "בני ברק",
    "רעננה",
    "כפר סבא",
    "אשדוד",
    "חולון",
]

ROLE_EXTRA_KEYWORDS = {
    "backend": ["backend", "back-end", "back end", "python", "api", "developer"],
    "it support": [
        "it",
        "support",
        "helpdesk",
        "help desk",
        "technical support",
        "טכנאי",
        "תמיכה",
        "help-desk",
    ],
    "soc": ["soc", "analyst", "cyber", "security", "סייבר", "אבטחת מידע", "noc"],
}


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def score_role_match(title: str, target_role: str) -> int:
    title_l = _normalize(title)
    role_l = _normalize(target_role)

    if role_l in title_l:
        return 40

    role_words = [word for word in role_l.split() if len(word) > 2]
    if not role_words:
        return 0

    matches = sum(1 for word in role_words if word in title_l)
    ratio = matches / len(role_words)

    if ratio >= 0.66:
        return 40
    if ratio >= 0.33:
        return 25
    if matches > 0:
        return 15

    for role_key, keywords in ROLE_EXTRA_KEYWORDS.items():
        if role_key in role_l:
            if any(keyword in title_l for keyword in keywords):
                return 20

    return 0


def description_bonus(description: str, target_roles: list[str]) -> int:
    desc_l = _normalize(description)
    if not desc_l:
        return 0

    for role in target_roles:
        role_l = _normalize(role)
        role_words = [word for word in role_l.split() if len(word) > 2]
        if any(word in desc_l for word in role_words):
            return 10

        for role_key, keywords in ROLE_EXTRA_KEYWORDS.items():
            if role_key in role_l and any(keyword in desc_l for keyword in keywords):
                return 10

    return 0


def location_bonus(location: str, description: str, profile: dict) -> int:
    location_l = _normalize(location)
    text_l = f"{location_l} {_normalize(description)}"
    score = 0

    if _normalize(profile.get("location", "")) == "israel":
        if any(hint in text_l for hint in ISRAEL_LOCATION_HINTS) or location_l:
            score += 15

    if profile.get("remote") and any(keyword in text_l for keyword in REMOTE_KEYWORDS):
        score += 10

    return min(score, 20)


def job_text(job: dict) -> str:
    description = job.get("full_description") or job.get("description") or ""
    return f"{job.get('title') or ''} {description}"


def gather_cv_skills(cv_profile: dict) -> set[str]:
    skills = cv_profile.get("skills", {})
    flat: set[str] = set()

    if isinstance(skills, dict):
        for items in skills.values():
            flat.update(str(item).lower() for item in items)
    elif isinstance(skills, list):
        flat.update(str(item).lower() for item in skills)

    return flat


def profile_fit_score(job: dict, profile: dict) -> int:
    title = job.get("title") or ""
    description = job.get("full_description") or job.get("description") or ""
    title_l = _normalize(title)
    text_l = f"{title_l} {_normalize(description)}"

    role_score = max(
        (score_role_match(title, role) for role in profile.get("target_roles", [])),
        default=0,
    )

    score = role_score
    score += location_bonus(job.get("location") or "", description, profile)
    score += description_bonus(description, profile.get("target_roles", []))

    if any(keyword in title_l for keyword in SENIOR_KEYWORDS):
        score -= 30

    if any(keyword in text_l for keyword in JUNIOR_KEYWORDS):
        score += 10

    return max(0, min(100, score))


def cv_fit_score(job: dict, cv_skills: set[str]) -> tuple[int, list[str]]:
    job_skills = {skill.lower() for skill in detect_skills(job_text(job))}
    if not job_skills:
        return 0, []

    matched = sorted(cv_skills & job_skills)
    if not matched:
        return 0, []

    coverage = len(matched) / len(job_skills)
    volume = min(len(matched) / 5, 1)
    score = round(100 * (0.6 * coverage + 0.4 * volume))
    return max(0, min(100, score)), matched


def score_job_fallback(job: dict, profile: dict, cv_profile: dict) -> tuple[int, str]:
    """Rule-based score with human-readable reason."""
    cv_skills = gather_cv_skills(cv_profile)
    profile_score = profile_fit_score(job, profile)

    if cv_skills:
        cv_score, matched = cv_fit_score(job, cv_skills)
        final = round(0.5 * profile_score + 0.5 * cv_score)
        skills_text = ", ".join(matched) if matched else "none"
        reason = (
            f"[fallback] profile {profile_score} + CV {cv_score} -> {final}; "
            f"matched skills: {skills_text}"
        )
    else:
        final = profile_score
        reason = f"[fallback] profile {profile_score} (no CV skills loaded)"

    return max(0, min(100, final)), reason
