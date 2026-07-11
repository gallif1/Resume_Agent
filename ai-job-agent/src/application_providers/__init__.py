"""Job application provider adapters."""

from __future__ import annotations

from playwright.sync_api import Page

from application_providers.base_provider import ApplicationProvider
from application_providers.comeet_provider import ComeetProvider
from application_providers.drushim_provider import DrushimProvider
from application_providers.generic_provider import GenericProvider
from application_providers.greenhouse_provider import GreenhouseProvider
from application_providers.lever_provider import LeverProvider
from application_providers.linkedin_provider import LinkedInProvider
from application_providers.smartrecruiters_provider import SmartRecruitersProvider
from application_providers.workday_provider import WorkdayProvider

PROVIDERS: list[ApplicationProvider] = [
    DrushimProvider(),
    GreenhouseProvider(),
    LeverProvider(),
    WorkdayProvider(),
    LinkedInProvider(),
    ComeetProvider(),
    SmartRecruitersProvider(),
    GenericProvider(),
]


def select_provider(url: str, page: Page | None = None) -> ApplicationProvider:
    """Return the first provider that can handle the given URL/page."""
    for provider in PROVIDERS:
        if provider.can_handle(url, page):
            return provider
    return GenericProvider()
