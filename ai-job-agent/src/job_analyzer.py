"""Structured job posting analysis — AI extraction with rule-based fallback."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ai_client import (
    OpenAIAPIError,
    call_openai_json,
    is_ai_available,
    normalize_string_list,
    truncate_text,
)
from config import OPENAI_JOB_MAX_CHARS
from job_identity import compute_job_content_hash
from multilingual_normalizer import to_canonical
from rule_based_matcher import JUNIOR_KEYWORDS, SENIOR_KEYWORDS
from skills import detect_skills

SENIORITY_LEVELS = frozenset({
    "intern", "student", "junior", "mid", "senior", "lead", "manager", "unknown",
})

LOCATION_TYPES = frozenset({"remote", "hybrid", "onsite", "unknown"})

LANGUAGE_KEYWORDS = {
    "english": "English",
    "אנגלית": "English",
    "hebrew": "Hebrew",
    "עברית": "Hebrew",
    "arabic": "Arabic",
    "ערבית": "Arabic",
    "russian": "Russian",
    "רוסית": "Russian",
    "french": "French",
    "צרפתית": "French",
}

YEARS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:\+)?\s*(?:years?|yrs?|שנ(?:ות|ה)?(?:\s+ניסיון)?)",
    re.IGNORECASE,
)

MANDATORY_MARKERS = (
    "must have",
    "required",
    "mandatory",
    "חובה",
    "דרישת סף",
    "נדרש",
    "חייב",
)


JOB_SYSTEM_PROMPT = """You are an expert job posting analyst for ANY profession
(healthcare, education, finance, law, marketing, logistics, hospitality, engineering,
IT, creative, public sector, trades, etc.). Do NOT assume or favor software/IT.

You receive a job posting (Hebrew and/or English).

Return ONE JSON object with exactly these keys:

- title: string (job title)
- professional_domain: string — the Target Professional Domain of THIS role, deduced
  dynamically from the posting (e.g. "Software Development", "Digital Marketing",
  "Graphic Design", "Product Management", "Clinical Nursing", "Accounting").
  Use a short free-form label; never force a fixed industry taxonomy.
- seniority: one of intern, student, junior, mid, senior, lead, manager, unknown
- required_skills: array of required skill strings (canonical names)
- preferred_skills: array of nice-to-have skill strings
- mandatory_requirements: array of hard requirements that are non-negotiable
  (e.g. years of experience, certifications, fluent languages, must-know tools)
- hard_constraints: array of STRICT Must-Have threshold requirements that are
  deal-breakers if unmet — dynamically extracted from THIS JD only. Examples of
  the *kinds* of constraints (do not invent if absent): minimum years in a
  specific niche role, mandatory certifications/licenses, specific operational
  environments or work modes explicitly required, regulated clinical/legal settings,
  on-call/shift requirements. Prefer short, concrete phrases copied/paraphrased
  from the JD. Soft skills and generic buzzwords are NEVER hard constraints.
- years_experience_min: number or null (minimum years required)
- education: array of education requirements
- languages: array of required languages
- certifications: array of required certifications
- location_type: one of remote, hybrid, onsite, unknown
- location: string (city/region if mentioned)
- technologies: array of technologies/tools mentioned
- responsibilities: array of key responsibility phrases

Rules:
- Base everything ONLY on the job text provided. Do not invent requirements.
- Distinguish required vs preferred skills clearly.
- Put general hard gates in mandatory_requirements; put the STRICTEST deal-breaker
  thresholds also (or only) in hard_constraints.
- professional_domain must reflect the job's core track, not generic soft skills.
- Handle Hebrew and English postings naturally.
- Return valid JSON only, no markdown."""


@dataclass
class JobProfile:
    title: str = ""
    professional_domain: str = ""
    seniority: str | None = None
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    mandatory_requirements: list[str] = field(default_factory=list)
    hard_constraints: list[str] = field(default_factory=list)
    years_experience_min: float | None = None
    education: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    location_type: str | None = None
    location: str | None = None
    technologies: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    analyzed_with: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobProfile:
        years = data.get("years_experience_min")
        try:
            years_val = float(years) if years is not None else None
        except (TypeError, ValueError):
            years_val = None

        seniority = str(data.get("seniority") or "unknown").strip().lower()
        if seniority not in SENIORITY_LEVELS:
            seniority = "unknown"

        location_type = str(data.get("location_type") or "unknown").strip().lower()
        if location_type not in LOCATION_TYPES:
            location_type = "unknown"

        professional_domain = str(
            data.get("professional_domain") or data.get("target_professional_domain") or ""
        ).strip()

        hard_constraints = normalize_string_list(
            data.get("hard_constraints") or data.get("must_have_constraints"),
            max_items=15,
        )

        return cls(
            title=str(data.get("title") or ""),
            professional_domain=professional_domain,
            seniority=seniority,
            required_skills=normalize_string_list(data.get("required_skills"), max_items=30),
            preferred_skills=normalize_string_list(data.get("preferred_skills"), max_items=20),
            mandatory_requirements=normalize_string_list(
                data.get("mandatory_requirements"), max_items=20
            ),
            hard_constraints=hard_constraints,
            years_experience_min=years_val,
            education=normalize_string_list(data.get("education"), max_items=10),
            languages=normalize_string_list(data.get("languages"), max_items=10),
            certifications=normalize_string_list(data.get("certifications"), max_items=10),
            location_type=location_type,
            location=str(data.get("location") or "").strip() or None,
            technologies=normalize_string_list(data.get("technologies"), max_items=30),
            responsibilities=normalize_string_list(data.get("responsibilities"), max_items=15),
            analyzed_with=str(data.get("analyzed_with") or "none"),
        )


def _job_text(job: dict[str, Any]) -> str:
    title = job.get("title") or ""
    short = job.get("description") or ""
    full = job.get("full_description") or ""
    return f"{title}\n{short}\n{full}".strip()


def _detect_seniority(text: str, title: str) -> str:
    combined = f"{title} {text}".lower()
    if any(kw in combined for kw in JUNIOR_KEYWORDS):
        return "junior"
    if any(kw in combined for kw in ("intern", "סטודנט", "student")):
        return "student"
    if any(kw in combined for kw in SENIOR_KEYWORDS):
        if "manager" in combined or "מנהל" in combined:
            return "manager"
        if "lead" in combined or "ראש צוות" in combined:
            return "lead"
        return "senior"
    if "mid" in combined:
        return "mid"
    return "unknown"


def _detect_years(text: str) -> float | None:
    matches = YEARS_RE.findall(text)
    if not matches:
        return None
    try:
        return max(float(m) for m in matches)
    except ValueError:
        return None


def _detect_languages(text: str) -> list[str]:
    text_l = text.lower()
    found: list[str] = []
    for keyword, canonical in LANGUAGE_KEYWORDS.items():
        if keyword in text_l and canonical not in found:
            found.append(canonical)
    return found


def _detect_location_type(text: str) -> str:
    text_l = text.lower()
    if any(kw in text_l for kw in ("remote", "מהבית", "עבודה מהבית", "work from home")):
        return "remote"
    if any(kw in text_l for kw in ("hybrid", "היברידי")):
        return "hybrid"
    if any(kw in text_l for kw in ("onsite", "from office", "במשרד")):
        return "onsite"
    return "unknown"


def _extract_mandatory_lines(text: str) -> list[str]:
    mandatory: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line_l = line.lower()
        if any(marker in line_l for marker in MANDATORY_MARKERS):
            mandatory.append(line[:200])
    return mandatory[:10]


# Generic stop-words stripped when deriving a free-form domain label from a title.
# Intentionally language-/seniority-oriented only — NOT an industry taxonomy.
_TITLE_DOMAIN_STOPWORDS = frozenset({
    "junior", "senior", "mid", "lead", "principal", "staff", "intern", "student",
    "manager", "head", "chief", "assistant", "associate", "entry", "level",
    "remote", "hybrid", "full", "part", "time", "contract", "temporary",
    "the", "and", "or", "of", "for", "a", "an", "in", "at", "to",
    "ג'וניור", "ג׳וניור", "סניור", "בכיר", "זוטר", "סטודנט", "מנהל",
})


def _derive_professional_domain(title: str, text: str = "") -> str:
    """Derive a free-form Target Professional Domain from the job title/text.

    Domain-agnostic: uses the posting's own wording rather than a fixed
    industry list. Prefer the cleaned title; fall back to a short phrase from
    the first line of the description.
    """
    cleaned_title = re.sub(r"\s+", " ", (title or "").strip())
    if cleaned_title:
        tokens = [
            t for t in re.split(r"[^\w\u0590-\u05FF/+.-]+", cleaned_title, flags=re.UNICODE)
            if t and t.lower() not in _TITLE_DOMAIN_STOPWORDS and len(t) >= 2
        ]
        if tokens:
            return " ".join(tokens[:6])
        return cleaned_title

    for line in (text or "").splitlines():
        line = line.strip()
        if len(line) >= 8:
            return line[:80]
    return ""


def _extract_hard_constraints(
    mandatory: list[str],
    *,
    years: float | None,
    certifications: list[str],
    text: str,
) -> list[str]:
    """Pick the strictest deal-breaker constraints from mandatory lines (rules).

    Soft-skill / vague phrases are skipped. No industry-specific hardcoding —
    selection is based on generic threshold markers (years, certs, must-have).
    """
    soft_markers = (
        "communication", "team player", "motivated", "passionate",
        "self-starter", "detail-oriented", "תקשורת", "יחסי אנוש", "מוטיבציה",
    )
    constraints: list[str] = []
    for line in mandatory:
        line_l = line.lower()
        if any(soft in line_l for soft in soft_markers):
            continue
        if any(marker in line_l for marker in MANDATORY_MARKERS) or YEARS_RE.search(line):
            constraints.append(line[:200])
    for cert in certifications:
        cert_req = f"Certification: {cert}"
        if cert_req not in constraints:
            constraints.append(cert_req)
    # Niche operational phrases often appear as short must-have lines.
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 160:
            continue
        line_l = line.lower()
        if any(marker in line_l for marker in MANDATORY_MARKERS) and line not in constraints:
            if not any(soft in line_l for soft in soft_markers):
                constraints.append(line[:200])
    if years is not None:
        years_req = f"{years}+ years experience"
        if years_req not in constraints and not any("years" in c.lower() or "שנ" in c for c in constraints):
            constraints.append(years_req)
    return constraints[:10]


def analyze_job_fallback(job: dict[str, Any]) -> JobProfile:
    """Rule-based structured extraction (no AI)."""
    text = _job_text(job)
    title = str(job.get("title") or "")
    detected_skills = detect_skills(text)
    normalized_skills = sorted(
        {to_canonical(s) or s for s in detected_skills if s}
    )
    years = _detect_years(text)

    mandatory = _extract_mandatory_lines(text)
    if years is not None:
        mandatory.append(f"{years}+ years experience")

    title_tokens = [
        to_canonical(t) or t
        for t in re.split(r"[^\w\u0590-\u05FF]+", title, flags=re.UNICODE)
        if len(t) >= 2
    ]
    domain_hints = [t for t in title_tokens if t and t not in normalized_skills][:5]
    professional_domain = _derive_professional_domain(title, text)
    hard_constraints = _extract_hard_constraints(
        mandatory, years=years, certifications=[], text=text
    )

    return JobProfile(
        title=title,
        professional_domain=professional_domain,
        seniority=_detect_seniority(text, title),
        required_skills=normalized_skills[:15],
        preferred_skills=domain_hints,
        mandatory_requirements=mandatory,
        hard_constraints=hard_constraints,
        years_experience_min=years,
        education=[],
        languages=_detect_languages(text),
        certifications=[],
        location_type=_detect_location_type(text),
        location=str(job.get("location") or "").strip() or None,
        technologies=normalized_skills[:20],
        responsibilities=[],
        analyzed_with="rules",
    )


def analyze_job(job: dict[str, Any], *, use_ai: bool = False) -> JobProfile:
    """Analyze a job posting into a structured profile (rule-based by default)."""
    if use_ai and is_ai_available():
        try:
            return analyze_job_with_openai(job)
        except OpenAIAPIError:
            pass
    return analyze_job_fallback(job)


def analyze_job_with_openai(job: dict[str, Any]) -> JobProfile:
    """Extract structured job profile using OpenAI."""
    text = truncate_text(_job_text(job), OPENAI_JOB_MAX_CHARS)
    user_prompt = (
        f"Title: {job.get('title') or 'N/A'}\n"
        f"Company: {job.get('company') or 'N/A'}\n"
        f"Location: {job.get('location') or 'N/A'}\n\n"
        f"Job posting:\n{text}"
    )
    cache_payload = f"job_profile_v2\n{job.get('job_url') or ''}\n{job_profile_hash(job)}"

    raw = call_openai_json(
        JOB_SYSTEM_PROMPT,
        user_prompt,
        cache_namespace="job_profile",
        cache_payload=cache_payload,
    )
    raw["title"] = raw.get("title") or job.get("title") or ""
    raw["analyzed_with"] = "openai"
    return JobProfile.from_dict(raw)


def parse_stored_job_profile(raw: str | None) -> JobProfile | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return JobProfile.from_dict(data)
    except (json.JSONDecodeError, TypeError):
        return None
    return None


def job_profile_hash(job: dict[str, Any]) -> str:
    """Hash of job content used to invalidate cached profiles."""
    return compute_job_content_hash(
        title=job.get("title") or "",
        company=job.get("company") or "",
        location=job.get("location") or "",
        description=job.get("description") or "",
        full_description=job.get("full_description") or "",
    )
