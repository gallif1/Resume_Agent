"""Workday application provider."""

from __future__ import annotations

from playwright.sync_api import Page

from application_providers.generic_provider import GenericProvider
from application_providers.provider_utils import url_matches


class WorkdayProvider(GenericProvider):
    name = "workday"

    def can_handle(self, url: str, page: Page | None = None) -> bool:
        return url_matches(url, "myworkdayjobs.com", "workday.com")
