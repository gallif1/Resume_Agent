"""Bridge CV Tailor MVP output into per-job tailored-CV history."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from config import OPENAI_CV_TAILOR_MODEL, cv_db_path
from cv_tailor.models import CandidateFact, JobAnalysis, TailoredCvData
from cv_tailor.renderer import pdf_filename_for_cv
from cv_tailor.service import store_restored_session
from tailor_cv_service import (
    TailorCvError,
    load_saved_tailored_cv_pdf,
    load_tailored_cv_version,
    persist_mvp_tailored_cv_for_user,
    tailored_cv_path,
    tailored_cv_pdf_path,
    tailored_cv_version_path,
    tailored_cv_version_pdf_path,
)

logger = logging.getLogger("cv_tailor.job_persist")


def maybe_persist_tailored_cv_to_job(
    *,
    cv_id: str | None,
    job_id: int | None,
    preview_text: str,
    user_id: str,
    pdf_bytes: bytes | None = None,
    tailored_cv: dict[str, Any] | None = None,
    job_analysis: dict[str, Any] | None = None,
    user_confirmed_facts: list[dict[str, Any]] | None = None,
    cv_text: str | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """Save MVP output to job history when cv/job ids are provided."""
    if not cv_id or job_id is None:
        return None
    markdown = (preview_text or "").strip()
    if not markdown:
        return None
    try:
        return persist_mvp_tailored_cv_for_user(
            cv_id,
            int(job_id),
            markdown,
            user_id=user_id,
            pdf_bytes=pdf_bytes,
            mvp_tailored_cv=tailored_cv,
            mvp_job_analysis=job_analysis,
            mvp_user_confirmed_facts=user_confirmed_facts,
            mvp_cv_text=cv_text,
            mvp_model=model,
        )
    except TailorCvError as exc:
        logger.warning(
            "Failed to persist CV tailor output for cv=%s job=%s: %s",
            cv_id,
            job_id,
            exc.message,
        )
        return None
    except Exception:
        logger.exception(
            "Unexpected failure persisting CV tailor output for cv=%s job=%s",
            cv_id,
            job_id,
        )
        return None


def _parse_tailored_cv(payload: dict[str, Any] | None) -> TailoredCvData:
    data = payload if isinstance(payload, dict) else {}
    try:
        return TailoredCvData.model_validate(data)
    except Exception:
        return TailoredCvData.from_llm_dict(data)


def _parse_job_analysis(payload: dict[str, Any] | None) -> JobAnalysis:
    data = payload if isinstance(payload, dict) else {}
    try:
        return JobAnalysis.from_llm_dict(data)
    except Exception:
        return JobAnalysis()


def _parse_facts(payload: Any) -> list[CandidateFact]:
    if not isinstance(payload, list):
        return []
    facts: list[CandidateFact] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            facts.append(CandidateFact.model_validate(item))
        except Exception:
            continue
    return facts


def _load_cv_text_fallback(cv_id: str) -> str:
    try:
        from tailor_cv_service import _load_source_cv_raw_text

        return (_load_source_cv_raw_text(cv_id) or "").strip()
    except Exception:
        return ""


def restore_mvp_session_for_user(
    *,
    cv_id: str,
    job_id: int,
    user_id: str,
    version_id: int | None = None,
) -> dict[str, Any]:
    """Rebuild an editable CV Tailor session from a saved job-history version."""
    import db as db_module

    db_module.ensure_multi_cv_storage()
    cv = db_module.get_cv(cv_id, db_path=db_module.REGISTRY_DB_PATH)
    if cv is None or cv.get("user_id") != user_id:
        raise TailorCvError("קורות חיים לא נמצאו", status_code=404)

    cv_db = cv_db_path(cv_id)
    db_module.init_db(cv_db)
    job = db_module.get_job_by_id(job_id, db_path=cv_db)
    if job is None:
        raise TailorCvError("משרה לא נמצאה", status_code=404)

    if version_id is not None:
        version_row = db_module.get_cv_tailor_version_by_id(version_id, db_path=cv_db)
        if (
            version_row is None
            or str(version_row.get("cv_id")) != cv_id
            or int(version_row.get("job_id") or 0) != int(job_id)
        ):
            raise TailorCvError("גרסת קורות חיים לא נמצאה", status_code=404)
    else:
        version_row = db_module.get_latest_cv_tailor_version(
            cv_id, job_id, db_path=cv_db
        )
        version_id = int(version_row["id"]) if version_row else None

    report_row = db_module.get_tailored_resume_report(
        cv_id=cv_id,
        job_id=job_id,
        version_id=version_id,
        db_path=cv_db,
    )
    report = (report_row or {}).get("report") or {}

    markdown = ""
    if version_id is not None:
        try:
            markdown = load_tailored_cv_version(
                cv_id, job_id, version_id, db_path=cv_db
            )
        except Exception:
            markdown = ""
    if not markdown.strip():
        from tailor_cv_service import load_saved_tailored_cv

        markdown = load_saved_tailored_cv(cv_id, job_id) or ""
    if not markdown.strip() and isinstance(report.get("preview_text"), str):
        markdown = str(report.get("preview_text") or "")
    if not markdown.strip():
        raise TailorCvError("לא נמצאה תוצאת התאמה שמורה", status_code=404)

    tailored_payload = report.get("tailored_cv") or report.get("tailored_resume")
    if isinstance(tailored_payload, dict) and tailored_payload:
        tailored_cv = _parse_tailored_cv(tailored_payload)
    else:
        tailored_cv = TailoredCvData(summary=markdown.strip()[:2000])

    job_analysis = _parse_job_analysis(
        report.get("job_analysis") if isinstance(report.get("job_analysis"), dict) else {}
    )
    facts = _parse_facts(report.get("user_confirmed_facts"))
    cv_text = str(report.get("cv_text") or "").strip() or _load_cv_text_fallback(cv_id)
    job_description = str(job.get("description") or "").strip()
    model = str(report.get("model") or "").strip() or OPENAI_CV_TAILOR_MODEL

    pdf_bytes = load_saved_tailored_cv_pdf(
        cv_id, job_id, version_id=version_id
    )
    result = store_restored_session(
        user_id=user_id,
        cv_text=cv_text,
        job_description=job_description,
        tailored_cv=tailored_cv,
        job_analysis=job_analysis,
        user_confirmed_facts=facts,
        pdf_bytes=pdf_bytes,
        pdf_filename=pdf_filename_for_cv(tailored_cv),
    )

    return {
        "result_id": result.result_id,
        "model": model,
        "preview_text": result.preview_text or markdown.strip(),
        "tailored_cv": result.tailored_cv.model_dump(),
        "job_analysis": result.job_analysis.model_dump(),
        "user_confirmed_facts": [fact.model_dump() for fact in result.user_confirmed_facts],
        "saved_to_job": True,
        "job_version_id": version_id,
        "restored": True,
    }


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.warning("Could not delete tailored CV file: %s", path)


def delete_tailored_cv_version_for_user(
    *,
    cv_id: str,
    job_id: int,
    version_id: int,
    user_id: str,
) -> dict[str, Any]:
    """Delete one tailored-CV history entry and keep match pointers consistent."""
    import db as db_module

    db_module.ensure_multi_cv_storage()
    cv = db_module.get_cv(cv_id, db_path=db_module.REGISTRY_DB_PATH)
    if cv is None or cv.get("user_id") != user_id:
        raise TailorCvError("קורות חיים לא נמצאו", status_code=404)

    cv_db = cv_db_path(cv_id)
    db_module.init_db(cv_db)
    version_row = db_module.get_cv_tailor_version_by_id(version_id, db_path=cv_db)
    if (
        version_row is None
        or str(version_row.get("cv_id")) != cv_id
        or int(version_row.get("job_id") or 0) != int(job_id)
    ):
        raise TailorCvError("גרסת קורות חיים לא נמצאה", status_code=404)

    was_latest = False
    latest = db_module.get_latest_cv_tailor_version(cv_id, job_id, db_path=cv_db)
    if latest and int(latest.get("id") or 0) == int(version_id):
        was_latest = True

    archive_md = tailored_cv_version_path(cv_id, job_id, version_id)
    archive_pdf = tailored_cv_version_pdf_path(cv_id, job_id, version_id)
    path_from_row = str(version_row.get("tailored_cv_path") or "").strip()
    if path_from_row:
        candidate = Path(path_from_row)
        if candidate.exists() and candidate != archive_md:
            _safe_unlink(candidate)

    db_module.delete_tailored_resume_reports_for_version(
        cv_id, job_id, version_id, db_path=cv_db
    )
    deleted = db_module.delete_cv_tailor_version(version_id, db_path=cv_db)
    if not deleted:
        raise TailorCvError("גרסת קורות חיים לא נמצאה", status_code=404)

    _safe_unlink(archive_md)
    _safe_unlink(archive_pdf)

    remaining = db_module.list_cv_tailor_versions(cv_id, job_id, db_path=cv_db)
    if not remaining:
        _safe_unlink(tailored_cv_path(cv_id, job_id))
        _safe_unlink(tailored_cv_pdf_path(cv_id, job_id))
        db_module.clear_cv_match_tailored(cv_id, job_id, db_path=cv_db)
        return {
            "deleted": True,
            "version_id": version_id,
            "remaining_count": 0,
            "has_tailored_cv": False,
        }

    if was_latest:
        next_latest = remaining[0]
        next_id = int(next_latest["id"])
        next_md = tailored_cv_version_path(cv_id, job_id, next_id)
        next_pdf = tailored_cv_version_pdf_path(cv_id, job_id, next_id)
        latest_md = tailored_cv_path(cv_id, job_id)
        latest_pdf = tailored_cv_pdf_path(cv_id, job_id)
        if next_md.exists():
            latest_md.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(next_md, latest_md)
        if next_pdf.exists():
            latest_pdf.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(next_pdf, latest_pdf)
        relative = f"data/cvs/{cv_id}/tailored_cvs/{job_id}.md"
        db_module.mark_cv_match_tailored(
            cv_id,
            job_id,
            tailored_cv_path=relative,
            db_path=cv_db,
        )

    return {
        "deleted": True,
        "version_id": version_id,
        "remaining_count": len(remaining),
        "has_tailored_cv": True,
        "latest_version_id": int(remaining[0]["id"]),
    }
