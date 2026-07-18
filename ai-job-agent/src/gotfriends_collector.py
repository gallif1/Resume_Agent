"""Backward-compatible re-export of the GotFriends scraper module.

New code should import from ``scrapers.gotfriends_scraper``.
"""

from __future__ import annotations

from scrapers.gotfriends_scraper import (  # noqa: F401
    GOTFRIENDS_CATEGORIES,
    GotfriendsScraper,
    build_gotfriends_page_url,
    collect_gotfriends_jobs,
    fetch_gotfriends_html,
    fetch_gotfriends_page,
    fetch_profession_slugs,
    parse_gotfriends_listing,
    resolve_gotfriends_listing_urls,
)

__all__ = [
    "GOTFRIENDS_CATEGORIES",
    "GotfriendsScraper",
    "build_gotfriends_page_url",
    "collect_gotfriends_jobs",
    "fetch_gotfriends_html",
    "fetch_gotfriends_page",
    "fetch_profession_slugs",
    "parse_gotfriends_listing",
    "resolve_gotfriends_listing_urls",
]
