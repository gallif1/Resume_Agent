"""Base collector interface for ATS career boards."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CollectedJob:
    """Normalized listing returned by a provider collector (cheap discovery)."""

    external_job_id: str
    title: str
    company: str
    job_url: str
    location: str = ""
    description: str = ""
    posted_date: str | None = None
    country: str = ""
    country_code: str = ""
    is_remote: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_job_id": self.external_job_id,
            "title": self.title,
            "company": self.company,
            "job_url": self.job_url,
            "location": self.location,
            "description": self.description,
            "posted_date": self.posted_date,
            "country": self.country,
            "country_code": self.country_code,
            "is_remote": self.is_remote,
        }


@dataclass
class CollectResult:
    """Outcome of one source fetch — never raises into the scheduler."""

    jobs: list[CollectedJob] = field(default_factory=list)
    status: str = "ok"  # ok | empty | error | unsupported
    error: str | None = None
    http_status: int | None = None


class BaseCollector(ABC):
    """Provider adapter. Prefer public/structured HTTP endpoints."""

    provider: str = "unknown"
    requires_browser: bool = False
    supported: bool = True
    unsupported_reason: str | None = None

    @abstractmethod
    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        """Fetch currently active jobs for one company/board."""

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        """Return an error message if config is invalid, else None."""
        if not (board_identifier or "").strip() and not (careers_url or "").strip():
            return "board_identifier or careers_url is required"
        return None
