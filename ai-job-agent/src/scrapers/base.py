"""Shared base class for job-board scrapers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from collection_report import CollectionOutcome


class BaseScraper(ABC):
    """Architectural base for modular board collectors.

    Concrete scrapers implement ``collect`` and return a ``CollectionOutcome``
    so the orchestrator can surface blocked/empty/error states without crashing
    the rest of the scan cycle.
    """

    source_id: str = "unknown"
    label_he: str = ""

    @abstractmethod
    def collect(self, query: str, **kwargs: Any) -> CollectionOutcome:
        """Fetch and parse jobs for a single search query."""

    def __call__(self, query: str, **kwargs: Any) -> CollectionOutcome:
        return self.collect(query, **kwargs)

    @staticmethod
    def empty_outcome(
        *,
        status: str = "empty",
        reason: str | None = None,
        reason_he: str | None = None,
        http_status: int | None = None,
    ) -> CollectionOutcome:
        return CollectionOutcome(
            jobs=[],
            status=status,
            reason=reason,
            reason_he=reason_he,
            http_status=http_status,
        )

    @staticmethod
    def caught_up_outcome(
        *,
        reason: str | None = None,
        http_status: int | None = None,
    ) -> CollectionOutcome:
        """Listings existed but all were already known — not a collection failure."""
        return CollectionOutcome(
            jobs=[],
            status="caught_up",
            reason=reason or "All jobs already known (incremental)",
            reason_he=None,
            http_status=http_status,
        )

    @staticmethod
    def ok_outcome(
        jobs: list[dict[str, Any]],
        *,
        http_status: int | None = None,
        caught_up: bool = False,
    ) -> CollectionOutcome:
        if jobs:
            return CollectionOutcome(jobs=jobs, status="ok", http_status=http_status)
        if caught_up:
            return BaseScraper.caught_up_outcome(http_status=http_status)
        return CollectionOutcome(
            jobs=[],
            status="empty",
            reason="No jobs parsed",
            http_status=http_status,
        )
