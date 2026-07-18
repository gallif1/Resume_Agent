"""AllJobs (alljobs.co.il) scraper — guest search HTML with pagination."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from browser_utils import browser_http_headers, is_cloudflare_blocked_html
from collection_report import CollectionOutcome
from config import ALLJOBS_BASE_URL, ALLJOBS_MAX_PAGES, ALLJOBS_HTTP_TIMEOUT_SEC
from job_identity import normalize_job_url
from scrapers.base import BaseScraper

_JOB_ID_RE = re.compile(r"JobID=(\d+)", re.IGNORECASE)
_LOCATION_PREFIX_RE = re.compile(r"^\s*מיקום המשרה:\s*", re.UNICODE)


def build_alljobs_search_url(query: str, *, page: int = 1) -> str:
    """Build an AllJobs guest search URL for free-text query + page."""
    params = {
        "page": max(1, int(page)),
        "position": "",
        "type": "",
        "city": "",
        "region": "",
        "freetxt": query,
    }
    return f"{ALLJOBS_BASE_URL}/SearchResultsGuest.aspx?{urlencode(params)}"


def _absolute_alljobs_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"{ALLJOBS_BASE_URL}{href}"
    return f"{ALLJOBS_BASE_URL}/{href.lstrip('/')}"


def _clean_title(raw: str) -> str:
    text = (raw or "").strip()
    # Title attributes often look like: "דרושים | Python Developer"
    if "|" in text:
        text = text.split("|", 1)[1].strip()
    return re.sub(r"\s+", " ", text)


def parse_alljobs_listing(html: str) -> list[dict[str, Any]]:
    """Extract job cards from an AllJobs SearchResultsGuest page."""
    if not html or is_cloudflare_blocked_html(html):
        return []

    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    cards = soup.select(".job-content-top")
    for card in cards:
        link = card.select_one(
            ".job-content-top-title a[href*='JobID='], a[href*='UploadSingle.aspx?JobID=']"
        )
        if link is None:
            continue
        href = _absolute_alljobs_url(link.get("href", ""))
        job_id_match = _JOB_ID_RE.search(href)
        if not job_id_match:
            continue
        job_id = job_id_match.group(1)
        # Canonical application / detail URL.
        job_url = normalize_job_url(
            f"{ALLJOBS_BASE_URL}/Search/UploadSingle.aspx?JobID={job_id}"
        )
        if not job_url or job_url in seen:
            continue

        title = _clean_title(link.get("title") or link.get_text(" ", strip=True))
        if not title:
            continue

        company_el = card.select_one(".job-content-top-title a.T14, a.T14, .T14")
        company = company_el.get_text(" ", strip=True) if company_el else ""

        location_el = card.select_one(".job-content-top-location")
        location = location_el.get_text(" ", strip=True) if location_el else ""
        location = _LOCATION_PREFIX_RE.sub("", location).strip()
        location = re.sub(r"\s+", " ", location)

        desc_el = card.select_one(".job-content-top-desc")
        description = desc_el.get_text(" ", strip=True) if desc_el else ""
        description = re.sub(r"\s+", " ", description)

        seen.add(job_url)
        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "job_url": job_url,
            "source": "alljobs",
            "description": description,
            "apply_url": f"{ALLJOBS_BASE_URL}/SearchResultsGuest.aspx?JobID={job_id}",
        })

    return jobs


class AllJobsScraper(BaseScraper):
    source_id = "alljobs"
    label_he = "אולג'ובס"

    def __init__(self) -> None:
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        headers = browser_http_headers(referer=f"{ALLJOBS_BASE_URL}/")
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        return headers

    def collect(
        self,
        query: str,
        *,
        max_pages: int = ALLJOBS_MAX_PAGES,
        **_kwargs: Any,
    ) -> CollectionOutcome:
        print(f"Searching AllJobs for: {query} (up to {max_pages} page(s))")
        all_jobs: list[dict[str, Any]] = []
        seen: set[str] = set()
        last_status: int | None = None

        # Warm session cookies (AllJobs often sets ASP.NET_SessionId + bot cookies).
        try:
            self._session.get(
                f"{ALLJOBS_BASE_URL}/",
                headers=self._headers(),
                timeout=ALLJOBS_HTTP_TIMEOUT_SEC,
            )
        except requests.RequestException:
            pass

        for page in range(1, max(1, max_pages) + 1):
            url = build_alljobs_search_url(query, page=page)
            print(f"  AllJobs page {page}/{max_pages}: {url}")
            try:
                response = self._session.get(
                    url,
                    headers=self._headers(),
                    timeout=ALLJOBS_HTTP_TIMEOUT_SEC,
                )
            except requests.RequestException as error:
                if all_jobs:
                    print(f"  AllJobs request failed after partial results: {error}")
                    break
                return self.empty_outcome(
                    status="http_error",
                    reason=f"AllJobs request failed: {error}",
                    reason_he=f"אולג'ובס: שגיאת רשת — {error}",
                )

            last_status = response.status_code
            if last_status >= 400:
                if all_jobs:
                    break
                return self.empty_outcome(
                    status="http_error",
                    reason=f"AllJobs returned HTTP {last_status}",
                    reason_he=f"אולג'ובס החזיר שגיאת HTTP {last_status}",
                    http_status=last_status,
                )
            if is_cloudflare_blocked_html(response.text):
                if all_jobs:
                    break
                return self.empty_outcome(
                    status="blocked",
                    reason="AllJobs blocked / challenge page",
                    reason_he="אולג'ובס חסם את הגישה",
                    http_status=last_status,
                )

            page_jobs = parse_alljobs_listing(response.text)
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

            print(f"  AllJobs page {page}: +{added} ({len(page_jobs)} on page)")
            if len(page_jobs) < 10:
                break
            if page < max_pages:
                time.sleep(0.8)

        print(f"  AllJobs returned {len(all_jobs)} job card(s) for '{query}'")
        if all_jobs:
            return self.ok_outcome(all_jobs, http_status=last_status)
        return self.empty_outcome(
            status="empty",
            reason=f"No AllJobs jobs for '{query}'",
            reason_he=f"אולג'ובס: לא נמצאו משרות לחיפוש '{query}'",
            http_status=last_status,
        )


def collect_alljobs_jobs(
    query: str,
    *,
    max_pages: int = ALLJOBS_MAX_PAGES,
) -> CollectionOutcome:
    return AllJobsScraper().collect(query, max_pages=max_pages)
