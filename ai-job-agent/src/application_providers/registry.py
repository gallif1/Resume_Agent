"""Provider registry and selection (avoids circular imports)."""

from __future__ import annotations

from playwright.sync_api import Page

from application_providers.base_provider import ApplicationProvider
from application_providers.bullhorn_provider import BullhornProvider
from application_providers.comeet_provider import ComeetProvider
from application_providers.drushim_provider import DrushimProvider
from application_providers.generic_provider import GenericProvider
from application_providers.greenhouse_provider import GreenhouseProvider
from application_providers.lever_provider import LeverProvider
from application_providers.linkedin_provider import LinkedInProvider
from application_providers.smartrecruiters_provider import SmartRecruitersProvider
from application_providers.workday_provider import WorkdayProvider

PROVIDER_CLASSES: tuple[type[ApplicationProvider], ...] = (
    DrushimProvider,
    BullhornProvider,
    GreenhouseProvider,
    LeverProvider,
    WorkdayProvider,
    LinkedInProvider,
    ComeetProvider,
    SmartRecruitersProvider,
    GenericProvider,
)


def select_provider(url: str, page: Page | None = None) -> ApplicationProvider:
    """Return a fresh provider instance that can handle the given URL/page."""
    for provider_cls in PROVIDER_CLASSES:
        instance = provider_cls()
        if instance.can_handle(url, page):
            return instance
    return GenericProvider()
