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
_JUNIOR_RE = re.compile(r"\bjunior\b", re.IGNORECASE)
_ROLE_TITLE_RE = re.compile(
    r"(?:(?:senior|lead|staff|principal|junior)\s+)?"
    r"(?:full[-\s]?stack|backend|frontend|software|cloud|devops)?\s*"
    r"(?:developer|engineer|architect)",
    re.IGNORECASE,
)


def _infer_role_title(job_description: str) -> str:
    """Best-effort role title extraction from a job description."""
    text = job_description or ""
    for line in text.splitlines()[:8]:
        line = line.strip(" -–—|")
        if not line or len(line) > 80:
            continue
        match = _ROLE_TITLE_RE.search(line)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    match = _ROLE_TITLE_RE.search(text[:400])
    if match:
        return re.sub(r"\s+", " ", match.group(0)).strip()
    return ""


def _role_aligned_title(target_job_title: str) -> str:
    title = re.sub(r"\s+", " ", (target_job_title or "").strip())
    if not title:
        return title
    low = title.lower()
    if "backend" in low and "developer" in low and "software" not in low:
        return "Backend Software Developer"
    if "full" in low and "stack" in low and "developer" in low:
        return title
    return title


def _posting_seeks_junior(job_description: str, job_analysis: JobAnalysis) -> bool:
    combined = " ".join(
        [
            job_description,
            job_analysis.target_job_title,
            job_analysis.seniority_required,
        ]
    )
    return bool(_JUNIOR_RE.search(combined))


def _align_professional_title(
    tailored_cv: TailoredCvData,
    *,
    job_description: str,
    job_analysis: JobAnalysis,
    source_text: str,
) -> TailoredCvData:
    """Replace generic/junior base-CV titles with the posting's target role when appropriate."""
    target = job_analysis.target_job_title.strip() or _infer_role_title(job_description)
    if not target:
        return tailored_cv

    current = tailored_cv.professional_title.strip()
    seeks_junior = _posting_seeks_junior(job_description, job_analysis)
    aligned = _role_aligned_title(target)

    if not current:
        tailored_cv.professional_title = aligned
        logger.info("Set professional_title from posting: %s", aligned)
        return tailored_cv

    if _JUNIOR_RE.search(current) and not seeks_junior:
        logger.warning("Replacing junior title '%s' with posting-aligned '%s'", current, aligned)
        tailored_cv.professional_title = aligned
        return tailored_cv

    source_opening = source_text[:600].lower()
    if current.lower() in source_opening and _JUNIOR_RE.search(current) and not seeks_junior:
        logger.warning("Replacing base-CV title copy '%s' with '%s'", current, aligned)
        tailored_cv.professional_title = aligned

    return tailored_cv


def _summary_echoes_source(summary: str, source_text: str) -> bool:
    summary_norm = re.sub(r"\s+", " ", summary.lower()).strip()
    if len(summary_norm) < 40:
        return False
    source_norm = re.sub(r"\s+", " ", source_text.lower())
    # First ~120 chars of source summary block often get copied verbatim.
    for chunk in (120, 90, 70):
        if summary_norm[:chunk] and summary_norm[:chunk] in source_norm:
            return True
    return False


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


def apply_factual_guards(
    source_text: str,
    tailored_cv: TailoredCvData,
    *,
    job_description: str = "",
    job_analysis: JobAnalysis | None = None,
) -> TailoredCvData:
    """Apply deterministic repairs for common factual and alignment issues."""
    analysis = job_analysis or JobAnalysis()
    tailored_cv = _align_professional_title(
        tailored_cv,
        job_description=job_description,
        job_analysis=analysis,
        source_text=source_text,
    )
    tailored_cv = _repair_education(source_text, tailored_cv)
    tailored_cv = _strip_unsupported_skills(source_text, tailored_cv)
    tailored_cv = _strip_unsupported_years_claims(source_text, tailored_cv)

    if _summary_echoes_source(tailored_cv.summary, source_text):
        logger.warning(
            "Summary appears copied from base CV; model should rewrite per posting"
        )

    return tailored_cv
