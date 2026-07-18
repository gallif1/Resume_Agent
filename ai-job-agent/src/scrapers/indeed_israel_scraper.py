"""Indeed Israel (il.indeed.com) scraper — guest job cards with safe query params."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from browser_utils import (
    browser_http_headers,
    fetch_html_with_playwright,
    is_cloudflare_blocked_html,
    is_http_blocked,
)
from collection_report import CollectionOutcome
from config import (
    HEADLESS,
    INDEED_BASE_URL,
    INDEED_BROWSER_FALLBACK,
    INDEED_HTTP_TIMEOUT_SEC,
    INDEED_LOCATION,
    INDEED_MAX_PAGES,
    INDEED_RESULTS_PER_PAGE,
)
from job_identity import normalize_job_url
from scrapers.base import BaseScraper

_JK_RE = re.compile(r"[?&]jk=([a-f0-9]+)", re.IGNORECASE)

_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)


def build_indeed_search_url(query: str, *, start: int = 0) -> str:
    """Build an il.indeed.com search URL with sanitized query parameters."""
    # Keep query params explicit and URL-encoded via urlencode (safe escaping).
    params = {
        "q": (query or "").strip(),
        "l": INDEED_LOCATION,
        "start": max(0, int(start)),
        "fromage": "14",
        "filter": "0",
    }
    return f"{INDEED_BASE_URL}/jobs?{urlencode(params)}"


def _headers(*, user_agent: str | None = None) -> dict[str, str]:
    headers = browser_http_headers(referer=f"{INDEED_BASE_URL}/")
    headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    headers["Accept-Language"] = "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7"
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


def parse_indeed_listing(html: str) -> list[dict[str, Any]]:
    """Parse raw Indeed job cards / result mosaic items from HTML."""
    if not html or is_cloudflare_blocked_html(html):
        return []
    lowered = html.lower()
    if "security check" in lowered and "indeed" in lowered:
        return []

    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    cards = soup.select(
        "div.job_seen_beacon, div.result, li.css-5lfssg, "
        "div.slider_container div.slider_item, div[data-jk], a.jcs-JobTitle"
    )
    # Also collect any direct job title anchors.
    anchors = soup.select("a[data-jk], a.jcs-JobTitle, h2.jobTitle a, a[href*='/viewjob'], a[href*='jk=']")

    nodes: list[Any] = []
    for card in cards:
        nodes.append(card)
    for anchor in anchors:
        parent = anchor.find_parent("div", class_=re.compile(r"job|result|card", re.I))
        nodes.append(parent or anchor)

    for node in nodes:
        link = None
        if getattr(node, "name", None) == "a":
            link = node
        else:
            link = node.select_one(
                "a[data-jk], a.jcs-JobTitle, h2.jobTitle a, a[href*='jk='], a[href*='/viewjob']"
            )
        if link is None:
            continue

        jk = (link.get("data-jk") or "").strip()
        href = (link.get("href") or "").strip()
        if not jk:
            match = _JK_RE.search(href)
            if match:
                jk = match.group(1)
        if not jk and node is not link:
            jk = (node.get("data-jk") or "").strip()
        if not jk:
            continue

        job_url = normalize_job_url(f"{INDEED_BASE_URL}/viewjob?jk={jk}")
        if not job_url or job_url in seen:
            continue

        title = link.get_text(" ", strip=True)
        if not title and node is not link:
            title_el = node.select_one("h2.jobTitle, span[title], .jobTitle")
            title = title_el.get("title") if title_el and title_el.get("title") else (
                title_el.get_text(" ", strip=True) if title_el else ""
            )
        title = (title or link.get("aria-label") or "").strip()
        if not title:
            continue

        company = ""
        location = ""
        description = ""
        if node is not link:
            company_el = node.select_one(
                "[data-testid='company-name'], span.companyName, .company_location .companyName"
            )
            location_el = node.select_one(
                "[data-testid='text-location'], .companyLocation, div.company_location"
            )
            snippet_el = node.select_one(
                "[data-testid='jobsnippet'], .job-snippet, .jobCardShelfContainer"
            )
            company = company_el.get_text(" ", strip=True) if company_el else ""
            location = location_el.get_text(" ", strip=True) if location_el else ""
            description = snippet_el.get_text(" ", strip=True) if snippet_el else ""

        seen.add(job_url)
        jobs.append({
            "title": title,
            "company": company,
            "location": location or INDEED_LOCATION,
            "job_url": job_url,
            "source": "indeed",
            "description": description,
        })

    return jobs


def _fetch_indeed_html(url: str, *, headless: bool = HEADLESS) -> tuple[int, str]:
    last_status = 0
    last_html = ""
    for index, ua in enumerate(_USER_AGENTS):
        try:
            response = requests.get(
                url,
                headers=_headers(user_agent=ua),
                timeout=INDEED_HTTP_TIMEOUT_SEC,
            )
        except requests.RequestException as error:
            print(f"  Indeed request error: {error}")
            last_status = 0
            last_html = ""
            continue
        last_status = response.status_code
        last_html = response.text or ""
        if last_status == 200 and not is_cloudflare_blocked_html(last_html):
            if "security check" not in last_html.lower():
                return last_status, last_html
        if index < len(_USER_AGENTS) - 1:
            time.sleep(1.0)

    if INDEED_BROWSER_FALLBACK and (
        is_http_blocked(last_status, last_html)
        or "security check" in last_html.lower()
        or not last_html
    ):
        print(f"  Indeed HTTP {last_status or 'error'} — retrying with Playwright...")
        try:
            return fetch_html_with_playwright(url, headless=headless, wait_after_load_ms=2500)
        except Exception as error:
            print(f"  Indeed Playwright fallback failed: {error}")

    return last_status, last_html


class IndeedIsraelScraper(BaseScraper):
    source_id = "indeed"
    label_he = "אינדיד"

    def collect(
        self,
        query: str,
        *,
        max_pages: int = INDEED_MAX_PAGES,
        headless: bool = HEADLESS,
        **_kwargs: Any,
    ) -> CollectionOutcome:
        page_size = max(1, INDEED_RESULTS_PER_PAGE)
        print(
            f"Searching Indeed Israel for: {query} "
            f"(location={INDEED_LOCATION}, up to {max_pages} page(s))"
        )
        all_jobs: list[dict[str, Any]] = []
        seen: set[str] = set()
        last_status: int | None = None
        blocked = False

        for page_index in range(max(1, max_pages)):
            start = page_index * page_size
            url = build_indeed_search_url(query, start=start)
            print(f"  Indeed page {page_index + 1}/{max_pages}: start={start}")
            status, html = _fetch_indeed_html(url, headless=headless)
            last_status = status or last_status

            if status >= 400 or is_cloudflare_blocked_html(html) or "security check" in (html or "").lower():
                blocked = True
                print(f"  Indeed blocked/empty response (HTTP {status})")
                break

            page_jobs = parse_indeed_listing(html)
            if not page_jobs:
                break

            added = 0
            for job in page_jobs:
                key = job.get("job_url") or ""
                if not key or key in seen:
                    continue
                seen.add(key)
                all_jobs.append(job)
                added += 1

            print(f"  Indeed page {page_index + 1}: +{added} ({len(page_jobs)} on page)")
            if len(page_jobs) < max(5, page_size // 2):
                break
            if page_index < max_pages - 1:
                time.sleep(1.2)

        print(f"  Indeed returned {len(all_jobs)} job card(s) for '{query}'")
        if all_jobs:
            return self.ok_outcome(all_jobs, http_status=last_status)
        if blocked:
            return self.empty_outcome(
                status="blocked",
                reason="Indeed Israel blocked / security check",
                reason_he="אינדיד חסם את הגישה (Security Check)",
                http_status=last_status,
            )
        return self.empty_outcome(
            status="empty",
            reason=f"No Indeed jobs for '{query}'",
            reason_he=f"אינדיד: לא נמצאו משרות לחיפוש '{query}'",
            http_status=last_status,
        )


def collect_indeed_jobs(
    query: str,
    *,
    max_pages: int = INDEED_MAX_PAGES,
    headless: bool = HEADLESS,
) -> CollectionOutcome:
    return IndeedIsraelScraper().collect(query, max_pages=max_pages, headless=headless)
