"""Background worker that runs Playwright job application automation.

Applications run in a separate subprocess (not a thread) so Playwright's sync API
does not conflict with FastAPI/uvicorn's asyncio event loop.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

import db
from application_providers import select_provider
from application_providers.provider_utils import hebrew_failure_message
from application_service import (
    build_user_profile,
    resolve_cover_letter,
    resolve_cv_file_path,
)
from browser_utils import create_browser_context, format_browser_launch_error, page_looks_blocked
from config import APPLY_HEADLESS, AUTO_SUBMIT, LOGS_DIR, PROJECT_ROOT

SRC = PROJECT_ROOT / "src"
PYTHON = sys.executable

_worker_lock = threading.Lock()
_active_applications: set[str] = set()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_step(
    application_id: str,
    step_name: str,
    status: str,
    message: str | None,
    db_path: Path,
) -> None:
    db.add_job_application_step(
        application_id, step_name, status, message=message, db_path=db_path
    )


def _save_debug_screenshot(page: Page, application_id: str, tag: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = LOGS_DIR / f"job_app_{application_id}_{tag}_{stamp}.png"
        page.screenshot(path=str(path))
    except Exception:
        pass


def _classify_worker_error(exc: Exception) -> tuple[str, str, str]:
    """Return (failure_category, user_message, step_name) for an unexpected error."""
    text = str(exc)
    if isinstance(exc, RuntimeError) or "Executable doesn't exist" in text:
        return (
            "website_blocked_automation",
            format_browser_launch_error(exc),
            "opening_job_page",
        )
    if isinstance(exc, PlaywrightTimeoutError):
        return ("network_error", hebrew_failure_message("network_error"), "opening_job_page")
    if "cannot switch to a different thread" in text.lower() or "sync api inside" in text.lower():
        return (
            "website_blocked_automation",
            "שגיאת תזמון בדפדפן השרת. נסה שוב בעוד רגע.",
            "opening_job_page",
        )
    return (
        "unexpected_error",
        hebrew_failure_message("unexpected_error"),
        "opening_job_page",
    )


def _finalize_application(
    application_id: str,
    status: str,
    *,
    failure_reason: str | None = None,
    failure_category: str | None = None,
    requires_user_action_reason: str | None = None,
    confirmation_text: str | None = None,
    confirmation_url: str | None = None,
    current_url: str | None = None,
    provider_name: str | None = None,
    db_path: Path,
) -> None:
    now = _utc_now()
    fields: dict[str, Any] = {
        "status": status,
        "completed_at": now,
        "failure_reason": failure_reason,
        "failure_category": failure_category,
        "requires_user_action_reason": requires_user_action_reason,
        "external_confirmation_text": confirmation_text,
        "external_confirmation_url": confirmation_url,
        "current_step_url": current_url,
        "provider_name": provider_name,
    }
    if status == db.JOB_APP_SUBMITTED:
        fields["submitted_at"] = now
    db.update_job_application(application_id, fields, db_path=db_path)


def run_application_attempt(
    application_id: str,
    cv_id: str,
    job_id: int,
    *,
    db_path: Path,
) -> None:
    """Execute one application attempt synchronously (subprocess entry point)."""
    app = db.get_job_application(application_id, db_path=db_path)
    if app is None:
        return

    job = db.get_job_by_id(job_id, db_path=db_path)
    if job is None:
        _finalize_application(
            application_id,
            db.JOB_APP_FAILED,
            failure_reason=hebrew_failure_message("job_page_unavailable"),
            failure_category="job_page_unavailable",
            db_path=db_path,
        )
        return

    db.update_job_application(
        application_id,
        {"status": db.JOB_APP_IN_PROGRESS, "started_at": _utc_now()},
        db_path=db_path,
    )

    try:
        profile = build_user_profile(cv_id)
        cv_path = resolve_cv_file_path(cv_id)
        cover_letter = resolve_cover_letter(cv_id, profile, job)
    except Exception as exc:
        _finalize_application(
            application_id,
            db.JOB_APP_FAILED,
            failure_reason=str(exc),
            failure_category="unexpected_error",
            db_path=db_path,
        )
        return

    url = job.get("job_url") or ""

    try:
        with sync_playwright() as playwright:
            context, page = create_browser_context(playwright, headless=APPLY_HEADLESS)
            try:
                _run_on_page(
                    page,
                    application_id,
                    url,
                    profile,
                    str(cv_path),
                    job,
                    cover_letter,
                    db_path,
                )
            finally:
                context.close()
    except Exception as exc:
        category, message, step = _classify_worker_error(exc)
        _log_step(
            application_id,
            step,
            db.STEP_FAILED,
            f"{type(exc).__name__}: {str(exc)[:180]}",
            db_path,
        )
        _finalize_application(
            application_id,
            db.JOB_APP_FAILED,
            failure_reason=message,
            failure_category=category,
            db_path=db_path,
        )


def _run_on_page(
    page: Page,
    application_id: str,
    url: str,
    profile: dict[str, Any],
    cv_file_path: str,
    job: dict[str, Any],
    cover_letter: str | None,
    db_path: Path,
) -> None:
    # Step: opening_job_page
    _log_step(application_id, "opening_job_page", db.STEP_SUCCESS, "Navigating", db_path)
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        status_code = response.status if response is not None else 0
        if status_code >= 400:
            _log_step(
                application_id,
                "opening_job_page",
                db.STEP_FAILED,
                f"HTTP {status_code}",
                db_path,
            )
            _finalize_application(
                application_id,
                db.JOB_APP_FAILED,
                failure_reason=hebrew_failure_message("job_page_unavailable"),
                failure_category="job_page_unavailable",
                current_url=page.url,
                db_path=db_path,
            )
            _save_debug_screenshot(page, application_id, "page_unavailable")
            return
    except PlaywrightTimeoutError:
        _finalize_application(
            application_id,
            db.JOB_APP_FAILED,
            failure_reason=hebrew_failure_message("network_error"),
            failure_category="network_error",
            db_path=db_path,
        )
        return

    if page_looks_blocked(page):
        _finalize_application(
            application_id,
            db.JOB_APP_REQUIRES_USER_ACTION,
            failure_reason=hebrew_failure_message("website_blocked_automation"),
            failure_category="website_blocked_automation",
            requires_user_action_reason=hebrew_failure_message("website_blocked_automation"),
            current_url=page.url,
            db_path=db_path,
        )
        _save_debug_screenshot(page, application_id, "blocked")
        return

    # Step: detecting_application_provider
    provider = select_provider(url, page)
    provider_name = provider.name
    db.update_job_application(
        application_id, {"provider_name": provider_name}, db_path=db_path
    )
    _log_step(
        application_id,
        "detecting_application_provider",
        db.STEP_SUCCESS,
        provider_name,
        db_path,
    )

    # Step: opening_application_form + filling
    fill_result = provider.fill_application(
        page, profile, cv_file_path, job, cover_letter=cover_letter
    )
    _log_step(
        application_id,
        "opening_application_form",
        db.STEP_SUCCESS if fill_result.success else db.STEP_FAILED,
        fill_result.message,
        db_path,
    )

    if fill_result.filled_fields:
        _log_step(
            application_id,
            "filling_personal_details",
            db.STEP_SUCCESS,
            ", ".join(fill_result.filled_fields[:15]),
            db_path,
        )
    if cv_file_path:
        _log_step(
            application_id,
            "uploading_cv",
            db.STEP_SUCCESS if fill_result.success else db.STEP_SKIPPED,
            "CV upload attempted",
            db_path,
        )

    if fill_result.status == db.JOB_APP_SUBMITTED:
        _finalize_application(
            application_id,
            db.JOB_APP_SUBMITTED,
            confirmation_text=fill_result.confirmation_text,
            confirmation_url=fill_result.confirmation_url or page.url,
            current_url=page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        _log_step(application_id, "verifying_submission", db.STEP_SUCCESS, "Confirmed", db_path)
        _sync_match_sent(application_id, db_path)
        return

    if fill_result.status == "requires_user_action":
        _finalize_application(
            application_id,
            db.JOB_APP_REQUIRES_USER_ACTION,
            failure_reason=fill_result.message,
            failure_category=fill_result.failure_category,
            requires_user_action_reason=fill_result.message,
            current_url=fill_result.current_url or page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        _log_step(
            application_id,
            "answering_questions",
            db.STEP_REQUIRES_USER_ACTION,
            fill_result.message,
            db_path,
        )
        _save_debug_screenshot(page, application_id, "user_action")
        return

    if not fill_result.success:
        _finalize_application(
            application_id,
            db.JOB_APP_FAILED,
            failure_reason=fill_result.message,
            failure_category=fill_result.failure_category or "unexpected_error",
            current_url=fill_result.current_url or page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        _save_debug_screenshot(page, application_id, "fill_failed")
        return

    if fill_result.uncertain_fields:
        _finalize_application(
            application_id,
            db.JOB_APP_REQUIRES_USER_ACTION,
            failure_reason=hebrew_failure_message("user_action_required"),
            failure_category="user_action_required",
            requires_user_action_reason=(
                "שדות שלא ניתן למלא בוודאות: " + ", ".join(fill_result.uncertain_fields[:10])
            ),
            current_url=page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        _log_step(
            application_id,
            "answering_questions",
            db.STEP_REQUIRES_USER_ACTION,
            "Uncertain fields",
            db_path,
        )
        return

    # Step: validating_form
    validation = provider.validate_before_submit(page)
    _log_step(
        application_id,
        "validating_form",
        db.STEP_SUCCESS if validation.valid else db.STEP_FAILED,
        ", ".join(validation.missing_required) if validation.missing_required else "OK",
        db_path,
    )
    if not validation.valid:
        _finalize_application(
            application_id,
            db.JOB_APP_FAILED,
            failure_reason=hebrew_failure_message("form_validation_failed"),
            failure_category="form_validation_failed",
            current_url=page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        _save_debug_screenshot(page, application_id, "validation_failed")
        return

    if not AUTO_SUBMIT:
        _finalize_application(
            application_id,
            db.JOB_APP_REQUIRES_USER_ACTION,
            failure_reason="מצב בדיקה: הטופס מולא אך לא נשלח (AUTO_SUBMIT=false)",
            failure_category="user_action_required",
            requires_user_action_reason="יש ללחוץ על שליחה ידנית",
            current_url=page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        return

    # Step: submitting_application
    submit_result = provider.submit(page)
    _log_step(
        application_id,
        "submitting_application",
        db.STEP_SUCCESS if submit_result.success else db.STEP_FAILED,
        submit_result.message,
        db_path,
    )
    if not submit_result.success:
        _finalize_application(
            application_id,
            db.JOB_APP_FAILED,
            failure_reason=submit_result.message,
            failure_category=submit_result.failure_category or "unexpected_error",
            current_url=page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        return

    # Step: verifying_submission
    verify_result = provider.verify_submission(page)
    _log_step(
        application_id,
        "verifying_submission",
        db.STEP_SUCCESS if verify_result.success else db.STEP_FAILED,
        verify_result.message,
        db_path,
    )
    if verify_result.success:
        _finalize_application(
            application_id,
            db.JOB_APP_SUBMITTED,
            confirmation_text=verify_result.confirmation_text,
            confirmation_url=verify_result.confirmation_url or page.url,
            current_url=page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        _sync_match_sent(application_id, db_path)
    else:
        _finalize_application(
            application_id,
            db.JOB_APP_FAILED,
            failure_reason=verify_result.message,
            failure_category=verify_result.failure_category or "submission_confirmation_not_found",
            current_url=page.url,
            provider_name=provider_name,
            db_path=db_path,
        )
        _save_debug_screenshot(page, application_id, "no_confirmation")


def _sync_match_sent(application_id: str, db_path: Path) -> None:
    app = db.get_job_application(application_id, db_path=db_path)
    if not app:
        return
    try:
        db.update_cv_match_status_by_job(
            app["cv_id"], app["job_id"], db.CV_APP_SENT, db_path=db_path
        )
    except ValueError:
        pass


def enqueue_application(
    application_id: str,
    cv_id: str,
    job_id: int,
    *,
    db_path: Path,
) -> bool:
    """Start application processing in a background subprocess."""
    with _worker_lock:
        if application_id in _active_applications:
            return False
        _active_applications.add(application_id)

    env = os.environ.copy()
    env["AGENT_CV_ID"] = cv_id

    proc = subprocess.Popen(
        [
            PYTHON,
            str(SRC / "application_worker.py"),
            "--application-id",
            application_id,
            "--cv-id",
            cv_id,
            "--job-id",
            str(job_id),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    def _waiter() -> None:
        try:
            _, stderr = proc.communicate()
            if proc.returncode != 0 and stderr:
                traceback.print_exc()
        finally:
            with _worker_lock:
                _active_applications.discard(application_id)

    threading.Thread(target=_waiter, daemon=True).start()
    return True


def is_application_active(application_id: str) -> bool:
    with _worker_lock:
        return application_id in _active_applications


if __name__ == "__main__":
    import argparse

    sys.path.insert(0, str(SRC))

    from config import cv_db_path

    parser = argparse.ArgumentParser(description="Run a single job application attempt")
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--cv-id", required=True)
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args()

    run_application_attempt(
        args.application_id,
        args.cv_id,
        args.job_id,
        db_path=cv_db_path(args.cv_id),
    )
