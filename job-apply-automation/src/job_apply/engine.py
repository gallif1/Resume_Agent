"""Core engine: open job URL, fill form, submit."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from job_apply.browser import BROWSER_PROFILE_DIR, LOGS_DIR, create_browser_context
from job_apply.form_filler import (
    click_submit,
    detect_captcha,
    detect_login_required,
    detect_submission_success,
    fill_mapped_fields,
    open_application_page,
    page_has_application_form,
    upload_cv_file,
    validate_required,
)
from job_apply.models import ApplyRequest, ApplyResult


def _screenshot(page, tag: str) -> str | None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = LOGS_DIR / f"apply_{tag}_{stamp}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


def apply_to_job(request: ApplyRequest) -> ApplyResult:
    """Fill (and optionally submit) a job application form for the given request."""
    cv_path = Path(request.cv_path).expanduser().resolve()
    if not cv_path.is_file():
        return ApplyResult(
            success=False,
            status="failed",
            message=f"CV file not found: {cv_path}",
            job_url=request.job_url,
            failure_category="cv_missing",
        )

    job_url = (request.job_url or "").strip()
    if not job_url.startswith(("http://", "https://", "file://")):
        return ApplyResult(
            success=False,
            status="failed",
            message="Invalid job URL",
            job_url=job_url,
            failure_category="invalid_url",
        )

    profile = request.applicant.to_profile()

    with sync_playwright() as playwright:
        context, page = create_browser_context(
            playwright,
            headless=request.headless,
            user_data_dir=BROWSER_PROFILE_DIR if not job_url.startswith("file://") else None,
        )
        try:
            page.goto(job_url, wait_until="domcontentloaded", timeout=request.timeout_ms)
            page.wait_for_timeout(1500)

            if detect_captcha(page):
                shot = _screenshot(page, "captcha")
                return ApplyResult(
                    success=False,
                    status="requires_user_action",
                    message="CAPTCHA detected — complete it manually and retry",
                    job_url=job_url,
                    final_url=page.url,
                    screenshot_path=shot,
                    failure_category="captcha_detected",
                )

            if detect_login_required(page) and not page_has_application_form(page):
                shot = _screenshot(page, "login")
                return ApplyResult(
                    success=False,
                    status="requires_user_action",
                    message="Login required — sign in manually (browser profile is saved) and retry",
                    job_url=job_url,
                    final_url=page.url,
                    screenshot_path=shot,
                    failure_category="login_required",
                )

            active = open_application_page(page)
            if detect_captcha(active):
                shot = _screenshot(active, "captcha")
                return ApplyResult(
                    success=False,
                    status="requires_user_action",
                    message="CAPTCHA detected after opening apply form",
                    job_url=job_url,
                    final_url=active.url,
                    screenshot_path=shot,
                    failure_category="captcha_detected",
                )

            if detect_login_required(active) and not page_has_application_form(active):
                shot = _screenshot(active, "login")
                return ApplyResult(
                    success=False,
                    status="requires_user_action",
                    message="Login required to open the application form",
                    job_url=job_url,
                    final_url=active.url,
                    screenshot_path=shot,
                    failure_category="login_required",
                )

            if not page_has_application_form(active):
                shot = _screenshot(active, "no_form")
                return ApplyResult(
                    success=False,
                    status="failed",
                    message="No application form found on this page",
                    job_url=job_url,
                    final_url=active.url,
                    screenshot_path=shot,
                    failure_category="application_form_not_found",
                )

            filled, skipped = fill_mapped_fields(active, profile)
            cv_ok = upload_cv_file(active, str(cv_path))

            if not filled and not cv_ok:
                shot = _screenshot(active, "fill_failed")
                return ApplyResult(
                    success=False,
                    status="failed",
                    message="Could not fill any fields or upload CV",
                    job_url=job_url,
                    final_url=active.url,
                    filled_fields=filled,
                    skipped_fields=skipped,
                    screenshot_path=shot,
                    failure_category="application_form_not_found",
                )

            missing = validate_required(active)
            if missing:
                shot = _screenshot(active, "validation")
                return ApplyResult(
                    success=False,
                    status="failed",
                    message=f"Required fields still empty: {', '.join(missing)}",
                    job_url=job_url,
                    final_url=active.url,
                    filled_fields=filled,
                    skipped_fields=skipped + missing,
                    screenshot_path=shot,
                    failure_category="required_field_missing",
                )

            if request.dry_run:
                shot = _screenshot(active, "dry_run")
                return ApplyResult(
                    success=True,
                    status="filled",
                    message="Form filled (dry-run — submit skipped)",
                    job_url=job_url,
                    final_url=active.url,
                    filled_fields=filled + (["cv_file"] if cv_ok else []),
                    skipped_fields=skipped,
                    screenshot_path=shot,
                )

            if not click_submit(active):
                shot = _screenshot(active, "no_submit")
                return ApplyResult(
                    success=False,
                    status="failed",
                    message="Could not find or click the Submit button",
                    job_url=job_url,
                    final_url=active.url,
                    filled_fields=filled + (["cv_file"] if cv_ok else []),
                    skipped_fields=skipped,
                    screenshot_path=shot,
                    failure_category="application_form_not_found",
                )

            active.wait_for_timeout(2500)
            ok, snippet = detect_submission_success(active)
            shot = _screenshot(active, "submitted" if ok else "submit_unclear")
            if ok:
                return ApplyResult(
                    success=True,
                    status="submitted",
                    message="Application submitted",
                    job_url=job_url,
                    final_url=active.url,
                    filled_fields=filled + (["cv_file"] if cv_ok else []),
                    skipped_fields=skipped,
                    confirmation_text=snippet,
                    screenshot_path=shot,
                )

            return ApplyResult(
                success=True,
                status="submitted",
                message=(
                    "Submit clicked; confirmation text not detected — "
                    "verify the page screenshot manually"
                ),
                job_url=job_url,
                final_url=active.url,
                filled_fields=filled + (["cv_file"] if cv_ok else []),
                skipped_fields=skipped,
                screenshot_path=shot,
                failure_category="submission_confirmation_not_found",
            )
        finally:
            context.close()
