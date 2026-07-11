"""Base classes for job application provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page

FAILURE_CATEGORIES = frozenset({
    "job_page_unavailable",
    "application_form_not_found",
    "unsupported_provider",
    "required_field_missing",
    "cv_upload_failed",
    "form_validation_failed",
    "captcha_detected",
    "login_required",
    "user_action_required",
    "submission_confirmation_not_found",
    "website_blocked_automation",
    "network_error",
    "unexpected_error",
})


@dataclass
class ApplicationResult:
    success: bool
    status: str  # submitted | failed | requires_user_action
    message: str = ""
    failure_category: str | None = None
    confirmation_text: str | None = None
    confirmation_url: str | None = None
    current_url: str | None = None
    provider_name: str | None = None
    filled_fields: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)
    uncertain_fields: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    cv_attached: bool = False


class ApplicationProvider(ABC):
    name: str = "base"

    @abstractmethod
    def can_handle(self, url: str, page: Page | None = None) -> bool:
        ...

    @abstractmethod
    def fill_application(
        self,
        page: Page,
        user_profile: dict[str, Any],
        cv_file_path: str,
        job: dict[str, Any],
        *,
        cover_letter: str | None = None,
    ) -> ApplicationResult:
        ...

    @abstractmethod
    def validate_before_submit(self, page: Page) -> ValidationResult:
        ...

    @abstractmethod
    def submit(self, page: Page) -> ApplicationResult:
        ...

    @abstractmethod
    def verify_submission(self, page: Page) -> ApplicationResult:
        ...
