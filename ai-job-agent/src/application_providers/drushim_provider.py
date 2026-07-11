"""Drushim.co.il application provider."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from application_providers.base_provider import ApplicationResult, ValidationResult
from application_providers.generic_provider import GenericProvider
from application_providers.provider_utils import (
    detect_captcha,
    detect_login_required,
    detect_submission_success,
    fill_cover_letter,
    fill_mapped_fields,
    hebrew_failure_message,
    upload_cv_file,
    url_matches,
    validate_form,
)
from apply_jobs import (
    APPLY_BUTTON_SELECTORS,
    APPLY_BUTTON_TEXTS,
    SUBMIT_BUTTON_TEXTS,
    _click_first,
    _page_shows_success,
)
from site_auth import ensure_drushim_session
from site_credentials import drushim_credentials_configured


class DrushimProvider(GenericProvider):
    name = "drushim"

    def can_handle(self, url: str, page: Page | None = None) -> bool:
        return url_matches(url, "drushim.co.il")

    def fill_application(
        self,
        page: Page,
        user_profile: dict[str, Any],
        cv_file_path: str,
        job: dict[str, Any],
        *,
        cover_letter: str | None = None,
        cv_id: str | None = None,
    ) -> ApplicationResult:
        if _page_shows_success(page):
            ok, snippet = detect_submission_success(page)
            return ApplicationResult(
                success=True,
                status="submitted",
                message="Already applied",
                confirmation_text=snippet,
                confirmation_url=page.url,
                current_url=page.url,
                provider_name=self.name,
            )

        if detect_captcha(page):
            return ApplicationResult(
                success=False,
                status="requires_user_action",
                message=hebrew_failure_message("captcha_detected"),
                failure_category="captcha_detected",
                current_url=page.url,
                provider_name=self.name,
            )

        if not _click_first(page, APPLY_BUTTON_SELECTORS, APPLY_BUTTON_TEXTS):
            return ApplicationResult(
                success=False,
                status="failed",
                message=hebrew_failure_message("application_form_not_found"),
                failure_category="application_form_not_found",
                current_url=page.url,
                provider_name=self.name,
            )

        page.wait_for_timeout(2500)

        if detect_login_required(page):
            if cv_id and ensure_drushim_session(page, cv_id):
                if not _click_first(page, APPLY_BUTTON_SELECTORS, APPLY_BUTTON_TEXTS):
                    return ApplicationResult(
                        success=False,
                        status="failed",
                        message=hebrew_failure_message("application_form_not_found"),
                        failure_category="application_form_not_found",
                        current_url=page.url,
                        provider_name=self.name,
                    )
                page.wait_for_timeout(2500)
            if detect_login_required(page):
                message = (
                    "הזן אימייל וסיסמה לדרושים בעמוד הפרופיל "
                    "כדי שהמערכת תתחבר אוטומטית."
                    if not drushim_credentials_configured(cv_id)
                    else hebrew_failure_message("login_required")
                )
                return ApplicationResult(
                    success=False,
                    status="failed",
                    message=message,
                    failure_category="login_required",
                    current_url=page.url,
                    provider_name=self.name,
                )

        filled, skipped, uncertain = fill_mapped_fields(page, user_profile)
        cv_ok = upload_cv_file(page, cv_file_path)
        if cover_letter:
            fill_cover_letter(page, cover_letter)

        if _page_shows_success(page):
            ok, snippet = detect_submission_success(page)
            return ApplicationResult(
                success=True,
                status="submitted",
                message="Sent via one-click apply",
                confirmation_text=snippet,
                confirmation_url=page.url,
                current_url=page.url,
                provider_name=self.name,
                filled_fields=filled,
            )

        return ApplicationResult(
            success=True,
            status="in_progress",
            message="Form filled",
            current_url=page.url,
            provider_name=self.name,
            filled_fields=filled,
            skipped_fields=skipped,
            uncertain_fields=uncertain if not cv_ok else uncertain,
        )

    def submit(self, page: Page) -> ApplicationResult:
        if _page_shows_success(page):
            ok, snippet = detect_submission_success(page)
            return ApplicationResult(
                success=True,
                status="submitted",
                message="Already submitted",
                confirmation_text=snippet,
                confirmation_url=page.url,
                current_url=page.url,
                provider_name=self.name,
            )
        if not _click_first(page, ["button[type='submit']"], SUBMIT_BUTTON_TEXTS):
            return ApplicationResult(
                success=False,
                status="failed",
                message=hebrew_failure_message("application_form_not_found"),
                failure_category="application_form_not_found",
                current_url=page.url,
                provider_name=self.name,
            )
        return ApplicationResult(
            success=True,
            status="in_progress",
            message="Submit clicked",
            current_url=page.url,
            provider_name=self.name,
        )

    def validate_before_submit(self, page: Page) -> ValidationResult:
        valid, errors, cv_attached = validate_form(page)
        return ValidationResult(valid=valid, missing_required=errors, cv_attached=cv_attached)
