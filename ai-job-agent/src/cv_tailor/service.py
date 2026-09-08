"""CV tailoring orchestration — single OpenAI workflow."""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from ai_client import OpenAIAPIError, call_openai_json, truncate_text
from config import (
    OPENAI_CV_MAX_CHARS,
    OPENAI_CV_TAILOR_MODEL,
    OPENAI_CV_TAILOR_REASONING_EFFORT,
    OPENAI_CV_TAILOR_VERBOSITY,
    OPENAI_JOB_MAX_CHARS,
)
from cv_tailor.models import (
    CandidateFact,
    JobAnalysis,
    RegenerateCvRequest,
    TailoredCvData,
    TailoredCvResult,
)
from cv_tailor.parser import CvParseError, parse_cv_bytes
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
# Chromium on small EC2 hosts can hang/OOM after a successful LLM call.
# Generate/regenerate skip PDF entirely; download retries with a hard timeout.
PDF_DOWNLOAD_TIMEOUT_SEC = 45
JOB_TTL = timedelta(hours=1)


def _cv_tailor_llm_kwargs() -> dict[str, Any]:
    """GPT-5 latency knobs for CV Tailor — does not alter prompt text."""
    kwargs: dict[str, Any] = {}
    if OPENAI_CV_TAILOR_REASONING_EFFORT:
        kwargs["reasoning_effort"] = OPENAI_CV_TAILOR_REASONING_EFFORT
    if OPENAI_CV_TAILOR_VERBOSITY:
        kwargs["verbosity"] = OPENAI_CV_TAILOR_VERBOSITY
    return kwargs


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


@dataclass
class _AsyncJob:
    job_id: str
    user_id: str
    kind: str  # generate | regenerate
    status: str = "pending"  # pending | running | done | error
    error: str | None = None
    result: TailoredCvResult | None = None
    saved_to_job: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_store: dict[str, _StoredResult] = {}
_store_lock = Lock()
_jobs: dict[str, _AsyncJob] = {}
_jobs_lock = Lock()
_job_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cv-tailor-job")


class CvTailorError(RuntimeError):
    """User-facing CV tailor failure."""


def _cleanup_store_unlocked() -> None:
    cutoff = datetime.now(timezone.utc) - SESSION_TTL
    expired = [key for key, item in _store.items() if item.created_at < cutoff]
    for key in expired:
        _store.pop(key, None)


def _cleanup_jobs_unlocked() -> None:
    job_cutoff = datetime.now(timezone.utc) - JOB_TTL
    expired_jobs = [key for key, item in _jobs.items() if item.created_at < job_cutoff]
    for key in expired_jobs:
        _jobs.pop(key, None)


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


def _render_pdf_with_timeout(
    tailored_cv: TailoredCvData,
    *,
    timeout_sec: float,
) -> bytes:
    """Run Playwright PDF render in a worker thread with a hard timeout."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cv-tailor-pdf")
    future = pool.submit(render_tailored_cv_pdf, tailored_cv)
    try:
        return future.result(timeout=timeout_sec)
    except FuturesTimeoutError:
        future.cancel()
        raise TimeoutError(f"PDF render exceeded {timeout_sec:.0f}s") from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _render_pdf_best_effort(tailored_cv: TailoredCvData) -> tuple[bytes, str]:
    """Defer Chromium PDF work to download — never block generate/regenerate.

    Long synchronous generate requests fail on mobile networks with HTML/200
    bodies. Skipping Playwright here keeps the LLM path fast and OOM-safe;
    ``get_download_pdf`` still renders with a timeout when the user downloads.
    """
    return b"", pdf_filename_for_cv(tailored_cv)


def _parse_and_guard(
    raw: dict[str, Any],
    *,
    cv_text: str,
    job_description: str,
    user_confirmed_facts: list[CandidateFact] | None = None,
    regenerate: bool = False,
) -> tuple[TailoredCvData, JobAnalysis, list[CandidateFact]]:
    """Parse LLM JSON + factual guards; map unexpected crashes to CvTailorError."""
    try:
        if regenerate:
            tailored_cv, job_analysis, new_facts = parse_regenerate_response(raw)
        else:
            tailored_cv, job_analysis = parse_llm_response(raw)
            new_facts = []
        tailored_cv = apply_factual_guards(
            cv_text,
            tailored_cv,
            job_description=job_description,
            job_analysis=job_analysis,
            user_confirmed_facts=user_confirmed_facts,
        )
    except CvTailorError:
        raise
    except Exception as exc:
        logger.exception("Failed to parse/guard CV tailor LLM response")
        raise CvTailorError(
            "תשובת ה-AI לא הייתה תקינה — נסה שוב בעוד רגע."
        ) from exc
    return tailored_cv, job_analysis, new_facts


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
        started = datetime.now(timezone.utc)
        raw = call_openai_json(
            SYSTEM_PROMPT,
            user_prompt,
            use_cache=False,
            cache_namespace="cv_tailor_mvp_v3",
            model=OPENAI_CV_TAILOR_MODEL,
            **_cv_tailor_llm_kwargs(),
        )
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        usage = raw.get("_usage") if isinstance(raw, dict) else None
        logger.info(
            "CV tailor OpenAI call finished (model=%s, elapsed_ms=%d, usage=%s, effort=%s)",
            OPENAI_CV_TAILOR_MODEL,
            elapsed_ms,
            usage,
            OPENAI_CV_TAILOR_REASONING_EFFORT or "default",
        )
    except OpenAIAPIError as exc:
        logger.error("OpenAI CV tailor request failed: %s", exc)
        raise CvTailorError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected OpenAI CV tailor failure")
        raise CvTailorError("CV tailoring failed. Please try again.") from exc

    tailored_cv, job_analysis, _ = _parse_and_guard(
        raw,
        cv_text=cv_text,
        job_description=job_description,
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

    pdf_bytes, pdf_filename = _render_pdf_best_effort(tailored_cv)

    result_id = str(uuid.uuid4())
    with _store_lock:
        _cleanup_store_unlocked()
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
        _cleanup_store_unlocked()
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
        started = datetime.now(timezone.utc)
        raw = call_openai_json(
            REGENERATE_SYSTEM_PROMPT,
            user_prompt,
            use_cache=False,
            cache_namespace="cv_tailor_regen_v2",
            model=OPENAI_CV_TAILOR_MODEL,
            **_cv_tailor_llm_kwargs(),
        )
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        usage = raw.get("_usage") if isinstance(raw, dict) else None
        logger.info(
            "CV tailor regenerate OpenAI call finished (model=%s, elapsed_ms=%d, usage=%s, effort=%s)",
            OPENAI_CV_TAILOR_MODEL,
            elapsed_ms,
            usage,
            OPENAI_CV_TAILOR_REASONING_EFFORT or "default",
        )
    except OpenAIAPIError as exc:
        logger.error("OpenAI CV tailor regenerate failed: %s", exc)
        raise CvTailorError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected OpenAI CV tailor regenerate failure")
        raise CvTailorError("CV regeneration failed. Please try again.") from exc

    try:
        tailored_cv, job_analysis, new_facts = parse_regenerate_response(raw)
    except Exception as exc:
        logger.exception("Failed to parse CV tailor regenerate response")
        raise CvTailorError(
            "תשובת ה-AI לא הייתה תקינה — נסה שוב בעוד רגע."
        ) from exc

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

    try:
        tailored_cv = preserve_regeneration_baseline(stored.tailored_cv, tailored_cv)
        tailored_cv = apply_factual_guards(
            stored.cv_text,
            tailored_cv,
            job_description=stored.job_description,
            job_analysis=job_analysis,
            user_confirmed_facts=user_confirmed_facts,
        )
    except Exception as exc:
        logger.exception("Failed to guard CV tailor regenerate response")
        raise CvTailorError(
            "תשובת ה-AI לא הייתה תקינה — נסה שוב בעוד רגע."
        ) from exc

    if not (
        tailored_cv.summary
        or tailored_cv.experience
        or tailored_cv.skills
        or tailored_cv.skill_groups
        or tailored_cv.projects
    ):
        raise CvTailorError("Regenerated CV returned empty content")

    pdf_bytes, pdf_filename = _render_pdf_best_effort(tailored_cv)

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


def _result_payload(
    result: TailoredCvResult,
    *,
    saved_to_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "result_id": result.result_id,
        "model": result.model,
        "preview_text": result.preview_text,
        "tailored_cv": result.tailored_cv.model_dump(),
        "job_analysis": result.job_analysis.model_dump(),
        "user_confirmed_facts": [fact.model_dump() for fact in result.user_confirmed_facts],
        "saved_to_job": saved_to_job is not None,
        "job_version_id": (saved_to_job or {}).get("version_id"),
    }


def _set_job_status(job_id: str, **patch: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        for key, value in patch.items():
            setattr(job, key, value)


def _run_generate_job(
    *,
    job_id: str,
    file_bytes: bytes,
    filename: str,
    job_description: str,
    user_id: str,
    cv_id: str | None,
    link_job_id: int | None,
) -> None:
    from cv_tailor.job_persist import maybe_persist_tailored_cv_to_job

    _set_job_status(job_id, status="running", error=None)
    try:
        result = generate_tailored_cv(
            file_bytes=file_bytes,
            filename=filename,
            job_description=job_description,
            user_id=user_id,
        )
        # Mark done before durable persist so the UI unblocks immediately.
        _set_job_status(job_id, status="done", result=result, saved_to_job=None, error=None)
        saved = maybe_persist_tailored_cv_to_job(
            cv_id=cv_id,
            job_id=link_job_id,
            preview_text=result.preview_text,
            user_id=user_id,
            pdf_bytes=get_stored_pdf_bytes(result_id=result.result_id, user_id=user_id),
            tailored_cv=result.tailored_cv.model_dump(),
            job_analysis=result.job_analysis.model_dump(),
            user_confirmed_facts=[fact.model_dump() for fact in result.user_confirmed_facts],
            cv_text=(get_stored_session_snapshot(result_id=result.result_id, user_id=user_id) or {}).get(
                "cv_text"
            ),
            model=result.model,
        )
        if saved is not None:
            _set_job_status(job_id, status="done", result=result, saved_to_job=saved, error=None)
    except (CvTailorError, CvParseError) as exc:
        logger.warning("Async CV tailor generate failed: %s", exc)
        _set_job_status(job_id, status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected async CV tailor generate failure")
        detail = str(exc).strip() or exc.__class__.__name__
        _set_job_status(
            job_id,
            status="error",
            error=f"יצירת קורות חיים מותאמים נכשלה: {detail}",
        )


def _run_regenerate_job(
    *,
    job_id: str,
    result_id: str,
    user_id: str,
    request: RegenerateCvRequest,
) -> None:
    from cv_tailor.job_persist import maybe_persist_tailored_cv_to_job

    _set_job_status(job_id, status="running", error=None)
    try:
        result = regenerate_tailored_cv(
            result_id=result_id,
            user_id=user_id,
            request=request,
        )
        snapshot = get_stored_session_snapshot(result_id=result.result_id, user_id=user_id) or {}
        _set_job_status(job_id, status="done", result=result, saved_to_job=None, error=None)
        saved = maybe_persist_tailored_cv_to_job(
            cv_id=request.cv_id,
            job_id=request.job_id,
            preview_text=result.preview_text,
            user_id=user_id,
            pdf_bytes=get_stored_pdf_bytes(result_id=result.result_id, user_id=user_id),
            tailored_cv=result.tailored_cv.model_dump(),
            job_analysis=result.job_analysis.model_dump(),
            user_confirmed_facts=[fact.model_dump() for fact in result.user_confirmed_facts],
            cv_text=snapshot.get("cv_text"),
            model=result.model,
        )
        if saved is not None:
            _set_job_status(job_id, status="done", result=result, saved_to_job=saved, error=None)
    except CvTailorError as exc:
        logger.warning("Async CV tailor regenerate failed: %s", exc)
        _set_job_status(job_id, status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected async CV tailor regenerate failure")
        detail = str(exc).strip() or exc.__class__.__name__
        _set_job_status(
            job_id,
            status="error",
            error=f"עדכון קורות החיים נכשל: {detail}",
        )


def start_generate_job(
    *,
    file_bytes: bytes,
    filename: str,
    job_description: str,
    user_id: str,
    cv_id: str | None = None,
    link_job_id: int | None = None,
) -> str:
    """Enqueue generate work and return a pollable job id immediately."""
    # Fail fast on trivial validation before accepting the upload.
    if len((job_description or "").strip()) < 20:
        raise CvTailorError("Job description is too short")
    if not file_bytes:
        raise CvParseError("הקובץ שהועלה ריק")

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _cleanup_jobs_unlocked()
        _jobs[job_id] = _AsyncJob(
            job_id=job_id,
            user_id=user_id,
            kind="generate",
            status="pending",
        )
    _job_pool.submit(
        _run_generate_job,
        job_id=job_id,
        file_bytes=file_bytes,
        filename=filename,
        job_description=job_description,
        user_id=user_id,
        cv_id=cv_id,
        link_job_id=link_job_id,
    )
    return job_id


def start_regenerate_job(
    *,
    result_id: str,
    user_id: str,
    request: RegenerateCvRequest,
) -> str:
    """Enqueue regenerate work and return a pollable job id immediately."""
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _cleanup_jobs_unlocked()
        _jobs[job_id] = _AsyncJob(
            job_id=job_id,
            user_id=user_id,
            kind="regenerate",
            status="pending",
        )
    _job_pool.submit(
        _run_regenerate_job,
        job_id=job_id,
        result_id=result_id,
        user_id=user_id,
        request=request,
    )
    return job_id


def get_job_status(*, job_id: str, user_id: str) -> dict[str, Any]:
    """Return poll payload for an async CV tailor job."""
    with _jobs_lock:
        _cleanup_jobs_unlocked()
        job = _jobs.get(job_id)
        if job is None or job.user_id != user_id:
            raise CvTailorError("המשימה לא נמצאה או שפג תוקפה — נסה ליצור מחדש.")
        status = job.status
        error = job.error
        result = job.result
        saved = job.saved_to_job

    payload: dict[str, Any] = {"job_id": job_id, "status": status}
    if status == "error":
        payload["error"] = error or "יצירת קורות חיים מותאמים נכשלה"
        return payload
    if status == "done" and result is not None:
        payload.update(_result_payload(result, saved_to_job=saved))
    return payload


def get_stored_pdf_bytes(*, result_id: str, user_id: str) -> bytes | None:
    """Return PDF bytes for a recent CV Tailor session, if still in memory."""
    with _store_lock:
        _cleanup_store_unlocked()
        stored = _store.get(result_id)
    if stored is None or stored.user_id != user_id:
        return None
    return stored.pdf_bytes or None


def get_stored_session_snapshot(*, result_id: str, user_id: str) -> dict[str, Any] | None:
    """Return serializable session fields needed for durable job-history restore."""
    with _store_lock:
        _cleanup_store_unlocked()
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
    """Return PDF bytes and filename for a stored result.

    If generate/regenerate skipped PDF (Playwright failure), retry rendering now.
    """
    with _store_lock:
        _cleanup_store_unlocked()
        stored = _store.get(result_id)

    if stored is None:
        raise CvTailorError("Download link expired or not found")
    if stored.user_id != user_id:
        raise CvTailorError("Download link expired or not found")

    if stored.pdf_bytes:
        return stored.pdf_bytes, stored.pdf_filename or pdf_filename_for_cv(stored.tailored_cv)

    try:
        pdf_bytes = _render_pdf_with_timeout(
            stored.tailored_cv, timeout_sec=PDF_DOWNLOAD_TIMEOUT_SEC
        )
        pdf_filename = pdf_filename_for_cv(stored.tailored_cv)
    except TimeoutError as exc:
        logger.error("PDF re-render for download timed out: %s", exc)
        raise CvTailorError(
            "יצירת ה-PDF לקחה יותר מדי זמן — נסה שוב בעוד רגע."
        ) from exc
    except PdfGeneratorError as exc:
        logger.error("PDF re-render for download failed: %s", exc)
        raise CvTailorError(str(exc)) from exc
    except Exception as exc:
        logger.exception("PDF re-render for download failed")
        raise CvTailorError("Could not generate downloadable CV PDF") from exc

    with _store_lock:
        current = _store.get(result_id)
        if current is not None and current.user_id == user_id:
            current.pdf_bytes = pdf_bytes
            current.pdf_filename = pdf_filename

    return pdf_bytes, pdf_filename


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
            bytes_payload = _render_pdf_with_timeout(
                tailored_cv, timeout_sec=PDF_DOWNLOAD_TIMEOUT_SEC
            )
            filename = pdf_filename_for_cv(tailored_cv)
        except Exception as exc:
            logger.warning("Could not re-render PDF while restoring session: %s", exc)

    result_id = str(uuid.uuid4())
    with _store_lock:
        _cleanup_store_unlocked()
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
