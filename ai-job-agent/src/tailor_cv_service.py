"""On-demand ATS-optimized CV tailoring via OpenAI (zero hallucination)."""

from __future__ import annotations

import json
import re
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

# Bump when the tailored Markdown contract changes (invalidates OpenAI file cache).
TAILOR_PROMPT_VERSION = "v2"

TAILOR_SYSTEM_PROMPT = """You are an expert ATS resume writer for the Israeli tech job market.
You rewrite an existing CV to better align with ONE target job posting.

Return ONE JSON object with exactly these keys:
- markdown: string — the FULL response document in Markdown (see REQUIRED MARKDOWN STRUCTURE)
- changes_breakdown: array of short strings — the change bullets (same content as section 1)
- estimated_ats_score: integer 0-100 — realistic expected ATS match for the *tailored* CV
- cv_markdown: string — ONLY the resume body (section 3 content, without the section heading)
- highlights: array of short strings — 2-6 key ATS keyword alignments (English or Hebrew)
- caveats: array of short strings — honesty notes (skills not claimed, residual gaps)

REQUIRED MARKDOWN STRUCTURE for the `markdown` field (use these Hebrew headings):

## פירוט שינויים
- Bullet list explaining exactly what you reframed or highlighted for THIS job.
- Write in Hebrew when the source CV is primarily Hebrew; otherwise match the CV language.
- Example: "הודגשו כישורי troubleshooting ו-SQL מתפקיד Technical Support כדי להתיישר עם דרישות Backend."

## ציון התאמה למשרה
- One short line with the expected ATS match score out of 100 for this new tailored CV.
- Example: "**ציון משוער: 68/100** — התאמה טובה יותר לדרישות החובה והמילות מפתח במשרה."
- Be realistic. Do NOT invent experience to inflate the score.

---

## קורות החיים המעודכנים

Then the full tailored resume in clean Markdown (name/contact, summary, experience,
skills, education, projects/certifications as applicable).

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
8. The horizontal rule (`---`) MUST appear once between the analysis sections and the
   resume body so clients can copy only the CV section.
9. Keep `cv_markdown` identical to the resume body under "## קורות החיים המעודכנים"
   (without repeating that heading), and keep `estimated_ats_score` consistent with
   the score written in section 2.
"""

HR_SPLIT_RE = re.compile(r"\n---\s*\n", re.MULTILINE)
CV_SECTION_HEADING_RE = re.compile(
    r"^##\s*(?:קורות החיים המעודכנים|The Tailored CV|Tailored CV)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
SCORE_IN_TEXT_RE = re.compile(
    r"(?:ציון(?:\s+משוער)?|score|ATS)[^\d]{0,40}?(\d{1,3})\s*/\s*100",
    re.IGNORECASE,
)


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


def split_tailored_markdown(markdown: str) -> tuple[str, str]:
    """Split full tailor output into (preamble, cv_body).

    Prefers the content after the first horizontal rule (`---`). Falls back to
    the "## קורות החיים המעודכנים" heading, then to the full document.
    """
    text = (markdown or "").strip()
    if not text:
        return "", ""

    parts = HR_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        preamble = parts[0].strip()
        body = parts[1].strip()
        body = CV_SECTION_HEADING_RE.sub("", body, count=1).strip()
        return preamble, body

    heading = CV_SECTION_HEADING_RE.search(text)
    if heading:
        preamble = text[: heading.start()].strip()
        body = text[heading.end() :].strip()
        return preamble, body

    return "", text


def extract_cv_markdown_for_copy(markdown: str) -> str:
    """Return the resume body suitable for clipboard / download of the CV only."""
    _, body = split_tailored_markdown(markdown)
    return body or (markdown or "").strip()


def _clamp_score(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, score))


def _parse_score_from_markdown(markdown: str) -> int | None:
    match = SCORE_IN_TEXT_RE.search(markdown or "")
    if not match:
        return None
    return _clamp_score(match.group(1))


def _string_list(value: Any, *, max_items: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _assemble_structured_markdown(
    *,
    changes_breakdown: list[str],
    estimated_ats_score: int | None,
    cv_markdown: str,
    score_line: str | None = None,
) -> str:
    change_lines = "\n".join(f"- {item}" for item in changes_breakdown) or "- לא צוינו שינויים."
    if score_line:
        score_block = score_line.strip()
    elif estimated_ats_score is not None:
        score_block = f"**ציון משוער: {estimated_ats_score}/100**"
    else:
        score_block = "**ציון משוער:** לא צוין"

    return (
        "## פירוט שינויים\n"
        f"{change_lines}\n\n"
        "## ציון התאמה למשרה\n"
        f"{score_block}\n\n"
        "---\n\n"
        "## קורות החיים המעודכנים\n\n"
        f"{cv_markdown.strip()}\n"
    )


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
    changes = _string_list(
        raw.get("changes_breakdown") or raw.get("highlights"),
        max_items=12,
    )
    caveats = _string_list(raw.get("caveats"), max_items=12)
    estimated = _clamp_score(raw.get("estimated_ats_score"))

    cv_markdown = str(raw.get("cv_markdown") or "").strip()
    markdown = str(raw.get("markdown") or "").strip()

    if not markdown and cv_markdown:
        markdown = _assemble_structured_markdown(
            changes_breakdown=changes,
            estimated_ats_score=estimated,
            cv_markdown=cv_markdown,
        )
    if not markdown:
        raise TailorCvError("OpenAI returned an empty tailored CV", status_code=502)

    # Prefer an explicit cv_markdown; otherwise peel it off the full document.
    if not cv_markdown:
        _, cv_markdown = split_tailored_markdown(markdown)
    if not cv_markdown:
        cv_markdown = markdown

    # Ensure the saved/displayed document always has the analysis + --- + CV layout
    # when we have structured fields (even if the model omitted the HR rule).
    if changes or estimated is not None:
        if "---" not in markdown or "## פירוט שינויים" not in markdown:
            markdown = _assemble_structured_markdown(
                changes_breakdown=changes,
                estimated_ats_score=estimated,
                cv_markdown=cv_markdown,
            )

    if estimated is None:
        estimated = _parse_score_from_markdown(markdown)

    highlights = _string_list(raw.get("highlights"), max_items=12) or changes[:6]

    return {
        "markdown": markdown.strip(),
        "cv_markdown": cv_markdown.strip(),
        "changes_breakdown": changes,
        "estimated_ats_score": estimated,
        "highlights": highlights,
        "caveats": caveats,
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


def _result_from_saved_markdown(markdown: str, *, saved_path: str) -> dict[str, Any]:
    _, cv_body = split_tailored_markdown(markdown)
    return {
        "markdown": markdown,
        "cv_markdown": cv_body or markdown,
        "changes_breakdown": [],
        "estimated_ats_score": _parse_score_from_markdown(markdown),
        "highlights": [],
        "caveats": [],
        "from_cache": True,
        "saved_path": saved_path,
    }


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
            return _result_from_saved_markdown(
                cached, saved_path=str(tailored_cv_path(cv_id, job_id))
            )

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
        "Remember: do not invent experience; keep real job titles unchanged.\n"
        "Return markdown with sections: פירוט שינויים, ציון התאמה למשרה, then ---, "
        "then קורות החיים המעודכנים.\n\n"
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
            cache_namespace=f"tailor_cv_{TAILOR_PROMPT_VERSION}_{cv_id}",
            cache_payload=(
                f"{TAILOR_PROMPT_VERSION}|{cv_id}|{job_id}|"
                f"{job_payload[:2000]}|{cv_payload[:4000]}"
            ),
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
