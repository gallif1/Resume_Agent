"""Geektime Insider careers scraper (insider.geektime.co.il)."""

from __future__ import annotations

import json
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
    GEEKTIME_BASE_URL,
    GEEKTIME_BROWSER_FALLBACK,
    GEEKTIME_HTTP_TIMEOUT_SEC,
    GEEKTIME_MAX_PAGES,
    HEADLESS,
)
from job_identity import normalize_job_url
from scrapers.base import BaseScraper

def build_geektime_jobs_url(query: str = "", *, page: int = 1) -> str:
    params: dict[str, str | int] = {}
    if query.strip():
        params["s"] = query.strip()
        params["search"] = query.strip()
    if page > 1:
        params["page"] = page
    if params:
        return f"{GEEKTIME_BASE_URL}/jobs/?{urlencode(params)}"
    return f"{GEEKTIME_BASE_URL}/jobs/"


def build_geektime_api_urls(query: str, *, page: int = 1) -> list[str]:
    """Candidate WordPress / custom JSON endpoints used by Geektime Insider."""
    q = (query or "").strip()
    per_page = 20
    offset = max(0, (page - 1) * per_page)
    urls = [
        f"{GEEKTIME_BASE_URL}/wp-json/wp/v2/jobs?{urlencode({'per_page': per_page, 'page': page, 'search': q})}",
        f"{GEEKTIME_BASE_URL}/wp-json/wp/v2/job?{urlencode({'per_page': per_page, 'page': page, 'search': q})}",
        f"{GEEKTIME_BASE_URL}/wp-json/geektime/v1/jobs?{urlencode({'per_page': per_page, 'page': page, 'search': q})}",
        f"{GEEKTIME_BASE_URL}/wp-json/wp/v2/posts?{urlencode({'per_page': per_page, 'page': page, 'search': q, 'type': 'jobs'})}",
        f"{GEEKTIME_BASE_URL}/wp-admin/admin-ajax.php?{urlencode({'action': 'get_jobs', 'search': q, 'offset': offset})}",
    ]
    return urls


def _headers(*, accept_json: bool = False) -> dict[str, str]:
    headers = browser_http_headers(referer=f"{GEEKTIME_BASE_URL}/")
    if accept_json:
        headers["Accept"] = "application/json, text/plain, */*"
        headers["Origin"] = GEEKTIME_BASE_URL
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Site"] = "same-origin"
    else:
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    headers["Accept-Language"] = "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7"
    return headers


def _strip_html(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "<" not in text:
        return text
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def _job_url_from_item(item: dict[str, Any]) -> str:
    for key in ("link", "url", "permalink", "guid"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("rendered")
        href = str(value or "").strip()
        if href.startswith("http"):
            return href
    job_id = item.get("id") or item.get("id_job") or item.get("job_id")
    if job_id is not None:
        return f"{GEEKTIME_BASE_URL}/jobs/#jid={job_id}"
    return ""


def parse_geektime_api_jobs(payload: Any) -> list[dict[str, Any]]:
    """Parse Geektime / WordPress JSON job payloads."""
    if isinstance(payload, dict):
        for key in ("jobs", "data", "results", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]

    if not isinstance(payload, list):
        return []

    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if isinstance(title, dict):
            title = title.get("rendered")
        title = _strip_html(title or item.get("name") or "")
        if not title:
            continue

        href = normalize_job_url(_job_url_from_item(item))
        if not href or href in seen:
            continue

        company = str(
            item.get("company")
            or item.get("company_name")
            or item.get("employer")
            or ""
        ).strip()
        location = str(
            item.get("city")
            or item.get("location")
            or item.get("address")
            or ""
        ).strip()
        description = _strip_html(
            item.get("content")
            if not isinstance(item.get("content"), dict)
            else item.get("content", {}).get("rendered")
        ) or _strip_html(item.get("description") or item.get("excerpt") or "")

        seen.add(href)
        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "job_url": href,
            "source": "geektime",
            "description": description,
        })
    return jobs


def parse_geektime_listing(html: str) -> list[dict[str, Any]]:
    """Parse Geektime Insider jobs page HTML / embedded JSON."""
    if not html or is_cloudflare_blocked_html(html):
        return []

    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Embedded Vue/Angular state often ships jobs arrays in script tags.
    for match in re.finditer(
        r"(?:jobs|allJobs|hotJobs)\s*[:=]\s*(\[[\s\S]*?\])\s*[,;]",
        html,
    ):
        raw = match.group(1)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for job in parse_geektime_api_jobs(payload):
            if job["job_url"] not in seen:
                seen.add(job["job_url"])
                jobs.append(job)

    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or ""
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            title = str(item.get("title") or "").strip()
            href = normalize_job_url(str(item.get("url") or ""))
            if not title or not href or href in seen:
                continue
            org = item.get("hiringOrganization") if isinstance(item.get("hiringOrganization"), dict) else {}
            seen.add(href)
            jobs.append({
                "title": title,
                "company": str(org.get("name") or "").strip(),
                "location": "",
                "job_url": href,
                "source": "geektime",
                "description": _strip_html(item.get("description")),
            })

    for card in soup.select(
        "a[href*='/jobs'], a[href*='jid='], .job-card, .job-item, article, .jobs-list a"
    ):
        href = card.get("href") if card.name == "a" else None
        if not href:
            link = card.select_one("a[href]")
            href = link.get("href") if link else ""
        href = urljoin(GEEKTIME_BASE_URL + "/", href or "")
        if "jid=" not in href and "/jobs" not in href:
            continue
        title_el = card.select_one("h2, h3, h4, .title") if card.name != "a" else card
        if card.name == "a":
            title = card.get_text(" ", strip=True)
        else:
            title = title_el.get_text(" ", strip=True) if title_el else ""
        job_url = normalize_job_url(href)
        if not title or len(title) < 3 or not job_url or job_url in seen:
            continue
        company_el = card.select_one(".company, [class*='company']") if card.name != "a" else None
        city_el = card.select_one(".city, [class*='location']") if card.name != "a" else None
        seen.add(job_url)
        jobs.append({
            "title": title,
            "company": company_el.get_text(" ", strip=True) if company_el else "",
            "location": city_el.get_text(" ", strip=True) if city_el else "",
            "job_url": job_url,
            "source": "geektime",
            "description": "",
        })

    return jobs


def _fetch_html(url: str, *, headless: bool = HEADLESS) -> tuple[int, str]:
    try:
        response = requests.get(url, headers=_headers(), timeout=GEEKTIME_HTTP_TIMEOUT_SEC)
        status, html = response.status_code, response.text or ""
    except requests.RequestException as error:
        print(f"  Geektime request error: {error}")
        status, html = 0, ""

    if status == 200 and not is_cloudflare_blocked_html(html):
        return status, html

    if GEEKTIME_BROWSER_FALLBACK and (is_http_blocked(status, html) or not html):
        print(f"  Geektime HTTP {status or 'error'} — retrying with Playwright...")
        try:
            return fetch_html_with_playwright(url, headless=headless, wait_after_load_ms=2500)
        except Exception as error:
            print(f"  Geektime Playwright fallback failed: {error}")
    return status, html


class GeektimeScraper(BaseScraper):
    source_id = "geektime"
    label_he = "גיקטיים"

    def collect(
        self,
        query: str,
        *,
        max_pages: int = GEEKTIME_MAX_PAGES,
        headless: bool = HEADLESS,
        **_kwargs: Any,
    ) -> CollectionOutcome:
        print(f"Searching Geektime Insider for: {query} (up to {max_pages} page(s))")
        all_jobs: list[dict[str, Any]] = []
        seen: set[str] = set()
        last_status: int | None = None
        blocked = False

        for page in range(1, max(1, max_pages) + 1):
            page_jobs: list[dict[str, Any]] = []

            # Prefer clean JSON endpoints when available.
            for api_url in build_geektime_api_urls(query, page=page):
                try:
                    response = requests.get(
                        api_url,
                        headers=_headers(accept_json=True),
                        timeout=GEEKTIME_HTTP_TIMEOUT_SEC,
                    )
                except requests.RequestException:
                    continue
                last_status = response.status_code
                if response.status_code >= 400:
                    if response.status_code in (401, 403, 429):
                        blocked = True
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    continue
                parsed = parse_geektime_api_jobs(payload)
                if parsed:
                    page_jobs = parsed
                    print(f"  Geektime API hit: {api_url}")
                    break

            if not page_jobs:
                html_url = build_geektime_jobs_url(query, page=page)
                print(f"  Geektime HTML page {page}/{max_pages}: {html_url}")
                status, html = _fetch_html(html_url, headless=headless)
                last_status = status or last_status
                if status >= 400 or is_cloudflare_blocked_html(html):
                    blocked = True
                    print(f"  Geektime blocked/empty (HTTP {status})")
                    break
                page_jobs = parse_geektime_listing(html)

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

            print(f"  Geektime page {page}: +{added}")
            if added == 0:
                break
            if page < max_pages:
                time.sleep(0.8)

        print(f"  Geektime returned {len(all_jobs)} job card(s) for '{query}'")
        if all_jobs:
            return self.ok_outcome(all_jobs, http_status=last_status)
        if blocked:
            return self.empty_outcome(
                status="blocked",
                reason="Geektime Insider blocked by Cloudflare / auth",
                reason_he="גיקטיים חסם את הגישה (Cloudflare)",
                http_status=last_status,
            )
        return self.empty_outcome(
            status="empty",
            reason=f"No Geektime jobs for '{query}'",
            reason_he=f"גיקטיים: לא נמצאו משרות לחיפוש '{query}'",
            http_status=last_status,
        )


def collect_geektime_jobs(
    query: str,
    *,
    max_pages: int = GEEKTIME_MAX_PAGES,
    headless: bool = HEADLESS,
) -> CollectionOutcome:
    return GeektimeScraper().collect(query, max_pages=max_pages, headless=headless)
