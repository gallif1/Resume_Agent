"""On-demand ATS-optimized CV tailoring via OpenAI (zero hallucination)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_client import (
    OpenAIAPIError,
    call_openai_json,
    is_ai_available,
    truncate_text,
)
from config import OPENAI_CV_MAX_CHARS, OPENAI_JOB_MAX_CHARS, cv_data_dir
from job_analyzer import JobProfile, parse_stored_job_profile
from profile_utils import load_cv_profile

TAILOR_SYSTEM_PROMPT = """You are an expert ATS resume writer for the Israeli tech job market.
You rewrite an existing CV to better align with ONE target job posting.

Return ONE JSON object with exactly these keys:
- markdown: string — full tailored CV in clean Markdown
- highlights: array of short strings — what you emphasized for ATS alignment
- caveats: array of short strings — honesty notes (e.g. gaps you could not invent)

CRITICAL — ZERO HALLUCINATION RULES:
1. NEVER invent fake work experience, employers, job titles, degrees, certifications,
   tools, projects, or years of experience that are not present in the source CV.
2. NEVER change a real job title to a different title (e.g. keep "Technical Support"
   as "Technical Support" — do NOT rename it to "Software Engineer").
3. You MAY reframe existing bullets to highlight transferable skills that ARE grounded
   in the source text (troubleshooting, scripts, SQL/queries, automation, customer
   systems, documentation, incident response, technical problem-solving, etc.).
4. You MAY reorder sections, tighten wording, and weave in job keywords ONLY when they
   honestly map to skills/experience already on the CV.
5. If the candidate lacks a required skill, do NOT claim they have it. Prefer omitting
   it or noting related adjacent experience without false claims.
6. Preserve contact details, education facts, dates, and employers from the source CV.
7. Write the CV primarily in the same language as the source CV (Hebrew and/or English).
   Job-keyword alignment may include English tech terms when appropriate.
8. Format markdown with clear headings (# Name, ## Summary, ## Experience, ## Skills,
   ## Education, ## Projects / Certifications as applicable) and bullet lists.
"""


class TailorCvError(RuntimeError):
    """Raised when CV tailoring cannot be completed."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def tailored_cv_dir(cv_id: str) -> Path:
    return cv_data_dir(cv_id) / "tailored_cvs"


def tailored_cv_path(cv_id: str, job_id: int) -> Path:
    return tailored_cv_dir(cv_id) / f"{job_id}.md"


def _cv_source_payload(cv_profile: dict[str, Any]) -> str:
    """Build a compact factual snapshot of the CV for the LLM prompt."""
    parts: list[str] = []
    raw = (cv_profile.get("raw_text") or "").strip()
    if raw:
        parts.append("=== RAW CV TEXT ===")
        parts.append(truncate_text(raw, OPENAI_CV_MAX_CHARS))

    contact = cv_profile.get("contact") or {}
    if isinstance(contact, dict) and any(contact.values()):
        parts.append("=== CONTACT ===")
        parts.append(json.dumps(contact, ensure_ascii=False, indent=2))

    for key in (
        "experience",
        "education",
        "skills",
        "projects",
        "certifications",
        "best_fit_roles",
        "universal_profile",
        "sections",
    ):
        value = cv_profile.get(key)
        if value:
            parts.append(f"=== {key.upper()} ===")
            parts.append(json.dumps(value, ensure_ascii=False, indent=2)[:8000])

    return "\n\n".join(parts)


def _job_prompt_payload(job: dict[str, Any], job_profile: JobProfile | None) -> str:
    description = job.get("full_description") or job.get("description") or ""
    parts = [
        f"Title: {job.get('title') or ''}",
        f"Company: {job.get('company') or ''}",
        f"Location: {job.get('location') or ''}",
        f"Source: {job.get('source') or ''}",
        "Description:",
        truncate_text(description, OPENAI_JOB_MAX_CHARS),
    ]
    if job_profile is not None:
        parts.append("Structured JobProfile JSON:")
        parts.append(json.dumps(job_profile.to_dict(), ensure_ascii=False, indent=2))
    return "\n".join(parts)


def _normalize_tailor_result(raw: dict[str, Any]) -> dict[str, Any]:
    markdown = str(raw.get("markdown") or "").strip()
    if not markdown:
        raise TailorCvError("OpenAI returned an empty tailored CV", status_code=502)
    highlights = raw.get("highlights") if isinstance(raw.get("highlights"), list) else []
    caveats = raw.get("caveats") if isinstance(raw.get("caveats"), list) else []
    return {
        "markdown": markdown,
        "highlights": [str(h).strip() for h in highlights if str(h).strip()][:12],
        "caveats": [str(c).strip() for c in caveats if str(c).strip()][:12],
    }


def load_saved_tailored_cv(cv_id: str, job_id: int) -> str | None:
    path = tailored_cv_path(cv_id, job_id)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def save_tailored_cv(cv_id: str, job_id: int, markdown: str) -> Path:
    directory = tailored_cv_dir(cv_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = tailored_cv_path(cv_id, job_id)
    path.write_text(markdown.strip() + "\n", encoding="utf-8")
    return path


def tailor_cv_for_job(
    cv_id: str,
    job: dict[str, Any],
    *,
    force: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Generate (or load) an ATS-tailored Markdown CV for one job."""
    job_id = int(job["id"])
    if not force:
        cached = load_saved_tailored_cv(cv_id, job_id)
        if cached:
            return {
                "markdown": cached,
                "highlights": [],
                "caveats": [],
                "from_cache": True,
                "saved_path": str(tailored_cv_path(cv_id, job_id)),
            }

    if not is_ai_available():
        raise TailorCvError(
            "OPENAI_API_KEY is not configured — cannot tailor the CV",
            status_code=503,
        )

    cv_profile = load_cv_profile(cv_id)
    if not cv_profile or not (
        cv_profile.get("raw_text")
        or cv_profile.get("experience")
        or cv_profile.get("skills")
    ):
        raise TailorCvError(
            "Parsed CV profile not found — run the agent / parse CV first",
            status_code=404,
        )

    job_profile = parse_stored_job_profile(job.get("job_profile"))
    cv_payload = _cv_source_payload(cv_profile)
    job_payload = _job_prompt_payload(job, job_profile)

    user_prompt = (
        "Rewrite the candidate CV to improve ATS alignment for this job.\n"
        "Remember: do not invent experience; keep real job titles unchanged.\n\n"
        f"{cv_payload}\n\n"
        "=== TARGET JOB ===\n"
        f"{job_payload}"
    )

    try:
        raw = call_openai_json(
            TAILOR_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.25,
            use_cache=use_cache,
            cache_namespace=f"tailor_cv_{cv_id}",
            cache_payload=f"{cv_id}|{job_id}|{job_payload[:2000]}|{cv_payload[:4000]}",
        )
    except OpenAIAPIError as exc:
        raise TailorCvError(str(exc), status_code=502) from exc

    result = _normalize_tailor_result(raw)
    path = save_tailored_cv(cv_id, job_id, result["markdown"])
    return {
        **result,
        "from_cache": bool(raw.get("_from_cache")),
        "saved_path": str(path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
