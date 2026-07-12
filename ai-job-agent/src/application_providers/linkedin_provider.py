"""LinkedIn application provider."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from application_providers.base_provider import ApplicationResult, ValidationResult
from application_providers.generic_provider import GenericProvider
from application_providers.provider_utils import (
    detect_captcha,
    detect_linkedin_auth_wall,
    detect_login_required,
    extract_external_apply_url,
    hebrew_failure_message,
    navigate_to_external_apply,
    open_application_page,
    page_has_application_form,
    url_matches,
)
from site_auth import ensure_linkedin_session
from site_credentials import linkedin_credentials_configured

LINKEDIN_APPLY_TEXTS = [
    "הגשת מועמדות",
    "הגש מועמדות",
    "apply now",
    "apply on company website",
    "apply",
]

LINKEDIN_APPLY_SELECTORS = [
    "a.jobs-apply-button",
    "button.jobs-apply-button",
    "a[data-tracking-control-name='public_jobs_apply-link-offsite']",
    "a[data-tracking-control-name='public_jobs_apply-link']",
    ".jobs-s-apply button",
    ".jobs-apply-button--top-card",
]


def _select_provider(url: str, page: Page | None = None):
    from application_providers.registry import select_provider
    return select_provider(url, page)


class LinkedInProvider(GenericProvider):
    name = "linkedin"

    def can_handle(self, url: str, page: Page | None = None) -> bool:
        return url_matches(url, "linkedin.com/jobs", "linkedin.com/job")

    def _resolve_application_page(self, page: Page) -> Page | None:
        if page_has_application_form(page):
            return page

        # Prefer direct navigation to off-site apply URL (works without LinkedIn login).
        external_url = extract_external_apply_url(page)
        if external_url and navigate_to_external_apply(page, external_url):
            if page_has_application_form(page):
                return page

        external = open_application_page(
            page,
            LINKEDIN_APPLY_TEXTS,
            selectors=LINKEDIN_APPLY_SELECTORS,
            wait_ms=3500,
        )
        if external is not None:
            return external
        if page_has_application_form(page):
            return page
        return None

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
        if detect_captcha(page):
            return ApplicationResult(
                success=False,
                status="requires_user_action",
                message=hebrew_failure_message("captcha_detected"),
                failure_category="captcha_detected",
                current_url=page.url,
                provider_name=self.name,
            )

        app_page = self._resolve_application_page(page)

        if app_page is None and detect_linkedin_auth_wall(page):
            if cv_id and ensure_linkedin_session(page, cv_id):
                page.goto(job.get("job_url") or page.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                app_page = self._resolve_application_page(page)

        if app_page is None and detect_linkedin_auth_wall(page):
            message_key = (
                "linkedin_credentials_missing"
                if not linkedin_credentials_configured(cv_id)
                else "linkedin_login_required"
            )
            return ApplicationResult(
                success=False,
                status="failed",
                message=hebrew_failure_message(message_key),
                failure_category="login_required",
                current_url=page.url,
                provider_name=self.name,
            )

        if detect_login_required(page) and app_page is None:
            if cv_id and ensure_linkedin_session(page, cv_id):
                page.goto(job.get("job_url") or page.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                app_page = self._resolve_application_page(page)
            if app_page is None:
                message_key = (
                    "linkedin_credentials_missing"
                    if not linkedin_credentials_configured(cv_id)
                    else "linkedin_login_required"
                )
                return ApplicationResult(
                    success=False,
                    status="failed",
                    message=hebrew_failure_message(message_key),
                    failure_category="login_required",
                    current_url=page.url,
                    provider_name=self.name,
                )

        if app_page is None:
            return ApplicationResult(
                success=False,
                status="failed",
                message=hebrew_failure_message("application_form_not_found"),
                failure_category="application_form_not_found",
                current_url=page.url,
                provider_name=self.name,
            )

        app_page.wait_for_load_state("domcontentloaded", timeout=30000)
        app_page.wait_for_timeout(1500)
        self._application_page = app_page

        if url_matches(app_page.url, "linkedin.com"):
            if detect_linkedin_auth_wall(app_page):
                if cv_id and ensure_linkedin_session(app_page, cv_id):
                    app_page.goto(
                        job.get("job_url") or app_page.url,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    app_page.wait_for_timeout(2000)
                    app_page = self._resolve_application_page(app_page) or app_page
                if detect_linkedin_auth_wall(app_page):
                    message_key = (
                        "linkedin_credentials_missing"
                        if not linkedin_credentials_configured(cv_id)
                        else "linkedin_login_required"
                    )
                    return ApplicationResult(
                        success=False,
                        status="failed",
                        message=hebrew_failure_message(message_key),
                        failure_category="login_required",
                        current_url=app_page.url,
                        provider_name=self.name,
                    )
            result = super().fill_application(
                app_page,
                user_profile,
                cv_file_path,
                job,
                cover_letter=cover_letter,
                cv_id=cv_id,
            )
            result.provider_name = self.name
            if (
                not result.success
                and result.failure_category == "application_form_not_found"
                and detect_linkedin_auth_wall(app_page)
            ):
                result.status = "failed"
                result.failure_category = "login_required"
                result.message = hebrew_failure_message(
                    "linkedin_credentials_missing"
                    if not linkedin_credentials_configured(cv_id)
                    else "linkedin_login_required"
                )
            return result

        external = _select_provider(app_page.url, app_page)
        result = external.fill_application(
            app_page,
            user_profile,
            cv_file_path,
            job,
            cover_letter=cover_letter,
            cv_id=cv_id,
        )
        result.provider_name = f"{self.name}->{external.name}"
        if result.current_url:
            self._application_page = app_page
        return result

    def validate_before_submit(self, page: Page) -> ValidationResult:
        target = self.application_page(page)
        if not url_matches(target.url, "linkedin.com"):
            return _select_provider(target.url, target).validate_before_submit(target)
        return super().validate_before_submit(target)

    def submit(self, page: Page) -> ApplicationResult:
        target = self.application_page(page)
        if not url_matches(target.url, "linkedin.com"):
            return _select_provider(target.url, target).submit(target)
        return super().submit(target)

    def verify_submission(self, page: Page) -> ApplicationResult:
        target = self.application_page(page)
        if not url_matches(target.url, "linkedin.com"):
            return _select_provider(target.url, target).verify_submission(target)
        return super().verify_submission(target)
