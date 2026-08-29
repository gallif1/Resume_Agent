"""CV tailoring orchestration — single OpenAI workflow."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock

from ai_client import OpenAIAPIError, call_openai_json, truncate_text
from config import OPENAI_CV_MAX_CHARS, OPENAI_CV_TAILOR_MODEL, OPENAI_JOB_MAX_CHARS
from cv_tailor.models import JobAnalysis, TailoredCvData, TailoredCvResult
from cv_tailor.parser import parse_cv_bytes
from cv_tailor.prompt import SYSTEM_PROMPT, build_user_prompt
from cv_tailor.renderer import pdf_filename_for_cv, render_tailored_cv_pdf
from cv_tailor.validation import apply_factual_guards, parse_llm_response
from pdf_generator_service import PdfGeneratorError

logger = logging.getLogger("cv_tailor.service")

SESSION_TTL = timedelta(hours=1)


@dataclass
class _StoredResult:
    user_id: str
    tailored_cv: TailoredCvData
    job_analysis: JobAnalysis
    pdf_bytes: bytes
    pdf_filename: str
    created_at: datetime


_store: dict[str, _StoredResult] = {}
_store_lock = Lock()


class CvTailorError(RuntimeError):
    """User-facing CV tailor failure."""


def _cleanup_expired() -> None:
    cutoff = datetime.now(timezone.utc) - SESSION_TTL
    expired = [key for key, item in _store.items() if item.created_at < cutoff]
    for key in expired:
        _store.pop(key, None)


def generate_tailored_cv(
    *,
    file_bytes: bytes,
    filename: str,
    job_description: str,
    user_id: str,
) -> TailoredCvResult:
    """Parse CV, call OpenAI once, apply factual guards, store PDF for download."""
    job_description = (job_description or "").strip()
    if len(job_description) < 20:
        raise CvTailorError("Job description is too short")

    cv_text, source = parse_cv_bytes(file_bytes, filename)
    logger.info("Starting CV tailor workflow (cv_source=%s, user=%s)", source, user_id)

    user_prompt = build_user_prompt(
        cv_text=truncate_text(cv_text, OPENAI_CV_MAX_CHARS),
        job_description=truncate_text(job_description, OPENAI_JOB_MAX_CHARS),
    )

    try:
        raw = call_openai_json(
            SYSTEM_PROMPT,
            user_prompt,
            use_cache=False,
            cache_namespace="cv_tailor_mvp_v2",
            model=OPENAI_CV_TAILOR_MODEL,
        )
    except OpenAIAPIError as exc:
        logger.error("OpenAI CV tailor request failed: %s", exc)
        raise CvTailorError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected OpenAI CV tailor failure")
        raise CvTailorError("CV tailoring failed. Please try again.") from exc

    tailored_cv, job_analysis = parse_llm_response(raw)
    tailored_cv = apply_factual_guards(cv_text, tailored_cv)

    if not (
        tailored_cv.summary
        or tailored_cv.experience
        or tailored_cv.skills
        or tailored_cv.skill_groups
        or tailored_cv.projects
    ):
        logger.error("OpenAI returned empty tailored CV structure")
        raise CvTailorError("Tailored CV generation returned empty content")

    logger.info(
        "CV tailor analysis complete (strong_matches=%d, gaps=%d)",
        len(job_analysis.strong_matches),
        len(job_analysis.gaps),
    )

    try:
        pdf_bytes = render_tailored_cv_pdf(tailored_cv)
        pdf_filename = pdf_filename_for_cv(tailored_cv)
    except PdfGeneratorError as exc:
        logger.error("PDF generation failed: %s", exc)
        raise CvTailorError(str(exc)) from exc
    except Exception as exc:
        logger.exception("PDF generation failed")
        raise CvTailorError("Could not generate downloadable CV PDF") from exc

    result_id = str(uuid.uuid4())
    with _store_lock:
        _cleanup_expired()
        _store[result_id] = _StoredResult(
            user_id=user_id,
            tailored_cv=tailored_cv,
            job_analysis=job_analysis,
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename,
            created_at=datetime.now(timezone.utc),
        )

    logger.info("CV tailor workflow completed (result_id=%s, model=%s)", result_id, OPENAI_CV_TAILOR_MODEL)
    result = TailoredCvResult(
        result_id=result_id,
        tailored_cv=tailored_cv,
        preview_text=tailored_cv.to_preview_text(),
        model=OPENAI_CV_TAILOR_MODEL,
        job_analysis=job_analysis,
    )
    return result


def get_download_pdf(*, result_id: str, user_id: str) -> tuple[bytes, str]:
    """Return PDF bytes and filename for a stored result."""
    with _store_lock:
        _cleanup_expired()
        stored = _store.get(result_id)

    if stored is None:
        raise CvTailorError("Download link expired or not found")
    if stored.user_id != user_id:
        raise CvTailorError("Download link expired or not found")

    return stored.pdf_bytes, stored.pdf_filename
