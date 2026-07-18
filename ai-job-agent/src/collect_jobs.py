from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

from browser_utils import (
    browser_http_headers,
    create_browser_context,
    is_cloudflare_blocked_html,
    page_looks_blocked,
)
from collection_report import (
    CollectionOutcome,
    emit_agent_warning,
    emit_collect_summary,
    outcome_to_dict,
)
from config import (
    AGENT_CV_ID,
    ALLJOBS_MAX_PAGES,
    COLLECT_MAX_CATEGORIES,
    COLLECT_MAX_QUERIES,
    DRUSHIM_API_BASE_URL,
    DRUSHIM_BASE_URL,
    DRUSHIM_BROWSER_FALLBACK,
    DRUSHIM_GOTO_TIMEOUT_MS,
    DRUSHIM_HTTP_FIRST,
    DRUSHIM_HTTP_TIMEOUT_SEC,
    DRUSHIM_MAX_PAGES,
    DRUSHIM_PAGE_WAIT_MS,
    DRUSHIM_SELECTOR_TIMEOUT_MS,
    GEEKTIME_MAX_PAGES,
    GOTFRIENDS_MAX_PAGES,
    HEADLESS,
    INDEED_MAX_PAGES,
    LINKEDIN_BASE_URL,
    LINKEDIN_GEO_ID,
    LINKEDIN_JOBS_PER_PAGE,
    LINKEDIN_LOCATION,
    LINKEDIN_MAX_PAGES,
    LINKEDIN_MAX_RETRIES,
    LOGS_DIR,
    SECRET_TEL_AVIV_MAX_PAGES,
)
from date_utils import normalize_posted_date, pick_raw_posted_date
from db import get_known_job_identity_keys, init_db, upsert_collected_job
from job_boards import collection_searches, job_boards_label, normalize_job_board_ids
from scrapers.alljobs_scraper import collect_alljobs_jobs
from scrapers.geektime_scraper import collect_geektime_jobs
from scrapers.gotfriends_scraper import collect_gotfriends_jobs
from scrapers.indeed_israel_scraper import collect_indeed_jobs
from scrapers.secret_tel_aviv_scraper import collect_secret_tel_aviv_jobs
from job_identity import (
    compute_candidate_strategy_hash,
    compute_job_identity_key,
    extract_linkedin_job_id,
    normalize_job_url,
)
from profile_utils import load_profile
from query_builder import queries_for_board
from role_analyzer import (
    collection_plan_from_roles,
    get_collection_query_plan,
    get_collection_roles,
    load_ai_roles,
    load_matching_strategy,
)

DEFAULT_MAX_QUERIES_PER_CATEGORY = COLLECT_MAX_QUERIES

SITE_LABELS_HE = {
    "drushim": "דרושים",
    "linkedin": "לינקדאין",
    "gotfriends": "GotFriends",
    "alljobs": "אולג'ובס",
    "indeed": "אינדיד",
    "secret_tel_aviv": "סיקרט תל אביב",
    "geektime": "גיקטיים",
}

# Boards that expect English search terms (Hebrew queries return near-zero hits).
_ENGLISH_QUERY_SITES = frozenset({
    "linkedin",
    "gotfriends",
    "indeed",
    "secret_tel_aviv",
    "geektime",
})


def _interactive_retry_enabled() -> bool:
    """Allow manual browser retry only in an interactive terminal (not from the web UI)."""
    return sys.stdin.isatty() and not AGENT_CV_ID


def _site_label(site_name: str) -> str:
    return SITE_LABELS_HE.get(site_name, site_name)


@dataclass
class _SiteTotals:
    raw: int = 0
    new: int = 0
    already_in_db: int = 0
    excluded: int = 0
    queries: int = 0
    queries_with_raw: int = 0
    issues: list[str] = field(default_factory=list)


def _note_site_issue(totals: dict[str, _SiteTotals], site_name: str, message: str) -> None:
    site = totals.setdefault(site_name, _SiteTotals())
    if message not in site.issues:
        site.issues.append(message)


def _finalize_site_warnings(totals: dict[str, _SiteTotals]) -> list[str]:
    warnings: list[str] = []
    for site_name, site in totals.items():
        label = _site_label(site_name)
        if site.queries == 0:
            continue
        if site.raw == 0:
            if site.issues:
                warnings.append(f"{label}: לא נמצאו משרות. {site.issues[0]}")
            else:
                warnings.append(
                    f"{label}: לא נמצאו משרות בכל {site.queries} החיפושים. "
                    "ייתכן שהאתר חסם את הגישה או שאין תוצאות לשאילתות."
                )
            continue
        if site.new == 0 and site.already_in_db > 0:
            warnings.append(
                f"{label}: נמצאו {site.raw} משרות, אך כולן כבר קיימות במסד הנתונים "
                f"({site.already_in_db}). לא נוספו משרות חדשות בסריקה זו."
            )
        elif site.new == 0 and site.excluded > 0 and site.raw == site.excluded:
            warnings.append(
                f"{label}: נמצאו {site.raw} משרות, אך כולן סוננו לפי מילות מפתח שליליות."
            )
        elif site.new == 0:
            warnings.append(
                f"{label}: נמצאו {site.raw} משרות בחיפוש, אך לא נוספה אף משרה חדשה."
            )
        for issue in site.issues[1:]:
            warnings.append(f"{label}: {issue}")
    return warnings

EXTRACT_JOBS_JS = """
() => {
    const items = [...document.querySelectorAll(".job-item")];
    const jobs = [];

    for (const item of items) {
        const title =
            item.querySelector("h3 .job-url, h3 span")?.innerText?.trim() || "";
        const company =
            item.querySelector(".job-details-top a span")?.innerText?.trim() || "";
        const location = (
            item.querySelector(".job-details-sub .display-18 span")?.innerText?.trim() || ""
        ).replace(/\\s*\\|\\s*$/, "");
        const description =
            item.querySelector(".job-intro p, .vacancyMain p")?.innerText?.trim() || "";
        const link = item.querySelector('a[href*="/job/"]');
        const href = link
            ? new URL(link.getAttribute("href"), window.location.origin).href
            : "";
        const dateEl = item.querySelector(
            "time[datetime], .job-details-sub time, [class*='date'], [class*='Date'], .display-14"
        );
        const posted_date =
            dateEl?.getAttribute?.("datetime") ||
            dateEl?.innerText?.trim() ||
            "";

        if (!title || !href) {
            continue;
        }

        jobs.push({
            title,
            company,
            location,
            job_url: href,
            source: "drushim",
            description: description || "",
            posted_date: posted_date || "",
        });
    }

    return jobs;
}
"""


def _extract_drushim_posted_raw(item: dict[str, Any], content: dict, info: dict) -> Any:
    """Pull the best available publication-date field from a Drushim API card.

    Absolute ISO/timestamp fields are preferred over Hebrew relative strings.
    """
    return pick_raw_posted_date(
        info.get("DateActual"),
        info.get("dateActual"),
        info.get("FirstPublishDate"),
        info.get("firstPublishDate"),
        info.get("PublishDate"),
        info.get("publishDate"),
        info.get("CreateDate"),
        info.get("createDate"),
        info.get("UpdateDate"),
        info.get("updateDate"),
        info.get("Date"),
        content.get("DateActual"),
        content.get("dateActual"),
        content.get("FirstPublishDate"),
        item.get("datePosted"),
        item.get("DatePosted"),
        item.get("postedAt"),
        item.get("PostedAt"),
        info.get("JumpDateString"),
        info.get("jumpDateString"),
        content.get("JumpDateString"),
        item.get("JumpDateString"),
    )


def build_drushim_search_url(query: str) -> str:
    """Build a Drushim HTML search URL for the given query."""
    return f"{DRUSHIM_BASE_URL}/jobs/search/?searchterm={quote(query)}"


def build_drushim_api_search_url(query: str, *, page: int | None = None) -> str:
    """Build a Drushim JSON search API URL.

    Omitting ``page`` returns the first SSR-sized batch (~20–24 jobs).
    ``page=1`` and up return subsequent pages (~10 jobs each).
    """
    params: dict[str, str | int] = {"searchterm": query}
    if page is not None and page > 0:
        params["page"] = page
    return f"{DRUSHIM_API_BASE_URL}/api/jobs/search?{urlencode(params)}"


def _strip_html_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "<" not in text:
        return text
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def parse_drushim_api_jobs(payload: dict[str, Any] | list[Any] | None) -> list[dict]:
    """Parse job cards from a Drushim JSON search API response."""
    if isinstance(payload, list):
        result_list = payload
    elif isinstance(payload, dict):
        result_list = payload.get("ResultList") or []
    else:
        return []
    if not isinstance(result_list, list):
        return []

    jobs: list[dict] = []
    seen_urls: set[str] = set()
    for item in result_list:
        if not isinstance(item, dict):
            continue
        content = item.get("JobContent") if isinstance(item.get("JobContent"), dict) else {}
        company_obj = item.get("Company") if isinstance(item.get("Company"), dict) else {}
        info = item.get("JobInfo") if isinstance(item.get("JobInfo"), dict) else {}

        code = (
            item.get("Code")
            or content.get("JobCode")
            or info.get("JobCode")
        )
        title = str(
            content.get("Name")
            or content.get("FullName")
            or (item.get("JobAnalytics") or {}).get("name")
            or ""
        ).strip()
        if not title:
            continue

        href = str(info.get("Link") or "").strip()
        if not href and code is not None:
            job_hash = str(info.get("Hash") or "").strip().lower()
            href = f"/job/{code}/{job_hash}/" if job_hash else f"/job/{code}/"
        if not href:
            continue

        if href.startswith("/"):
            job_url = f"{DRUSHIM_BASE_URL}{href}"
        elif href.startswith("http"):
            job_url = href
        else:
            job_url = f"{DRUSHIM_BASE_URL}/{href.lstrip('/')}"

        canonical = normalize_job_url(job_url)
        if not canonical or canonical in seen_urls:
            continue
        seen_urls.add(canonical)

        regions = content.get("Regions") if isinstance(content.get("Regions"), list) else []
        location_parts = [
            str(region.get("NameInHebrew") or "").strip()
            for region in regions
            if isinstance(region, dict) and region.get("NameInHebrew")
        ]
        location = ", ".join(location_parts[:3])

        company = str(
            company_obj.get("CompanyDisplayName")
            or company_obj.get("NameInHebrew")
            or ""
        ).strip()
        description = _strip_html_text(content.get("Description"))
        posted_date = normalize_posted_date(
            _extract_drushim_posted_raw(item, content, info),
            default_to_today=True,
        )

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "job_url": canonical,
            "source": "drushim",
            "description": description,
            "posted_date": posted_date,
        })

    return jobs


def parse_drushim_search_html(html: str) -> list[dict]:
    """Parse job cards from a Drushim search results HTML page."""
    if not html or is_cloudflare_blocked_html(html):
        return []

    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []

    for item in soup.select(".job-item"):
        title_el = item.select_one("h3 .job-url, h3 span")
        company_el = item.select_one(".job-details-top a span")
        location_el = item.select_one(".job-details-sub .display-18 span")
        description_el = item.select_one(".job-intro p, .vacancyMain p")
        link_el = item.select_one('a[href*="/job/"]')
        date_el = item.select_one(
            "time[datetime], .job-details-sub time, [class*='date'], [class*='Date']"
        )

        title = title_el.get_text(strip=True) if title_el else ""
        href = (link_el.get("href") or "").strip() if link_el else ""
        if not title or not href:
            continue

        if href.startswith("/"):
            job_url = f"{DRUSHIM_BASE_URL}{href}"
        elif href.startswith("http"):
            job_url = href
        else:
            job_url = f"{DRUSHIM_BASE_URL}/{href.lstrip('/')}"

        location = location_el.get_text(strip=True) if location_el else ""
        location = location.rstrip("|").strip()

        raw_date = ""
        if date_el is not None:
            raw_date = (date_el.get("datetime") or date_el.get_text(strip=True) or "").strip()
        if not raw_date:
            # Fallback: Hebrew relative fragments often sit in the details sub-row.
            for span in item.select(".job-details-sub span, .display-14, .display-16"):
                text = span.get_text(strip=True)
                if text and (
                    "היום" in text
                    or "אתמול" in text
                    or "לפני" in text
                    or re_search_date(text)
                ):
                    raw_date = text
                    break

        jobs.append({
            "title": title,
            "company": company_el.get_text(strip=True) if company_el else "",
            "location": location,
            "job_url": job_url,
            "source": "drushim",
            "description": description_el.get_text(strip=True) if description_el else "",
            "posted_date": normalize_posted_date(raw_date, default_to_today=True),
        })

    return jobs


def re_search_date(text: str) -> bool:
    """True when text looks like a short numeric/relative date fragment."""
    return bool(
        re.search(r"\d{1,2}[./-]\d{1,2}", text)
        or re.search(r"\d+\s*(יום|ימים|שעה|שעות|שבוע)", text)
    )


def collect_drushim_jobs_api(
    query: str,
    *,
    max_pages: int = DRUSHIM_MAX_PAGES,
) -> CollectionOutcome:
    """Fetch Drushim search results via the paginated JSON API."""
    print(f"Searching Drushim (API) for: {query} (max {max_pages} page(s))")
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()
    last_status: int | None = None
    pages_fetched = 0

    # Page schedule: initial batch (no page param), then page=1..N-1.
    page_numbers: list[int | None] = [None]
    if max_pages > 1:
        page_numbers.extend(range(1, max_pages))

    headers = {
        **browser_http_headers(referer=f"{DRUSHIM_BASE_URL}/"),
        "Accept": "application/json, text/plain, */*",
        "Origin": DRUSHIM_BASE_URL,
    }

    for index, page in enumerate(page_numbers):
        url = build_drushim_api_search_url(query, page=page)
        try:
            response = requests.get(url, headers=headers, timeout=DRUSHIM_HTTP_TIMEOUT_SEC)
        except requests.RequestException as error:
            if all_jobs:
                print(f"  Drushim API page request failed after partial results: {error}")
                break
            return CollectionOutcome(
                status="http_error",
                reason=f"Drushim API request failed: {error}",
                reason_he=f"דרושים: שגיאת רשת — {error}",
            )

        last_status = response.status_code
        if last_status == 429:
            print("  Drushim API rate limit hit (429) — stopping this query.")
            break
        if last_status >= 400:
            if all_jobs:
                print(f"  Drushim API returned HTTP {last_status} — stopping pagination.")
                break
            return CollectionOutcome(
                status="http_error",
                reason=f"Drushim API returned HTTP {last_status}",
                reason_he=f"דרושים החזיר שגיאת HTTP {last_status}",
                http_status=last_status,
            )

        try:
            payload = response.json()
        except ValueError:
            if all_jobs:
                break
            return CollectionOutcome(
                status="http_error",
                reason="Drushim API returned non-JSON response",
                reason_he="דרושים: תגובת API לא תקינה",
                http_status=last_status,
            )

        page_jobs = parse_drushim_api_jobs(payload if isinstance(payload, dict) else None)
        if not page_jobs:
            break

        added = 0
        for job in page_jobs:
            url_key = job.get("job_url") or ""
            if not url_key or url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            all_jobs.append(job)
            added += 1

        pages_fetched += 1
        next_page = payload.get("NextPageNumber") if isinstance(payload, dict) else None
        # API uses NextPageNumber=-1 when there are no further pages.
        if added == 0 or next_page in (-1, "-1"):
            break
        if index < len(page_numbers) - 1:
            time.sleep(0.75)

    if all_jobs:
        print(
            f"  Drushim API extracted {len(all_jobs)} job card(s) "
            f"across {pages_fetched} page(s) for '{query}'"
        )
        return CollectionOutcome(jobs=all_jobs, status="ok", http_status=last_status)

    return CollectionOutcome(
        status="empty",
        reason="No job cards found in Drushim API response",
        reason_he=f"דרושים: לא נמצאו משרות לחיפוש '{query}'",
        http_status=last_status,
    )


def collect_drushim_jobs_http(query: str) -> CollectionOutcome:
    """Fetch Drushim search results with plain HTTP (API first, HTML fallback)."""
    api_outcome = collect_drushim_jobs_api(query, max_pages=DRUSHIM_MAX_PAGES)
    if api_outcome.status == "ok" and api_outcome.jobs:
        return api_outcome

    search_url = build_drushim_search_url(query)
    print(f"Searching Drushim (HTTP HTML fallback) for: {query}")
    print(f"URL: {search_url}")

    try:
        response = requests.get(
            search_url,
            headers=browser_http_headers(),
            timeout=DRUSHIM_HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as error:
        # Prefer the richer API failure reason when the API already failed.
        if api_outcome.status != "empty":
            return api_outcome
        reason = f"Drushim HTTP request failed: {error}"
        return CollectionOutcome(
            status="http_error",
            reason=reason,
            reason_he=f"דרושים: שגיאת רשת — {error}",
        )

    http_status = response.status_code
    if http_status >= 400:
        if api_outcome.status != "empty":
            return api_outcome
        return CollectionOutcome(
            status="http_error",
            reason=f"Drushim returned HTTP {http_status}",
            reason_he=f"דרושים החזיר שגיאת HTTP {http_status}",
            http_status=http_status,
        )

    if is_cloudflare_blocked_html(response.text):
        if api_outcome.status != "empty":
            return api_outcome
        return CollectionOutcome(
            status="blocked",
            reason="Cloudflare block page",
            reason_he="דרושים חסם את הגישה (Cloudflare)",
            http_status=http_status,
        )

    jobs = parse_drushim_search_html(response.text)
    if jobs:
        print(f"  Drushim HTML extracted {len(jobs)} job card(s) for '{query}'")
        return CollectionOutcome(jobs=jobs, status="ok")

    return api_outcome if api_outcome.status != "ok" else CollectionOutcome(
        status="empty",
        reason="No job cards found in HTTP response",
        reason_he=f"דרושים: לא נמצאו משרות לחיפוש '{query}'",
        http_status=http_status,
    )


def _drushim_uses_browser() -> bool:
    """True when a shared Playwright session should be warmed up.

    Collection prefers the paginated JSON API, so we no longer keep Chromium
    open for the whole run. Browser fallback launches on demand inside
    ``collect_drushim_jobs`` when enabled.
    """
    return False


def save_debug_artifacts(page: Page, reason: str) -> Path:
    """Save a screenshot and log file when extraction fails."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"drushim_{timestamp}.log"
    screenshot_path = LOGS_DIR / f"drushim_{timestamp}.png"

    page.screenshot(path=str(screenshot_path), full_page=True)
    log_path.write_text(
        f"Time: {datetime.now().isoformat()}\n"
        f"Reason: {reason}\n"
        f"URL: {page.url}\n"
        f"Title: {page.title()}\n"
        f"Screenshot: {screenshot_path.name}\n",
        encoding="utf-8",
    )

    print(f"Saved debug log: {log_path}")
    print(f"Saved screenshot: {screenshot_path}")
    return log_path


def page_looks_blocked_drushim(page: Page) -> bool:
    """Detect Drushim anti-bot pages without false positives from meta robots."""
    return page_looks_blocked(page)


def extract_jobs_from_page(page: Page) -> list[dict]:
    """Extract job cards from the current Drushim search page."""
    return page.evaluate(EXTRACT_JOBS_JS)


def _collect_drushim_with_page(
    page: Page,
    query: str,
    *,
    headless: bool,
    allow_visible_retry: bool = True,
) -> CollectionOutcome:
    """Extract Drushim jobs using an existing Playwright page."""
    search_url = build_drushim_search_url(query)
    print(f"Searching Drushim for: {query}")
    print(f"URL: {search_url}")
    print(f"Browser mode: {'headless' if headless else 'visible'}")

    response = page.goto(
        search_url, wait_until="domcontentloaded", timeout=DRUSHIM_GOTO_TIMEOUT_MS
    )
    page.wait_for_timeout(DRUSHIM_PAGE_WAIT_MS)
    http_status = response.status if response is not None else None

    if http_status is not None and http_status >= 400:
        reason = f"Drushim returned HTTP {http_status}"
        debug = save_debug_artifacts(page, reason)
        if headless and allow_visible_retry and _interactive_retry_enabled():
            print(f"{reason}. Retrying with a visible browser...")
            return collect_drushim_jobs(query, headless=False)
        return CollectionOutcome(
            status="http_error",
            reason=reason,
            reason_he=f"דרושים החזיר שגיאת HTTP {http_status}",
            http_status=http_status,
            debug_artifact=str(debug),
        )

    try:
        page.wait_for_selector(".job-item", timeout=DRUSHIM_SELECTOR_TIMEOUT_MS)
    except Exception:
        if page_looks_blocked_drushim(page):
            reason = "Page may be blocked by captcha or anti-bot protection"
            reason_he = "דרושים חסם את הגישה (captcha / anti-bot)"
            status = "blocked"
        else:
            reason = "No job cards found on the page"
            reason_he = f"דרושים: לא נמצאו כרטיסי משרות לחיפוש '{query}'"
            status = "empty"
        debug = save_debug_artifacts(page, reason)
        emit_agent_warning(reason_he)

        if headless and allow_visible_retry and _interactive_retry_enabled():
            print("Headless extraction failed. Retrying with a visible browser...")
            return collect_drushim_jobs(query, headless=False)

        if _interactive_retry_enabled():
            print("Inspect the browser window, then press Enter to retry extraction.")
            input()
            jobs = extract_jobs_from_page(page)
            if jobs:
                return CollectionOutcome(jobs=jobs, status="ok")
            save_debug_artifacts(page, "Extraction still failed after manual inspection")
        return CollectionOutcome(
            status=status,
            reason=reason,
            reason_he=reason_he,
            http_status=http_status,
            debug_artifact=str(debug),
        )

    jobs = extract_jobs_from_page(page)
    if jobs:
        print(f"  Drushim extracted {len(jobs)} job card(s) for '{query}'")
        return CollectionOutcome(jobs=jobs, status="ok")

    reason = "Job cards were present but fields could not be parsed"
    reason_he = "דרושים: נמצאו כרטיסי משרות אך לא ניתן לחלץ את הנתונים (ייתכן שהאתר שינה מבנה)"
    debug = save_debug_artifacts(page, reason)
    emit_agent_warning(reason_he)

    if headless and allow_visible_retry and _interactive_retry_enabled():
        print("Could not parse jobs in headless mode. Retrying visibly...")
        return collect_drushim_jobs(query, headless=False)

    if _interactive_retry_enabled():
        print("Inspect the browser window, then press Enter to retry extraction.")
        input()
        jobs = extract_jobs_from_page(page)
        if jobs:
            return CollectionOutcome(jobs=jobs, status="ok")
        save_debug_artifacts(page, "Extraction still failed after manual inspection")

    return CollectionOutcome(
        status="parse_error",
        reason=reason,
        reason_he=reason_he,
        http_status=http_status,
        debug_artifact=str(debug),
    )


class DrushimBrowserSession:
    """Reuse one Chromium instance across many Drushim searches in a single run."""

    def __init__(self, headless: bool = HEADLESS) -> None:
        self.headless = headless
        self._playwright = None
        self._context = None
        self.page: Page | None = None

    def __enter__(self) -> DrushimBrowserSession:
        self._playwright = sync_playwright().start()
        self._context, self.page = create_browser_context(
            self._playwright, headless=self.headless
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()

    def collect(self, query: str) -> CollectionOutcome:
        if self.page is None:
            raise RuntimeError("Drushim browser session is not open")
        # Prefer paginated API even when a browser session is open for fallback.
        api_outcome = collect_drushim_jobs_api(query, max_pages=DRUSHIM_MAX_PAGES)
        if api_outcome.status == "ok" and api_outcome.jobs:
            return api_outcome
        return _collect_drushim_with_page(
            self.page, query, headless=self.headless, allow_visible_retry=False
        )


def collect_drushim_jobs(
    query: str,
    headless: bool = HEADLESS,
    *,
    page: Page | None = None,
) -> CollectionOutcome:
    """Collect Drushim jobs — paginated JSON API first, then HTML/browser fallback."""
    if page is not None:
        return _collect_drushim_with_page(page, query, headless=headless)

    # Always try the paginated JSON API (and HTML SSR fallback) before Chromium.
    # The HTML search page alone capped results at ~20–24 jobs per query.
    http_outcome = collect_drushim_jobs_http(query)
    if http_outcome.status == "ok" and http_outcome.jobs:
        return http_outcome
    if not DRUSHIM_BROWSER_FALLBACK:
        if http_outcome.reason_he:
            emit_agent_warning(http_outcome.reason_he)
        return http_outcome
    print(f"  Drushim HTTP {http_outcome.status} — falling back to browser...")

    with DrushimBrowserSession(headless=headless) as session:
        assert session.page is not None
        return _collect_drushim_with_page(
            session.page,
            query,
            headless=headless,
            allow_visible_retry=_interactive_retry_enabled(),
        )


LINKEDIN_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
)


def _linkedin_headers(*, user_agent: str | None = None) -> dict[str, str]:
    """Browser-like headers for LinkedIn guest job search."""
    headers = browser_http_headers(referer=f"{LINKEDIN_BASE_URL}/jobs/search/")
    headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    headers["Sec-Fetch-Site"] = "same-origin"
    headers["Sec-Fetch-Mode"] = "cors"
    headers["Sec-Fetch-Dest"] = "empty"
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


def build_linkedin_search_url(query: str, start: int = 0) -> str:
    """Build a LinkedIn guest jobs-search API URL (no login required).

    Uses broad location defaults (Israel + geoId). No seniority / experience
    filters — those belong in matching, not collection.
    """
    params: dict[str, str | int] = {
        "keywords": query,
        "location": LINKEDIN_LOCATION,
        "start": max(0, int(start)),
    }
    if LINKEDIN_GEO_ID:
        params["geoId"] = LINKEDIN_GEO_ID
    return f"{LINKEDIN_BASE_URL}/jobs-guest/jobs/api/seeMoreJobPostings/search?{urlencode(params)}"


def _linkedin_response_blocked(status_code: int, html: str) -> str | None:
    """Return a human reason when LinkedIn blocked/throttled the guest API."""
    if status_code == 429:
        return "rate_limited_429"
    if status_code in (401, 403):
        return f"blocked_http_{status_code}"
    if status_code >= 500:
        return f"server_error_{status_code}"
    lowered = (html or "").lower()
    if not lowered.strip():
        return "empty_body"
    if "authwall" in lowered or "session_redirect" in lowered:
        return "authwall"
    if "captcha" in lowered or "challenge" in lowered:
        return "challenge"
    if is_cloudflare_blocked_html(html):
        return "cloudflare"
    return None


def _parse_linkedin_cards(html: str) -> list[dict]:
    """Parse job cards from a LinkedIn guest search response."""
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    cards = soup.select("div.base-card") or soup.select("li")
    for card in cards:
        link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        if link is None:
            continue
        href = (link.get("href") or "").strip()
        if not href or "/jobs/view/" not in href:
            continue

        linkedin_id = extract_linkedin_job_id(href)
        if linkedin_id:
            if linkedin_id in seen_ids:
                continue
            seen_ids.add(linkedin_id)
            href = f"https://www.linkedin.com/jobs/view/{linkedin_id}"

        title_el = card.select_one("h3.base-search-card__title, h3")
        company_el = card.select_one("h4.base-search-card__subtitle a, h4 a, h4")
        location_el = card.select_one("span.job-search-card__location")
        date_el = card.select_one(
            "time.job-search-card__listdate, "
            "time.job-search-card__listdate--new, "
            "time[datetime]"
        )

        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        raw_date = ""
        if date_el is not None:
            raw_date = (date_el.get("datetime") or date_el.get_text(strip=True) or "").strip()

        jobs.append({
            "title": title,
            "company": company_el.get_text(strip=True) if company_el else "",
            "location": location_el.get_text(strip=True) if location_el else "",
            "job_url": href,
            "source": "linkedin",
            "description": "",
            "posted_date": normalize_posted_date(raw_date, default_to_today=True),
        })

    return jobs


def _fetch_linkedin_page(
    url: str,
    *,
    page_index: int,
    max_retries: int = LINKEDIN_MAX_RETRIES,
) -> tuple[int, str, str | None]:
    """GET one LinkedIn guest page with UA rotation and exponential backoff.

    Returns (status_code, body, error_reason_or_none).
    """
    last_status = 0
    last_body = ""
    last_reason: str | None = None

    for attempt in range(max(1, max_retries)):
        ua = LINKEDIN_USER_AGENTS[attempt % len(LINKEDIN_USER_AGENTS)]
        headers = _linkedin_headers(user_agent=ua)
        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as error:
            last_reason = f"request_error:{error}"
            wait_s = min(8.0, 1.5 * (2 ** attempt))
            print(
                f"  LinkedIn request error (page {page_index + 1}, "
                f"attempt {attempt + 1}/{max_retries}): {error} — retry in {wait_s:.1f}s"
            )
            time.sleep(wait_s)
            continue

        last_status = response.status_code
        last_body = response.text or ""
        block_reason = _linkedin_response_blocked(last_status, last_body)
        if block_reason in ("rate_limited_429", "server_error_503", "blocked_http_403") or (
            block_reason and block_reason.startswith("server_error_")
        ):
            last_reason = block_reason
            wait_s = min(20.0, 2.0 * (2 ** attempt))
            print(
                f"  LinkedIn {block_reason} (page {page_index + 1}, "
                f"attempt {attempt + 1}/{max_retries}) — backoff {wait_s:.1f}s"
            )
            time.sleep(wait_s)
            continue

        if block_reason:
            return last_status, last_body, block_reason
        return last_status, last_body, None

    return last_status, last_body, last_reason or "retries_exhausted"


def collect_linkedin_jobs(
    query: str,
    max_pages: int = LINKEDIN_MAX_PAGES,
) -> CollectionOutcome:
    """Fetch job cards from LinkedIn's public guest search API with pagination."""
    location_label = LINKEDIN_LOCATION
    if LINKEDIN_GEO_ID:
        location_label = f"{LINKEDIN_LOCATION} (geoId={LINKEDIN_GEO_ID})"
    print(
        f"Searching LinkedIn for: {query} "
        f"(location: {location_label}, up to {max_pages} page(s), "
        f"~{LINKEDIN_JOBS_PER_PAGE}/page)"
    )
    all_jobs: list[dict] = []
    seen_ids: set[str] = set()
    page_size = max(1, LINKEDIN_JOBS_PER_PAGE)
    last_status: int | None = None

    for page_index in range(max_pages):
        start = page_index * page_size
        url = build_linkedin_search_url(query, start=start)
        print(f"  LinkedIn page {page_index + 1}/{max_pages}: start={start}")
        print(f"  URL: {url}")

        status, body, error_reason = _fetch_linkedin_page(url, page_index=page_index)
        last_status = status

        if error_reason:
            reason_he = {
                "rate_limited_429": "לינקדאין חסם/הגביל בקשות (429)",
                "authwall": "לינקדאין דרש התחברות (authwall)",
                "challenge": "לינקדאין הציג אתגר אבטחה",
                "cloudflare": "לינקדאין חסום ע״י Cloudflare",
                "empty_body": "לינקדאין החזיר תשובה ריקה",
            }.get(error_reason, f"לינקדאין: {error_reason}")
            if all_jobs:
                print(
                    f"  LinkedIn stopped pagination after partial results "
                    f"({error_reason}, HTTP {status}). Keeping {len(all_jobs)} job(s)."
                )
                break
            print(f"  LinkedIn BLOCKED/empty for '{query}': {error_reason} (HTTP {status})")
            return CollectionOutcome(
                status="blocked" if "429" in error_reason or "auth" in error_reason or "challenge" in error_reason else "http_error",
                reason=f"LinkedIn {error_reason} (HTTP {status})",
                reason_he=reason_he,
                http_status=status or None,
            )

        page_jobs = _parse_linkedin_cards(body)
        if not page_jobs:
            print(
                f"  LinkedIn page {page_index + 1}: 0 parseable cards "
                f"(HTTP {status}, body {len(body)} bytes) — stopping."
            )
            break

        # Adapt to the guest API's actual page size (currently ~10, not 25).
        if page_index == 0:
            page_size = max(len(page_jobs), 1)

        added = 0
        for job in page_jobs:
            job_id = extract_linkedin_job_id(job.get("job_url"))
            key = job_id or normalize_job_url(job.get("job_url"))
            if not key or key in seen_ids:
                continue
            seen_ids.add(key)
            all_jobs.append(job)
            added += 1

        print(f"  LinkedIn page {page_index + 1}: +{added} new ({len(page_jobs)} on page)")
        if len(page_jobs) < page_size:
            break
        if page_index < max_pages - 1:
            time.sleep(1.5)

    print(f"  LinkedIn returned {len(all_jobs)} job card(s) for '{query}'")
    if not all_jobs:
        return CollectionOutcome(
            status="empty",
            reason="No LinkedIn job cards parsed",
            reason_he=f"לינקדאין: לא נמצאו משרות לחיפוש '{query}'",
            http_status=last_status,
        )
    return CollectionOutcome(jobs=all_jobs, status="ok", http_status=last_status)


def save_jobs_to_db(
    jobs: list[dict],
    *,
    source_query: str,
    source_category: str,
    source_strategy_hash: str | None,
    exclude_keywords: list[str] | None = None,
    seen_job_keys: set[str],
    known_db_keys: set[str],
    touched_job_keys: set[str],
) -> tuple[int, int, int, int, int, int, int]:
    """Upsert jobs into SQLite with strict run-level deduplication.

    Already-known jobs (by identity key / UNIQUE job_url) are lightly touched
    only — Enrich and Match must not re-process them on subsequent agent runs.

    Returns:
        (raw_found, unique_processed, duplicates_skipped, already_in_db,
         excluded, inserted, touched_once)
    """
    del exclude_keywords  # retained in signature for call-site compatibility
    raw_found = len(jobs)
    unique_processed = 0
    duplicates_skipped = 0
    already_in_db = 0
    excluded = 0
    inserted = 0
    touched_once = 0

    for job in jobs:
        title = job.get("title", "")
        company = job.get("company", "")
        location = job.get("location", "")
        url = normalize_job_url(job.get("job_url", ""))
        if not url:
            continue

        job_key = compute_job_identity_key(url, title, company, location)
        if job_key in seen_job_keys:
            duplicates_skipped += 1
            continue

        already_known = job_key in known_db_keys
        seen_job_keys.add(job_key)
        unique_processed += 1
        posted_date = normalize_posted_date(
            job.get("posted_date"), default_to_today=True
        )

        job_id, is_new = upsert_collected_job(
            title=title,
            job_url=url,
            company=company,
            location=location,
            source=job.get("source"),
            description=job.get("description"),
            source_query=source_query,
            source_category=source_category,
            source_strategy_hash=source_strategy_hash,
            posted_date=posted_date,
        )
        if already_known and not is_new:
            # Existing identity — skip Enrich/Match by never resetting pipeline flags.
            already_in_db += 1
            if job_id is not None and job_key not in touched_job_keys:
                touched_once += 1
                touched_job_keys.add(job_key)
            continue

        if is_new:
            inserted += 1
            known_db_keys.add(job_key)
            touched_job_keys.add(job_key)
        elif job_id is not None:
            # Collision via UNIQUE constraint (URL/hash) — treat as already in DB.
            already_in_db += 1
            if job_key not in touched_job_keys:
                touched_once += 1
                touched_job_keys.add(job_key)
            known_db_keys.add(job_key)

    return (
        raw_found,
        unique_processed,
        duplicates_skipped,
        already_in_db,
        excluded,
        inserted,
        touched_once,
    )


def build_collection_plan(
    profile: dict,
) -> tuple[list[dict], str | None, str]:
    """Resolve the search plan, preferring AI strategy over roles over profile.

    Returns (plan, strategy_hash, source_label) where each plan entry has
    category, priority, primary_role, queries and exclude_keywords.
    """
    strategy = load_matching_strategy()
    if strategy:
        plan = get_collection_query_plan(strategy)
        if plan:
            strategy_hash = compute_candidate_strategy_hash(profile, strategy)
            return plan, strategy_hash, f"AI matching strategy ({strategy.get('source', 'unknown')})"

    ai_roles = load_ai_roles()
    if ai_roles and ai_roles.get("best_fit_roles"):
        roles = get_collection_roles(ai_roles, profile)
        plan = collection_plan_from_roles(roles)
        if plan:
            return plan, None, "ai_roles.json (no collection_queries)"

    roles = list(profile.get("target_roles", []))
    plan = collection_plan_from_roles(roles)
    return plan, None, "profile.json target_roles (last-resort fallback)"


def _unwrap_collection_result(result: Any) -> tuple[list[dict], CollectionOutcome | None]:
    if isinstance(result, CollectionOutcome):
        return result.jobs, result
    jobs = result or []
    return jobs, CollectionOutcome(jobs=jobs, status="ok" if jobs else "empty")


def _job_collectors() -> dict[str, Any]:
    """Register every active board collector for the orchestration loop."""
    return {
        "drushim": collect_drushim_jobs,
        "linkedin": collect_linkedin_jobs,
        "gotfriends": collect_gotfriends_jobs,
        "alljobs": collect_alljobs_jobs,
        "indeed": collect_indeed_jobs,
        "secret_tel_aviv": collect_secret_tel_aviv_jobs,
        "geektime": collect_geektime_jobs,
    }


def _parse_sites_arg(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",")]
    return [part for part in parts if part]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect jobs from Drushim, LinkedIn, GotFriends, AllJobs, "
            "Indeed Israel, Secret Tel Aviv, and Geektime using AI-generated search queries"
        )
    )
    parser.add_argument(
        "--max-categories", type=int, default=COLLECT_MAX_CATEGORIES,
        help="Limit how many categories to search",
    )
    parser.add_argument(
        "--max-queries", type=int, default=DEFAULT_MAX_QUERIES_PER_CATEGORY,
        help=f"Max query variations per category (default: {DEFAULT_MAX_QUERIES_PER_CATEGORY})",
    )
    parser.add_argument(
        "--sites",
        type=str,
        default=None,
        help=(
            "Comma-separated job boards to search "
            "(drushim, linkedin, gotfriends, alljobs, indeed, secret_tel_aviv, geektime)"
        ),
    )
    args = parser.parse_args()

    print("AI Job Agent — job collection")

    init_db()
    profile = load_profile()
    plan, strategy_hash, source_label = build_collection_plan(profile)

    if not plan:
        print("No search queries found. Run: python src/analyze_roles.py")
        print("Or add target_roles to profile.json")
        return

    if args.max_categories is not None:
        plan = plan[: args.max_categories]

    known_db_keys = get_known_job_identity_keys()
    if known_db_keys:
        print(f"Jobs already in database: {len(known_db_keys)} (will skip re-processing)")

    try:
        selected_sites = normalize_job_board_ids(_parse_sites_arg(args.sites))
    except ValueError as exc:
        print(f"Invalid --sites value: {exc}")
        sys.exit(1)

    print(f"Job boards: {job_boards_label(selected_sites)}")
    print(f"Search source: {source_label}")
    if strategy_hash:
        print(f"Strategy hash: {strategy_hash[:12]}...")
    print(f"Categories to search: {len(plan)}")
    print(
        f"Collection limits: max {args.max_queries} queries/category, "
        f"{args.max_categories if args.max_categories is not None else 'all'} categories; "
        f"board pages — Drushim ≤{DRUSHIM_MAX_PAGES}, LinkedIn ≤{LINKEDIN_MAX_PAGES}, "
        f"GotFriends ≤{GOTFRIENDS_MAX_PAGES}, AllJobs ≤{ALLJOBS_MAX_PAGES}, "
        f"Indeed ≤{INDEED_MAX_PAGES}, SecretTelAviv ≤{SECRET_TEL_AVIV_MAX_PAGES}, "
        f"Geektime ≤{GEEKTIME_MAX_PAGES}"
    )

    seen_job_keys: set[str] = set()
    touched_job_keys: set[str] = set()
    # Per-board query dedupe: the same English string may run on LinkedIn and Drushim.
    searched_queries: set[tuple[str, str]] = set()
    total_raw_found = 0
    total_unique = 0
    total_duplicates = 0
    total_already_in_db = 0
    total_inserted = 0
    total_touched = 0
    total_excluded = 0
    total_queries = 0
    site_totals: dict[str, _SiteTotals] = {}
    site_outcomes: dict[str, list[dict[str, Any]]] = {}

    drushim_session: DrushimBrowserSession | None = None
    if "drushim" in selected_sites and _drushim_uses_browser():
        print("Starting shared Drushim browser session (one browser for all queries)...")
        drushim_session = DrushimBrowserSession(headless=HEADLESS)
        drushim_session.__enter__()
    elif "drushim" in selected_sites:
        print("Drushim: using HTTP mode (no browser — saves server memory)")

    try:
        searches = collection_searches(selected_sites, _job_collectors())
        for entry in plan:
            category = entry.get("category", "")
            exclude_keywords = entry.get("exclude_keywords", [])
            print(f"\n{'=' * 60}")
            print(f"Category: {category} (priority {entry.get('priority', 0)})")
            print(f"Primary role: {entry.get('primary_role') or '(none)'}")
            if entry.get("queries_en"):
                print(f"English titles: {', '.join(entry.get('queries_en') or [])}")
            if entry.get("queries_he") or entry.get("queries_mixed"):
                print(
                    "Hebrew/mixed titles: "
                    + ", ".join(
                        list(entry.get("queries_he") or [])
                        + list(entry.get("queries_mixed") or [])
                    )
                )

            for site_name, collect_fn in searches:
                queries = queries_for_board(
                    entry, site_name, max_items=args.max_queries
                )
                print(f"\n{'-' * 50}")
                print(
                    f"[{site_name}] Queries ({len(queries)}): "
                    + (", ".join(queries) if queries else "(none — skipped)")
                )
                if not queries:
                    message = (
                        "אין שאילתות באנגלית לחיפוש — "
                        "לוחות באנגלית דורשים מונחי חיפוש באנגלית"
                        if site_name in _ENGLISH_QUERY_SITES
                        else "אין שאילתות חיפוש לקטגוריה זו"
                    )
                    emit_agent_warning(f"{_site_label(site_name)}: {message}")
                    _note_site_issue(site_totals, site_name, message)
                    continue

                site = site_totals.setdefault(site_name, _SiteTotals())
                for query in queries:
                    query_key = (site_name, query.strip().lower())
                    if not query_key[1] or query_key in searched_queries:
                        continue
                    searched_queries.add(query_key)
                    total_queries += 1
                    site.queries += 1

                    try:
                        if site_name == "drushim" and drushim_session is not None:
                            raw_result = drushim_session.collect(query)
                        else:
                            raw_result = collect_fn(query)
                        jobs, outcome = _unwrap_collection_result(raw_result)
                    except KeyboardInterrupt:
                        print("\nInterrupted by user — stopping collection.")
                        raise
                    except Exception as error:
                        message = f"חיפוש '{query}' נכשל: {error}"
                        print(f"  [{site_name}] Search failed for '{query}': {error}")
                        emit_agent_warning(f"{_site_label(site_name)}: {message}")
                        _note_site_issue(site_totals, site_name, message)
                        site_outcomes.setdefault(site_name, []).append(
                            {"query": query, "status": "failed", "reason": str(error)}
                        )
                        continue

                    if outcome and outcome.reason_he and outcome.status != "ok":
                        _note_site_issue(site_totals, site_name, outcome.reason_he)
                        site_outcomes.setdefault(site_name, []).append(
                            {"query": query, **outcome_to_dict(outcome)}
                        )
                    elif not jobs:
                        empty_message = (
                            outcome.reason_he
                            if outcome and outcome.reason_he
                            else f"לא נמצאו משרות לחיפוש '{query}'"
                        )
                        _note_site_issue(site_totals, site_name, empty_message)
                        site_outcomes.setdefault(site_name, []).append(
                            {
                                "query": query,
                                "status": outcome.status if outcome else "empty",
                                "reason_he": empty_message,
                            }
                        )

                    raw, unique, duplicates, already_in_db, excluded, inserted, touched = save_jobs_to_db(
                        jobs,
                        source_query=query,
                        source_category=category,
                        source_strategy_hash=strategy_hash,
                        exclude_keywords=exclude_keywords,
                        seen_job_keys=seen_job_keys,
                        known_db_keys=known_db_keys,
                        touched_job_keys=touched_job_keys,
                    )
                    total_raw_found += raw
                    total_unique += unique
                    total_duplicates += duplicates
                    total_already_in_db += already_in_db
                    total_inserted += inserted
                    total_touched += touched
                    total_excluded += excluded
                    site.raw += raw
                    site.new += inserted
                    site.already_in_db += already_in_db
                    site.excluded += excluded
                    if raw > 0:
                        site.queries_with_raw += 1

                    print(
                        f"  [{site_name}] '{query}': raw {raw}, unique {unique}, duplicates {duplicates}, "
                        f"already in db {already_in_db}, new {inserted}, touched {touched}, "
                        f"excluded {excluded}"
                    )
                    if raw == 0:
                        print(f"  [{site_name}] WARNING: no jobs found for query '{query}'")
                    elif inserted == 0 and already_in_db > 0:
                        print(
                            f"  [{site_name}] NOTE: {raw} jobs found but all already exist in database"
                        )
    finally:
        if drushim_session is not None:
            drushim_session.__exit__(None, None, None)

    print(f"\n{'=' * 60}")
    print("Overall:")
    print(f"  Categories searched: {len(plan)}")
    print(f"  Query variations searched: {total_queries}")
    print(f"  Raw jobs found: {total_raw_found}")
    print(f"  Unique jobs this run: {total_unique}")
    print(f"  Duplicates skipped this run: {total_duplicates}")
    print(f"  Already in database (skipped): {total_already_in_db}")
    print(f"  New jobs inserted: {total_inserted}")
    print(f"  Existing jobs touched once: {total_touched}")
    print(f"  Excluded by keyword: {total_excluded}")

    warnings = _finalize_site_warnings(site_totals)
    for warning in warnings:
        emit_agent_warning(warning)

    collection_summary = {
        site_name: {
            "raw": site.raw,
            "new": site.new,
            "already_in_db": site.already_in_db,
            "excluded": site.excluded,
            "queries": site.queries,
            "queries_with_raw": site.queries_with_raw,
            "issues": site.issues,
            "outcomes": site_outcomes.get(site_name, []),
        }
        for site_name, site in site_totals.items()
    }
    collection_summary["warnings"] = warnings
    emit_collect_summary(collection_summary)


if __name__ == "__main__":
    main()
