"""CV tailoring orchestration — single OpenAI workflow."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from ai_client import OpenAIAPIError, call_openai_json, truncate_text
from config import OPENAI_CV_MAX_CHARS, OPENAI_CV_TAILOR_MODEL, OPENAI_JOB_MAX_CHARS
from cv_tailor.models import (
    CandidateFact,
    JobAnalysis,
    RegenerateCvRequest,
    TailoredCvData,
    TailoredCvResult,
)
from cv_tailor.parser import parse_cv_bytes
from cv_tailor.prompt import (
    REGENERATE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_regenerate_user_prompt,
    build_user_prompt,
)
from cv_tailor.renderer import pdf_filename_for_cv, render_tailored_cv_pdf
from cv_tailor.validation import (
    apply_factual_guards,
    parse_llm_response,
    parse_regenerate_response,
    preserve_regeneration_baseline,
)
from pdf_generator_service import PdfGeneratorError

logger = logging.getLogger("cv_tailor.service")

SESSION_TTL = timedelta(hours=1)


@dataclass
class _StoredResult:
    user_id: str
    cv_text: str
    job_description: str
    tailored_cv: TailoredCvData
    job_analysis: JobAnalysis
    user_confirmed_facts: list[CandidateFact] = field(default_factory=list)
    pdf_bytes: bytes = b""
    pdf_filename: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_store: dict[str, _StoredResult] = {}
_store_lock = Lock()


class CvTailorError(RuntimeError):
    """User-facing CV tailor failure."""


def _cleanup_expired() -> None:
    cutoff = datetime.now(timezone.utc) - SESSION_TTL
    expired = [key for key, item in _store.items() if item.created_at < cutoff]
    for key in expired:
        _store.pop(key, None)


def _build_result(
    *,
    result_id: str,
    tailored_cv: TailoredCvData,
    job_analysis: JobAnalysis,
    user_confirmed_facts: list[CandidateFact],
) -> TailoredCvResult:
    return TailoredCvResult(
        result_id=result_id,
        tailored_cv=tailored_cv,
        preview_text=tailored_cv.to_preview_text(),
        model=OPENAI_CV_TAILOR_MODEL,
        job_analysis=job_analysis,
        user_confirmed_facts=user_confirmed_facts,
    )


def _merge_confirmed_facts(
    existing: list[CandidateFact],
    new_facts: list[CandidateFact],
    checkbox_facts: list[CandidateFact],
) -> list[CandidateFact]:
    merged: list[CandidateFact] = list(existing)
    seen = {
        (fact.normalized_fact or fact.fact).strip().lower()
        for fact in existing
        if (fact.normalized_fact or fact.fact).strip()
    }
    for fact in [*checkbox_facts, *new_facts]:
        label = (fact.normalized_fact or fact.fact).strip()
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(fact)
    return merged


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
            cache_namespace="cv_tailor_mvp_v3",
            model=OPENAI_CV_TAILOR_MODEL,
        )
    except OpenAIAPIError as exc:
        logger.error("OpenAI CV tailor request failed: %s", exc)
        raise CvTailorError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected OpenAI CV tailor failure")
        raise CvTailorError("CV tailoring failed. Please try again.") from exc

    tailored_cv, job_analysis = parse_llm_response(raw)
    tailored_cv = apply_factual_guards(
        cv_text,
        tailored_cv,
        job_description=job_description,
        job_analysis=job_analysis,
    )

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
            cv_text=cv_text,
            job_description=job_description,
            tailored_cv=tailored_cv,
            job_analysis=job_analysis,
            user_confirmed_facts=[],
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename,
            created_at=datetime.now(timezone.utc),
        )

    logger.info("CV tailor workflow completed (result_id=%s, model=%s)", result_id, OPENAI_CV_TAILOR_MODEL)
    return _build_result(
        result_id=result_id,
        tailored_cv=tailored_cv,
        job_analysis=job_analysis,
        user_confirmed_facts=[],
    )


def regenerate_tailored_cv(
    *,
    result_id: str,
    user_id: str,
    request: RegenerateCvRequest,
) -> TailoredCvResult:
    """Apply user-confirmed gap information and regenerate the tailored CV."""
    with _store_lock:
        _cleanup_expired()
        stored = _store.get(result_id)

    if stored is None:
        raise CvTailorError("Session expired or not found. Please generate a new tailored CV.")
    if stored.user_id != user_id:
        raise CvTailorError("Session expired or not found. Please generate a new tailored CV.")

    gap_by_id = {gap.gap_id: gap for gap in stored.job_analysis.gaps}
    checkbox_confirmations: list[str] = []
    checkbox_facts: list[CandidateFact] = []
    gap_details: list[tuple[str, str, str]] = []

    for item in request.gap_confirmations:
        gap = gap_by_id.get(item.gap_id)
        if not gap:
            continue
        if item.confirmed and gap.confirmation_text.strip():
            checkbox_confirmations.append(gap.confirmation_text.strip())
            checkbox_facts.append(
                CandidateFact(
                    fact=gap.confirmation_text.strip(),
                    normalized_fact=gap.confirmation_text.strip(),
                    source="user_confirmed",
                    gap_id=gap.gap_id,
                )
            )
        if item.details.strip():
            gap_details.append((gap.gap_id, gap.title or gap.requirement, item.details.strip()))

    general_info = request.general_additional_info.strip()
    if not checkbox_confirmations and not gap_details and not general_info:
        raise CvTailorError("Please confirm at least one gap or add additional information.")

    user_prompt = build_regenerate_user_prompt(
        cv_text=truncate_text(stored.cv_text, OPENAI_CV_MAX_CHARS),
        job_description=truncate_text(stored.job_description, OPENAI_JOB_MAX_CHARS),
        current_tailored_cv=stored.tailored_cv,
        existing_confirmed_facts=stored.user_confirmed_facts,
        checkbox_confirmations=checkbox_confirmations,
        gap_details=gap_details,
        general_additional_info=general_info,
    )

    try:
        raw = call_openai_json(
            REGENERATE_SYSTEM_PROMPT,
            user_prompt,
            use_cache=False,
            cache_namespace="cv_tailor_regen_v2",
            model=OPENAI_CV_TAILOR_MODEL,
        )
    except OpenAIAPIError as exc:
        logger.error("OpenAI CV tailor regenerate failed: %s", exc)
        raise CvTailorError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected OpenAI CV tailor regenerate failure")
        raise CvTailorError("CV regeneration failed. Please try again.") from exc

    tailored_cv, job_analysis, new_facts = parse_regenerate_response(raw)
    if general_info:
        new_facts.append(
            CandidateFact(
                fact=general_info,
                normalized_fact=general_info,
                source="user_confirmed",
                gap_id="general",
            )
        )

    user_confirmed_facts = _merge_confirmed_facts(
        stored.user_confirmed_facts,
        new_facts,
        checkbox_facts,
    )

    tailored_cv = preserve_regeneration_baseline(stored.tailored_cv, tailored_cv)
    tailored_cv = apply_factual_guards(
        stored.cv_text,
        tailored_cv,
        job_description=stored.job_description,
        job_analysis=job_analysis,
        user_confirmed_facts=user_confirmed_facts,
    )

    if not (
        tailored_cv.summary
        or tailored_cv.experience
        or tailored_cv.skills
        or tailored_cv.skill_groups
        or tailored_cv.projects
    ):
        raise CvTailorError("Regenerated CV returned empty content")

    try:
        pdf_bytes = render_tailored_cv_pdf(tailored_cv)
        pdf_filename = pdf_filename_for_cv(tailored_cv)
    except PdfGeneratorError as exc:
        logger.error("PDF generation failed on regenerate: %s", exc)
        raise CvTailorError(str(exc)) from exc
    except Exception as exc:
        logger.exception("PDF generation failed on regenerate")
        raise CvTailorError("Could not generate downloadable CV PDF") from exc

    with _store_lock:
        _store[result_id] = _StoredResult(
            user_id=user_id,
            cv_text=stored.cv_text,
            job_description=stored.job_description,
            tailored_cv=tailored_cv,
            job_analysis=job_analysis,
            user_confirmed_facts=user_confirmed_facts,
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename,
            created_at=stored.created_at,
        )

    logger.info(
        "CV tailor regenerate complete (result_id=%s, gaps=%d, confirmed_facts=%d)",
        result_id,
        len(job_analysis.gaps),
        len(user_confirmed_facts),
    )
    return _build_result(
        result_id=result_id,
        tailored_cv=tailored_cv,
        job_analysis=job_analysis,
        user_confirmed_facts=user_confirmed_facts,
    )


def get_stored_pdf_bytes(*, result_id: str, user_id: str) -> bytes | None:
    """Return PDF bytes for a recent CV Tailor session, if still in memory."""
    with _store_lock:
        _cleanup_expired()
        stored = _store.get(result_id)
    if stored is None or stored.user_id != user_id:
        return None
    return stored.pdf_bytes or None


def get_stored_session_snapshot(*, result_id: str, user_id: str) -> dict[str, Any] | None:
    """Return serializable session fields needed for durable job-history restore."""
    with _store_lock:
        _cleanup_expired()
        stored = _store.get(result_id)
    if stored is None or stored.user_id != user_id:
        return None
    return {
        "cv_text": stored.cv_text,
        "job_description": stored.job_description,
        "tailored_cv": stored.tailored_cv.model_dump(),
        "job_analysis": stored.job_analysis.model_dump(),
        "user_confirmed_facts": [fact.model_dump() for fact in stored.user_confirmed_facts],
        "pdf_bytes": stored.pdf_bytes,
        "pdf_filename": stored.pdf_filename,
    }


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


def store_restored_session(
    *,
    user_id: str,
    cv_text: str,
    job_description: str,
    tailored_cv: TailoredCvData,
    job_analysis: JobAnalysis,
    user_confirmed_facts: list[CandidateFact] | None = None,
    pdf_bytes: bytes | None = None,
    pdf_filename: str | None = None,
) -> TailoredCvResult:
    """Rehydrate a persisted job-history version into an editable in-memory session."""
    facts = list(user_confirmed_facts or [])
    bytes_payload = pdf_bytes or b""
    filename = (pdf_filename or "").strip() or pdf_filename_for_cv(tailored_cv)
    if not bytes_payload:
        try:
            bytes_payload = render_tailored_cv_pdf(tailored_cv)
            filename = pdf_filename_for_cv(tailored_cv)
        except Exception as exc:
            logger.warning("Could not re-render PDF while restoring session: %s", exc)

    result_id = str(uuid.uuid4())
    with _store_lock:
        _cleanup_expired()
        _store[result_id] = _StoredResult(
            user_id=user_id,
            cv_text=cv_text or "",
            job_description=job_description or "",
            tailored_cv=tailored_cv,
            job_analysis=job_analysis,
            user_confirmed_facts=facts,
            pdf_bytes=bytes_payload,
            pdf_filename=filename,
            created_at=datetime.now(timezone.utc),
        )
    return _build_result(
        result_id=result_id,
        tailored_cv=tailored_cv,
        job_analysis=job_analysis,
        user_confirmed_facts=facts,
    )
