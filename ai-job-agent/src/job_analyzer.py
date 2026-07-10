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


JOB_SYSTEM_PROMPT = """You are an expert job posting analyst for the Israeli job market.
You receive a job posting (Hebrew and/or English).

Return ONE JSON object with exactly these keys:

- title: string (job title)
- seniority: one of intern, student, junior, mid, senior, lead, manager, unknown
- required_skills: array of required skill strings (canonical names)
- preferred_skills: array of nice-to-have skill strings
- mandatory_requirements: array of hard requirements that are non-negotiable
  (e.g. "5+ years experience", "CISSP certification", "fluent English", "must know Python")
- years_experience_min: number or null (minimum years required)
- education: array of education requirements
- languages: array of required languages
- certifications: array of required certifications
- location_type: one of remote, hybrid, onsite, unknown
- location: string (city/region if mentioned)
- technologies: array of technologies/tools mentioned
- responsibilities: array of key responsibility phrases

Rules:
- Base everything ONLY on the job text provided.
- Distinguish required vs preferred skills clearly.
- Put hard gates (years, mandatory certs, mandatory languages) in mandatory_requirements.
- Handle Hebrew and English postings naturally.
- Return valid JSON only, no markdown."""


@dataclass
class JobProfile:
    title: str = ""
    seniority: str | None = None
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    mandatory_requirements: list[str] = field(default_factory=list)
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

        return cls(
            title=str(data.get("title") or ""),
            seniority=seniority,
            required_skills=normalize_string_list(data.get("required_skills"), max_items=30),
            preferred_skills=normalize_string_list(data.get("preferred_skills"), max_items=20),
            mandatory_requirements=normalize_string_list(
                data.get("mandatory_requirements"), max_items=20
            ),
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

    return JobProfile(
        title=title,
        seniority=_detect_seniority(text, title),
        required_skills=normalized_skills[:15],
        preferred_skills=domain_hints,
        mandatory_requirements=mandatory,
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
    cache_payload = f"job_profile_v1\n{job.get('job_url') or ''}\n{job_profile_hash(job)}"

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
