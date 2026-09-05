"""Core engine: open job URL, fill form, submit."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from job_apply.browser import (
    BROWSER_PROFILE_DIR,
    LOGS_DIR,
    create_browser_context,
    resolve_headless,
)
from job_apply.form_filler import (
    click_submit,
    detect_captcha,
    detect_login_required,
    detect_submission_success,
    fill_mapped_fields,
    find_form_target,
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
            message=f"קובץ קורות החיים לא נמצא: {cv_path}",
            job_url=request.job_url,
            failure_category="cv_missing",
        )

    job_url = (request.job_url or "").strip()
    if not job_url.startswith(("http://", "https://", "file://")):
        return ApplyResult(
            success=False,
            status="failed",
            message="קישור משרה לא תקין",
            job_url=job_url,
            failure_category="invalid_url",
        )

    profile = request.applicant.to_profile()
    headless, headless_note = resolve_headless(request.headless)

    try:
        with sync_playwright() as playwright:
            context, page = create_browser_context(
                playwright,
                headless=headless,
                user_data_dir=BROWSER_PROFILE_DIR if not job_url.startswith("file://") else None,
            )
            try:
                result = _run_apply_session(
                    page,
                    request=request,
                    job_url=job_url,
                    cv_path=cv_path,
                    profile=profile,
                )
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:  # noqa: BLE001 — always return structured result to the API
        msg = str(exc).strip() or exc.__class__.__name__
        low = msg.lower()
        if any(
            token in low
            for token in (
                "missing dependencies",
                "executable doesn't exist",
                "browsertype.launch",
                "browser has been closed",
                "display",
                "x server",
                "xdg_runtime_dir",
            )
        ):
            return ApplyResult(
                success=False,
                status="failed",
                message=(
                    "כשל בהפעלת Chromium בשרת. "
                    "בטלו «הצג דפדפן בלייב» (אין מסך בשרת) או התקינו דפדפן Playwright. "
                    f"פרטים: {msg}"
                ),
                job_url=job_url,
                failure_category="browser_launch_failed",
            )
        return ApplyResult(
            success=False,
            status="failed",
            message=f"שגיאה בהרצת אוטומציית ההגשה: {msg}",
            job_url=job_url,
            failure_category="engine_error",
        )

    if headless_note and result.message:
        result.message = f"{result.message} ({headless_note})"
    elif headless_note:
        result.message = headless_note
    return result


def _run_apply_session(
    page,
    *,
    request: ApplyRequest,
    job_url: str,
    cv_path: Path,
    profile: dict,
) -> ApplyResult:
    page.goto(job_url, wait_until="domcontentloaded", timeout=request.timeout_ms)
    # Comeet / Angular boards need a moment to hydrate.
    page.wait_for_timeout(2500)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    if detect_captcha(page):
        shot = _screenshot(page, "captcha")
        return ApplyResult(
            success=False,
            status="requires_user_action",
            message="זוהה CAPTCHA — השלימו ידנית בחלון הדפדפן ונסו שוב",
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
            message="נדרשת התחברות — התחברו ידנית (פרופיל הדפדפן נשמר) ונסו שוב",
            job_url=job_url,
            final_url=page.url,
            screenshot_path=shot,
            failure_category="login_required",
        )

    active = open_application_page(page)
    form = find_form_target(active, wait_ms=12000)

    if detect_captcha(form) or detect_captcha(active):
        shot = _screenshot(active, "captcha")
        return ApplyResult(
            success=False,
            status="requires_user_action",
            message="זוהה CAPTCHA אחרי פתיחת טופס ההגשה — השלימו ידנית ונסו שוב",
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
            message="נדרשת התחברות כדי לפתוח את טופס ההגשה",
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
            message=(
                "לא נמצא טופס הגשה בעמוד. "
                "ב־Comeet לוחצים Apply ואז הטופס נטען בתוך iframe — "
                "אם הכפתור לא נמצא, פתחו עם הצגת דפדפן בלייב."
            ),
            job_url=job_url,
            final_url=active.url,
            screenshot_path=shot,
            failure_category="application_form_not_found",
        )

    filled, skipped = fill_mapped_fields(form, profile)
    cv_ok = upload_cv_file(form, str(cv_path))

    if not filled and not cv_ok:
        shot = _screenshot(active, "fill_failed")
        return ApplyResult(
            success=False,
            status="failed",
            message="לא הצלחנו למלא שדות או להעלות קורות חיים",
            job_url=job_url,
            final_url=active.url,
            filled_fields=filled,
            skipped_fields=skipped,
            screenshot_path=shot,
            failure_category="application_form_not_found",
        )

    missing = validate_required(form)
    if missing:
        shot = _screenshot(active, "validation")
        return ApplyResult(
            success=False,
            status="failed",
            message=f"שדות חובה עדיין ריקים: {', '.join(missing)}",
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
            message="הטופס מולא (מצב ניסיון — ללא שליחה)",
            job_url=job_url,
            final_url=active.url,
            filled_fields=filled + (["cv_file"] if cv_ok else []),
            skipped_fields=skipped,
            screenshot_path=shot,
        )

    if not click_submit(form):
        shot = _screenshot(active, "no_submit")
        return ApplyResult(
            success=False,
            status="failed",
            message="לא נמצא או לא נלחץ כפתור Submit",
            job_url=job_url,
            final_url=active.url,
            filled_fields=filled + (["cv_file"] if cv_ok else []),
            skipped_fields=skipped,
            screenshot_path=shot,
            failure_category="application_form_not_found",
        )

    active.wait_for_timeout(2500)

    if detect_captcha(form) or detect_captcha(active):
        shot = _screenshot(active, "captcha_after_submit")
        return ApplyResult(
            success=False,
            status="requires_user_action",
            message=(
                "הטופס מולא, אבל Comeet דורש CAPTCHA לפני שליחה. "
                "הפעילו «הצג דפדפן בלייב», סמנו את האימות ידנית, ושלחו."
            ),
            job_url=job_url,
            final_url=active.url,
            filled_fields=filled + (["cv_file"] if cv_ok else []),
            skipped_fields=skipped,
            screenshot_path=shot,
            failure_category="captcha_detected",
        )

    ok, snippet = detect_submission_success(form)
    if not ok:
        ok, snippet = detect_submission_success(active)
    shot = _screenshot(active, "submitted" if ok else "submit_unclear")
    if ok:
        return ApplyResult(
            success=True,
            status="submitted",
            message="המועמדות נשלחה",
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
        message="נלחץ Submit; לא זוהה טקסט אישור — בדקו את צילום המסך ידנית",
        job_url=job_url,
        final_url=active.url,
        filled_fields=filled + (["cv_file"] if cv_ok else []),
        skipped_fields=skipped,
        screenshot_path=shot,
        failure_category="submission_confirmation_not_found",
    )
