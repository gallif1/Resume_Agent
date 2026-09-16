"""Default scan intervals and small seed test sources."""

from __future__ import annotations

from live_scanner.constants import DEFAULT_INTERVALS_SECONDS


def default_interval_for(provider: str, *, requires_browser: bool = False) -> int:
    if requires_browser:
        return DEFAULT_INTERVALS_SECONDS["browser"]
    return DEFAULT_INTERVALS_SECONDS.get(
        (provider or "").lower(), DEFAULT_INTERVALS_SECONDS["lever"]
    )


# Small set for verification — not hundreds of companies.
# Comeet is included as a configured-but-token-required example so the adapter
# is exercised; enable after providing company_uid:token.
SEED_SOURCES: list[dict[str, object]] = [
    {
        "company_name": "Lever Demo",
        "provider": "lever",
        "board_identifier": "leverdemo",
        "careers_url": "https://jobs.lever.co/leverdemo",
        "enabled": True,
    },
    {
        "company_name": "Airbnb",
        "provider": "greenhouse",
        "board_identifier": "airbnb",
        "careers_url": "https://boards.greenhouse.io/airbnb",
        "enabled": True,
    },
    {
        "company_name": "Ashby",
        "provider": "ashby",
        "board_identifier": "ashby",
        "careers_url": "https://jobs.ashbyhq.com/ashby",
        "enabled": True,
    },
    {
        "company_name": "NVIDIA",
        "provider": "workday",
        "board_identifier": "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        "careers_url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        "enabled": True,
    },
    {
        "company_name": "Comeet (needs token)",
        "provider": "comeet",
        "board_identifier": "",
        "careers_url": "",
        "enabled": False,
        "notes": "Set board_identifier to company_uid:token to enable",
    },
]
