"""Greenhouse application provider."""

from __future__ import annotations

from playwright.sync_api import Page

from application_providers.generic_provider import GenericProvider
from application_providers.provider_utils import url_matches


class GreenhouseProvider(GenericProvider):
    name = "greenhouse"

    def can_handle(self, url: str, page: Page | None = None) -> bool:
        return url_matches(url, "greenhouse.io", "boards.greenhouse.io")
