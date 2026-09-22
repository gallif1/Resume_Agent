"""Default scan intervals and small seed test sources."""

from __future__ import annotations

from live_scanner.constants import DEFAULT_INTERVALS_SECONDS


def default_interval_for(provider: str, *, requires_browser: bool = False) -> int:
    if requires_browser:
        return DEFAULT_INTERVALS_SECONDS["browser"]
    return DEFAULT_INTERVALS_SECONDS.get(
        (provider or "").lower(), DEFAULT_INTERVALS_SECONDS["lever"]
    )


# Verification boards (real public APIs) — marked as demo/test. With the Israel
# market filter, most global listings are discarded; keep for provider smoke tests.
# To remove from a workspace UI: Disable or Delete each source in Sources.
SEED_SOURCES: list[dict[str, object]] = [
    {
        "company_name": "Lever Demo",
        "provider": "lever",
        "board_identifier": "leverdemo",
        "careers_url": "https://jobs.lever.co/leverdemo",
        "enabled": True,
        "is_demo": True,
        "notes": "DEMO/TEST seed — Lever public demo board",
    },
    {
        "company_name": "Airbnb",
        "provider": "greenhouse",
        "board_identifier": "airbnb",
        "careers_url": "https://boards.greenhouse.io/airbnb",
        "enabled": True,
        "is_demo": True,
        "notes": "DEMO/TEST seed — Greenhouse public board (global; Israel filter applies)",
    },
    {
        "company_name": "Ashby",
        "provider": "ashby",
        "board_identifier": "ashby",
        "careers_url": "https://jobs.ashbyhq.com/ashby",
        "enabled": True,
        "is_demo": True,
        "notes": "DEMO/TEST seed — Ashby public board",
    },
    {
        "company_name": "NVIDIA",
        "provider": "workday",
        "board_identifier": "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        "careers_url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        "enabled": True,
        "is_demo": True,
        "notes": "DEMO/TEST seed — Workday CXS (global; Israel filter applies)",
    },
    {
        "company_name": "ServiceNow",
        "provider": "smartrecruiters",
        "board_identifier": "servicenow",
        "careers_url": "https://jobs.smartrecruiters.com/servicenow",
        "enabled": True,
        "is_demo": True,
        "notes": "DEMO/TEST seed — SmartRecruiters (includes Israel offices)",
    },
    {
        "company_name": "Nuvei",
        "provider": "workable",
        "board_identifier": "nuvei",
        "careers_url": "https://apply.workable.com/nuvei",
        "enabled": True,
        "is_demo": True,
        "notes": "DEMO/TEST seed — Workable (includes Tel Aviv roles)",
    },
    {
        "company_name": "OrCam",
        "provider": "recruitee",
        "board_identifier": "orcam",
        "careers_url": "https://orcam.recruitee.com",
        "enabled": True,
        "is_demo": True,
        "notes": "DEMO/TEST seed — Recruitee (Israel jobs)",
    },
    {
        "company_name": "HR Plus (Teamtailor)",
        "provider": "teamtailor",
        "board_identifier": "hrplus-1669824102.teamtailor.com",
        "careers_url": "https://hrplus-1669824102.teamtailor.com",
        "enabled": True,
        "is_demo": True,
        "notes": "DEMO/TEST seed — Teamtailor jobs.json (Israel locations)",
    },
    {
        "company_name": "Comeet (needs token)",
        "provider": "comeet",
        "board_identifier": "",
        "careers_url": "",
        "enabled": False,
        "is_demo": True,
        "notes": "Set board_identifier to company_uid:token to enable",
    },
]
