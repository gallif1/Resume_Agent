"""Generic fallback application provider using normalized field mapping."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from application_providers.base_provider import ApplicationProvider, ApplicationResult, ValidationResult
from application_providers.provider_utils import (
    click_apply_entry,
    click_submit,
    detect_captcha,
    detect_login_required,
    detect_submission_success,
    fill_cover_letter,
    fill_mapped_fields,
    hebrew_failure_message,
    upload_cv_file,
    validate_form,
)

APPLY_TEXTS = [
    "apply",
    "apply now",
    "submit application",
    "הגש מועמדות",
    "הגשת מועמדות",
    "שלח קורות חיים",
]

SUBMIT_TEXTS = [
    "submit",
    "submit application",
    "send application",
    "apply",
    "שליחה",
    "שלח",
    "הגשה",
    "אישור ושליחה",
]


class GenericProvider(ApplicationProvider):
    name = "generic"

    def can_handle(self, url: str, page: Page | None = None) -> bool:
        return True

    def fill_application(
        self,
        page: Page,
        user_profile: dict[str, Any],
        cv_file_path: str,
        job: dict[str, Any],
        *,
        cover_letter: str | None = None,
    ) -> ApplicationResult:
        if detect_captcha(page):
            return ApplicationResult(
                success=False,
                status="requires_user_action",
                message=hebrew_failure_message("captcha_detected"),
                failure_category="captcha_detected",
                current_url=page.url,
                provider_name=self.name,
            )
        if detect_login_required(page):
            return ApplicationResult(
                success=False,
                status="requires_user_action",
                message=hebrew_failure_message("login_required"),
                failure_category="login_required",
                current_url=page.url,
                provider_name=self.name,
            )

        clicked = click_apply_entry(page, APPLY_TEXTS)
        if clicked:
            page.wait_for_timeout(2000)
            if detect_captcha(page):
                return ApplicationResult(
                    success=False,
                    status="requires_user_action",
                    message=hebrew_failure_message("captcha_detected"),
                    failure_category="captcha_detected",
                    current_url=page.url,
                    provider_name=self.name,
                )
            if detect_login_required(page):
                return ApplicationResult(
                    success=False,
                    status="requires_user_action",
                    message=hebrew_failure_message("login_required"),
                    failure_category="login_required",
                    current_url=page.url,
                    provider_name=self.name,
                )

        filled, skipped, uncertain = fill_mapped_fields(page, user_profile)
        cv_ok = upload_cv_file(page, cv_file_path)
        if cover_letter:
            fill_cover_letter(page, cover_letter)

        if not filled and not cv_ok:
            return ApplicationResult(
                success=False,
                status="failed",
                message=hebrew_failure_message("application_form_not_found"),
                failure_category="application_form_not_found",
                current_url=page.url,
                provider_name=self.name,
                skipped_fields=skipped,
            )

        return ApplicationResult(
            success=True,
            status="in_progress",
            message="Form filled",
            current_url=page.url,
            provider_name=self.name,
            filled_fields=filled,
            skipped_fields=skipped,
            uncertain_fields=uncertain,
        )

    def validate_before_submit(self, page: Page) -> ValidationResult:
        valid, errors, cv_attached = validate_form(page)
        return ValidationResult(
            valid=valid,
            errors=[hebrew_failure_message("required_field_missing")],
            missing_required=errors,
            cv_attached=cv_attached,
        )

    def submit(self, page: Page) -> ApplicationResult:
        if not click_submit(page, SUBMIT_TEXTS):
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

    def verify_submission(self, page: Page) -> ApplicationResult:
        page.wait_for_timeout(2500)
        ok, snippet = detect_submission_success(page)
        if ok:
            return ApplicationResult(
                success=True,
                status="submitted",
                message="Application submitted",
                confirmation_text=snippet,
                confirmation_url=page.url,
                current_url=page.url,
                provider_name=self.name,
            )
        return ApplicationResult(
            success=False,
            status="failed",
            message=hebrew_failure_message("submission_confirmation_not_found"),
            failure_category="submission_confirmation_not_found",
            current_url=page.url,
            provider_name=self.name,
        )
