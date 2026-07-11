"""LinkedIn application provider."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from application_providers.base_provider import ApplicationResult
from application_providers.generic_provider import GenericProvider
from application_providers.provider_utils import hebrew_failure_message, url_matches


class LinkedInProvider(GenericProvider):
    name = "linkedin"

    def can_handle(self, url: str, page: Page | None = None) -> bool:
        return url_matches(url, "linkedin.com/jobs", "linkedin.com/job")

    def fill_application(
        self,
        page: Page,
        user_profile: dict[str, Any],
        cv_file_path: str,
        job: dict[str, Any],
        *,
        cover_letter: str | None = None,
    ) -> ApplicationResult:
        # LinkedIn Easy Apply often requires login and has anti-bot measures.
        result = super().fill_application(
            page, user_profile, cv_file_path, job, cover_letter=cover_letter
        )
        if result.failure_category == "login_required":
            result.message = hebrew_failure_message("login_required")
        return result
