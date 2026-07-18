"""Secret Tel Aviv Jobs board scraper (jobs.secrettelaviv.com / WP Job Board)."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlencode, urljoin

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
    SECRET_TEL_AVIV_BASE_URL,
    SECRET_TEL_AVIV_BROWSER_FALLBACK,
    SECRET_TEL_AVIV_HTTP_TIMEOUT_SEC,
    SECRET_TEL_AVIV_MAX_PAGES,
)
from job_identity import normalize_job_url
from scrapers.base import BaseScraper

_JOB_PATH_RE = re.compile(r"/job/[^?#]+", re.IGNORECASE)


def build_secret_tel_aviv_search_url(query: str, *, page: int = 1) -> str:
    """Build a Secret Tel Aviv jobs search / list URL."""
    q = (query or "").strip()
    if page <= 1:
        if q:
            return f"{SECRET_TEL_AVIV_BASE_URL}/?{urlencode({'query': q})}"
        return f"{SECRET_TEL_AVIV_BASE_URL}/"
    if q:
        return f"{SECRET_TEL_AVIV_BASE_URL}/page/{page}/?{urlencode({'query': q})}"
    return f"{SECRET_TEL_AVIV_BASE_URL}/page/{page}/"


def _headers() -> dict[str, str]:
    headers = browser_http_headers(referer=f"{SECRET_TEL_AVIV_BASE_URL}/")
    headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    headers["Accept-Language"] = "en-US,en;q=0.9,he;q=0.8"
    return headers


def _absolute_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    return urljoin(SECRET_TEL_AVIV_BASE_URL + "/", href)


def parse_secret_tel_aviv_listing(html: str) -> list[dict[str, Any]]:
    """Parse WP Job Board style listings (and simple card fallbacks)."""
    if not html or is_cloudflare_blocked_html(html):
        return []
    if "just a moment" in html.lower() and "cloudflare" in html.lower():
        return []

    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Prefer structured WPJB rows; fall back to any /job/ anchors.
    rows = soup.select(
        ".wpjb-job-list .wpjb-grid-row, tr.wpjb-job, li.wpjb-job, "
        "article.job, .job-listing, .wpjb-job"
    )
    anchors = soup.select("a[href*='/job/']")

    candidates: list[Any] = list(rows) if rows else []
    if not candidates:
        for anchor in anchors:
            parent = anchor.find_parent(["article", "li", "tr", "div"])
            candidates.append(parent or anchor)

    for node in candidates:
        link = node if getattr(node, "name", None) == "a" else node.select_one("a[href*='/job/']")
        if link is None:
            continue
        href = _absolute_url(link.get("href", ""))
        if not href or not _JOB_PATH_RE.search(href):
            continue
        # External apply redirects still start at the STA job page URL.
        job_url = normalize_job_url(href.split("#")[0])
        if not job_url or job_url in seen:
            continue

        title = link.get_text(" ", strip=True)
        if not title:
            title_el = node.select_one("h2, h3, .wpjb-job-title, .job-title") if node is not link else None
            title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title or len(title) < 3:
            continue

        company = ""
        location = ""
        description = ""
        if node is not link:
            company_el = node.select_one(
                ".wpjb-job-company, .company, .wpjb-grid-col-company, [class*='company']"
            )
            location_el = node.select_one(
                ".wpjb-job-location, .location, .wpjb-grid-col-location, [class*='location']"
            )
            snippet_el = node.select_one(".wpjb-job-excerpt, .excerpt, p")
            company = company_el.get_text(" ", strip=True) if company_el else ""
            location = location_el.get_text(" ", strip=True) if location_el else ""
            description = snippet_el.get_text(" ", strip=True) if snippet_el else ""

        # Detect iframe-heavy application pages later during enrich; store board URL.
        seen.add(job_url)
        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "job_url": job_url,
            "source": "secret_tel_aviv",
            "description": description,
        })

    return jobs


def _fetch_html(url: str, *, headless: bool = HEADLESS) -> tuple[int, str]:
    try:
        response = requests.get(url, headers=_headers(), timeout=SECRET_TEL_AVIV_HTTP_TIMEOUT_SEC)
        status, html = response.status_code, response.text or ""
    except requests.RequestException as error:
        print(f"  Secret Tel Aviv request error: {error}")
        status, html = 0, ""

    if status == 200 and not is_cloudflare_blocked_html(html) and "just a moment" not in html.lower():
        return status, html

    if SECRET_TEL_AVIV_BROWSER_FALLBACK and (
        is_http_blocked(status, html) or is_cloudflare_blocked_html(html) or not html
    ):
        print(f"  Secret Tel Aviv HTTP {status or 'error'} — retrying with Playwright...")
        try:
            return fetch_html_with_playwright(url, headless=headless, wait_after_load_ms=2500)
        except Exception as error:
            print(f"  Secret Tel Aviv Playwright fallback failed: {error}")
    return status, html


class SecretTelAvivScraper(BaseScraper):
    source_id = "secret_tel_aviv"
    label_he = "סיקרט תל אביב"

    def collect(
        self,
        query: str,
        *,
        max_pages: int = SECRET_TEL_AVIV_MAX_PAGES,
        headless: bool = HEADLESS,
        **_kwargs: Any,
    ) -> CollectionOutcome:
        print(f"Searching Secret Tel Aviv for: {query} (up to {max_pages} page(s))")
        all_jobs: list[dict[str, Any]] = []
        seen: set[str] = set()
        last_status: int | None = None
        blocked = False

        for page in range(1, max(1, max_pages) + 1):
            url = build_secret_tel_aviv_search_url(query, page=page)
            print(f"  Secret Tel Aviv page {page}/{max_pages}: {url}")
            status, html = _fetch_html(url, headless=headless)
            last_status = status or last_status

            if status >= 400 or is_cloudflare_blocked_html(html) or "just a moment" in (html or "").lower():
                blocked = True
                print(f"  Secret Tel Aviv blocked/empty (HTTP {status})")
                break

            page_jobs = parse_secret_tel_aviv_listing(html)
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

            print(f"  Secret Tel Aviv page {page}: +{added}")
            if len(page_jobs) < 5:
                break
            if page < max_pages:
                time.sleep(0.8)

        print(f"  Secret Tel Aviv returned {len(all_jobs)} job card(s) for '{query}'")
        if all_jobs:
            return self.ok_outcome(all_jobs, http_status=last_status)
        if blocked:
            return self.empty_outcome(
                status="blocked",
                reason="Secret Tel Aviv blocked by Cloudflare",
                reason_he="סיקרט תל אביב חסם את הגישה (Cloudflare)",
                http_status=last_status,
            )
        return self.empty_outcome(
            status="empty",
            reason=f"No Secret Tel Aviv jobs for '{query}'",
            reason_he=f"סיקרט תל אביב: לא נמצאו משרות לחיפוש '{query}'",
            http_status=last_status,
        )


def collect_secret_tel_aviv_jobs(
    query: str,
    *,
    max_pages: int = SECRET_TEL_AVIV_MAX_PAGES,
    headless: bool = HEADLESS,
) -> CollectionOutcome:
    return SecretTelAvivScraper().collect(query, max_pages=max_pages, headless=headless)
