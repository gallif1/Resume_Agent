"""Bullhorn ATS application provider."""

from __future__ import annotations

from playwright.sync_api import Page

from application_providers.generic_provider import GenericProvider
from application_providers.provider_utils import url_matches


class BullhornProvider(GenericProvider):
    name = "bullhorn"

    def can_handle(self, url: str, page: Page | None = None) -> bool:
        return url_matches(
            url,
            "bullhorn.com",
            "bullhornstaffing.com",
            "bhnext.com",
            "bullhorn-os.com",
        )
