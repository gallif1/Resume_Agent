"""Post-generation factual guards for the CV Tailor MVP."""

from __future__ import annotations

import logging
import re
from typing import Any

from cv_tailor.models import JobAnalysis, TailoredCvData

logger = logging.getLogger("cv_tailor.validation")

_IN_PROGRESS_RE = re.compile(
    r"\b(in progress|currently pursuing|expected graduation|pursuing)\b",
    re.IGNORECASE,
)
_COMPLETED_DATE_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}\s*[–\-—]\s*"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}\b",
    re.IGNORECASE,
)
_YEARS_CLAIM_RE = re.compile(
    r"\b(\d+\+?\s*(?:years?|yrs?))\s+(?:of\s+)?([a-z0-9./+#\s-]{2,40})\b",
    re.IGNORECASE,
)
_SKILL_TOKEN_RE = re.compile(r"[a-z0-9.+#/]+", re.IGNORECASE)


def parse_llm_response(raw: dict[str, Any]) -> tuple[TailoredCvData, JobAnalysis]:
    """Normalize flat or nested LLM JSON into tailored CV + job analysis."""
    if isinstance(raw.get("tailored_cv"), dict):
        cv_data = raw["tailored_cv"]
        analysis_data = raw.get("job_analysis") if isinstance(raw.get("job_analysis"), dict) else {}
    else:
        cv_data = raw
        analysis_data = raw.get("job_analysis") if isinstance(raw.get("job_analysis"), dict) else {}

    tailored_cv = TailoredCvData.from_llm_dict(cv_data)
    job_analysis = JobAnalysis.from_llm_dict(analysis_data)
    return tailored_cv, job_analysis


def _source_skill_tokens(source_text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _SKILL_TOKEN_RE.finditer(source_text.lower()):
        token = match.group(0).strip()
        if len(token) >= 2:
            tokens.add(token)
    # Common multi-word skills preserved as phrases in source
    for phrase in (
        "node.js",
        "react native",
        "ci/cd",
        "threadpoolexecutor",
        "fastapi",
        "postgresql",
        "sqlalchemy",
        "generative ai",
        "rest api",
        "rest apis",
        "websockets",
        "pytest",
        "aws",
        "ec2",
        "rds",
        "firebase",
        "mongodb",
        "angular",
        "laravel",
        "sqlite",
        "expo",
        "java",
    ):
        if phrase in source_text.lower():
            tokens.add(phrase.replace(" ", ""))
            tokens.add(phrase)
    return tokens


def _skill_supported(skill: str, source_text: str, source_tokens: set[str]) -> bool:
    skill_l = skill.strip().lower()
    if not skill_l:
        return True
    if skill_l in source_text.lower():
        return True
    normalized = re.sub(r"\s+", "", skill_l)
    if normalized in source_tokens:
        return True
    for token in source_tokens:
        if token in skill_l or skill_l in token:
            return True
    return False


def _source_supports_years_claim(source_text: str, technology: str, years: str) -> bool:
    tech = technology.strip().lower()
    if tech not in source_text.lower():
        return False
    # Require explicit duration near the technology in source, not just a skills mention.
    patterns = [
        rf"{re.escape(years)}\s*(?:years?|yrs?).{{0,40}}{re.escape(tech)}",
        rf"{re.escape(tech)}.{{0,40}}{re.escape(years)}\s*(?:years?|yrs?)",
        rf"(\d+\+?\s*(?:years?|yrs?)).{{0,30}}{re.escape(tech)}",
    ]
    return any(re.search(p, source_text, re.IGNORECASE) for p in patterns)


def _repair_education(source_text: str, tailored_cv: TailoredCvData) -> TailoredCvData:
    """Prevent completed degrees from being rewritten as in-progress."""
    source_has_completed_range = bool(_COMPLETED_DATE_RE.search(source_text))
    if not source_has_completed_range:
        return tailored_cv

    for edu in tailored_cv.education:
        combined = f"{edu.degree} {edu.dates}".strip()
        if _IN_PROGRESS_RE.search(combined):
            logger.warning("Repairing education status: removing unsupported in-progress wording")
            edu.degree = _IN_PROGRESS_RE.sub("", edu.degree).strip(" ,;-")
            if not edu.degree:
                edu.degree = "B.Sc. Computer Science"
    return tailored_cv


def _strip_unsupported_skills(source_text: str, tailored_cv: TailoredCvData) -> TailoredCvData:
    source_tokens = _source_skill_tokens(source_text)
    cleaned_groups = []
    for group in tailored_cv.skill_groups:
        kept = [s for s in group.skills if _skill_supported(s, source_text, source_tokens)]
        removed = [s for s in group.skills if s not in kept]
        for skill in removed:
            logger.warning("Removed unsupported skill from output: %s", skill)
        if kept:
            cleaned_groups.append(group.model_copy(update={"skills": kept}))
    tailored_cv.skill_groups = cleaned_groups

    tailored_cv.skills = [
        s for s in tailored_cv.skills if _skill_supported(s, source_text, source_tokens)
    ]
    return tailored_cv


def _strip_unsupported_years_claims(source_text: str, tailored_cv: TailoredCvData) -> TailoredCvData:
    """Remove 'N years of X' claims from summary/bullets when unsupported by source."""

    def clean_text(text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            years, tech = match.group(1), match.group(2).strip()
            if _source_supports_years_claim(source_text, tech, years):
                return match.group(0)
            logger.warning("Removed unsupported duration claim: %s %s", years, tech)
            return tech

        return _YEARS_CLAIM_RE.sub(repl, text)

    tailored_cv.summary = clean_text(tailored_cv.summary)
    for entry in tailored_cv.experience:
        entry.bullets = [clean_text(b) for b in entry.bullets]
    for project in tailored_cv.projects:
        project.description = clean_text(project.description)
        project.bullets = [clean_text(b) for b in project.bullets]
    return tailored_cv


def apply_factual_guards(source_text: str, tailored_cv: TailoredCvData) -> TailoredCvData:
    """Apply deterministic repairs for common factual mutations."""
    tailored_cv = _repair_education(source_text, tailored_cv)
    tailored_cv = _strip_unsupported_skills(source_text, tailored_cv)
    tailored_cv = _strip_unsupported_years_claims(source_text, tailored_cv)
    return tailored_cv
