"""Collector registry for Live Job Scanner providers."""

from __future__ import annotations

from live_scanner.collectors.ashby import AshbyCollector
from live_scanner.collectors.base import BaseCollector
from live_scanner.collectors.comeet import ComeetCollector
from live_scanner.collectors.greenhouse import GreenhouseCollector
from live_scanner.collectors.lever import LeverCollector
from live_scanner.collectors.recruitee import RecruiteeCollector
from live_scanner.collectors.smartrecruiters import SmartRecruitersCollector
from live_scanner.collectors.teamtailor import TeamtailorCollector
from live_scanner.collectors.workable import WorkableCollector
from live_scanner.collectors.workday import WorkdayCollector
from live_scanner.constants import PROVIDERS

_COLLECTORS: dict[str, BaseCollector] = {
    "lever": LeverCollector(),
    "greenhouse": GreenhouseCollector(),
    "ashby": AshbyCollector(),
    "workday": WorkdayCollector(),
    "comeet": ComeetCollector(),
    "smartrecruiters": SmartRecruitersCollector(),
    "workable": WorkableCollector(),
    "teamtailor": TeamtailorCollector(),
    "recruitee": RecruiteeCollector(),
}


def get_collector(provider: str) -> BaseCollector | None:
    key = (provider or "").strip().lower()
    return _COLLECTORS.get(key)


def list_providers() -> list[dict[str, object]]:
    out = []
    for name in PROVIDERS:
        c = _COLLECTORS[name]
        out.append(
            {
                "id": name,
                "supported": getattr(c, "supported", True),
                "requires_browser": bool(getattr(c, "requires_browser", False)),
                "unsupported_reason": getattr(c, "unsupported_reason", None),
            }
        )
    return out


def validate_source_config(
    provider: str, *, board_identifier: str, careers_url: str | None = None
) -> str | None:
    collector = get_collector(provider)
    if collector is None:
        return f"Unknown provider: {provider}"
    return collector.validate_config(
        board_identifier=board_identifier or "",
        careers_url=careers_url,
    )
