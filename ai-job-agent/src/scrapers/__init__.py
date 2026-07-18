"""Modular job-board scrapers.

Each scraper exposes a ``collect_*_jobs(query)`` helper that returns a
``CollectionOutcome`` (or a plain job list for backward compatibility).
"""

from __future__ import annotations

from scrapers.alljobs_scraper import AllJobsScraper, collect_alljobs_jobs
from scrapers.base import BaseScraper
from scrapers.geektime_scraper import GeektimeScraper, collect_geektime_jobs
from scrapers.gotfriends_scraper import GotfriendsScraper, collect_gotfriends_jobs
from scrapers.indeed_israel_scraper import IndeedIsraelScraper, collect_indeed_jobs
from scrapers.secret_tel_aviv_scraper import (
    SecretTelAvivScraper,
    collect_secret_tel_aviv_jobs,
)

__all__ = [
    "BaseScraper",
    "AllJobsScraper",
    "GeektimeScraper",
    "GotfriendsScraper",
    "IndeedIsraelScraper",
    "SecretTelAvivScraper",
    "collect_alljobs_jobs",
    "collect_geektime_jobs",
    "collect_gotfriends_jobs",
    "collect_indeed_jobs",
    "collect_secret_tel_aviv_jobs",
]
