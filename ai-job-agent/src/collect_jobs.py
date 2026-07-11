from __future__ import annotations

import argparse
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
    COLLECT_MAX_CATEGORIES,
    COLLECT_MAX_QUERIES,
    DRUSHIM_BASE_URL,
    DRUSHIM_BROWSER_FALLBACK,
    DRUSHIM_GOTO_TIMEOUT_MS,
    DRUSHIM_HTTP_FIRST,
    DRUSHIM_HTTP_TIMEOUT_SEC,
    DRUSHIM_PAGE_WAIT_MS,
    DRUSHIM_SELECTOR_TIMEOUT_MS,
    HEADLESS,
    LINKEDIN_BASE_URL,
    LINKEDIN_LOCATION,
    LINKEDIN_MAX_PAGES,
    LOGS_DIR,
)
from db import get_known_job_identity_keys, init_db, upsert_collected_job
from gotfriends_collector import collect_gotfriends_jobs
from job_boards import collection_searches, job_boards_label, normalize_job_board_ids
from job_identity import (
    compute_candidate_strategy_hash,
    compute_job_identity_key,
    extract_linkedin_job_id,
    normalize_job_url,
)
from profile_utils import load_profile
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
}


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
        });
    }

    return jobs;
}
"""


def build_drushim_search_url(query: str) -> str:
    """Build a Drushim search URL for the given query."""
    return f"{DRUSHIM_BASE_URL}/jobs/search/?searchterm={quote(query)}"


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

        jobs.append({
            "title": title,
            "company": company_el.get_text(strip=True) if company_el else "",
            "location": location,
            "job_url": job_url,
            "source": "drushim",
            "description": description_el.get_text(strip=True) if description_el else "",
        })

    return jobs


def collect_drushim_jobs_http(query: str) -> CollectionOutcome:
    """Fetch Drushim search results with plain HTTP (no browser)."""
    search_url = build_drushim_search_url(query)
    print(f"Searching Drushim (HTTP) for: {query}")
    print(f"URL: {search_url}")

    try:
        response = requests.get(
            search_url,
            headers=browser_http_headers(),
            timeout=DRUSHIM_HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as error:
        reason = f"Drushim HTTP request failed: {error}"
        return CollectionOutcome(
            status="http_error",
            reason=reason,
            reason_he=f"דרושים: שגיאת רשת — {error}",
        )

    http_status = response.status_code
    if http_status >= 400:
        return CollectionOutcome(
            status="http_error",
            reason=f"Drushim returned HTTP {http_status}",
            reason_he=f"דרושים החזיר שגיאת HTTP {http_status}",
            http_status=http_status,
        )

    if is_cloudflare_blocked_html(response.text):
        return CollectionOutcome(
            status="blocked",
            reason="Cloudflare block page",
            reason_he="דרושים חסם את הגישה (Cloudflare)",
            http_status=http_status,
        )

    jobs = parse_drushim_search_html(response.text)
    if jobs:
        print(f"  Drushim HTTP extracted {len(jobs)} job card(s) for '{query}'")
        return CollectionOutcome(jobs=jobs, status="ok")

    return CollectionOutcome(
        status="empty",
        reason="No job cards found in HTTP response",
        reason_he=f"דרושים: לא נמצאו משרות לחיפוש '{query}'",
        http_status=http_status,
    )


def _drushim_uses_browser() -> bool:
    """True when Drushim collection may launch Playwright."""
    if DRUSHIM_HTTP_FIRST and not DRUSHIM_BROWSER_FALLBACK:
        return False
    return True


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
        return _collect_drushim_with_page(
            self.page, query, headless=self.headless, allow_visible_retry=False
        )


def collect_drushim_jobs(
    query: str,
    headless: bool = HEADLESS,
    *,
    page: Page | None = None,
) -> CollectionOutcome:
    """Collect Drushim jobs — HTTP first on server, browser fallback on desktop."""
    if page is None and DRUSHIM_HTTP_FIRST:
        http_outcome = collect_drushim_jobs_http(query)
        if http_outcome.status == "ok":
            return http_outcome
        if not DRUSHIM_BROWSER_FALLBACK:
            if http_outcome.reason_he:
                emit_agent_warning(http_outcome.reason_he)
            return http_outcome
        print(f"  Drushim HTTP {http_outcome.status} — falling back to browser...")

    if page is not None:
        return _collect_drushim_with_page(page, query, headless=headless)

    with DrushimBrowserSession(headless=headless) as session:
        assert session.page is not None
        return _collect_drushim_with_page(
            session.page,
            query,
            headless=headless,
            allow_visible_retry=_interactive_retry_enabled(),
        )


LINKEDIN_JOBS_PER_PAGE = 25

LINKEDIN_HEADERS = browser_http_headers()


def build_linkedin_search_url(query: str, start: int = 0) -> str:
    """Build a LinkedIn guest jobs-search API URL (no login required)."""
    params = {
        "keywords": query,
        "location": LINKEDIN_LOCATION,
        "start": start,
    }
    return f"{LINKEDIN_BASE_URL}/jobs-guest/jobs/api/seeMoreJobPostings/search?{urlencode(params)}"


def _parse_linkedin_cards(html: str) -> list[dict]:
    """Parse job cards from a LinkedIn guest search response."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    cards = soup.select("li") or soup.select("div.base-card")
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

        title_el = card.select_one("h3.base-search-card__title, h3")
        company_el = card.select_one("h4.base-search-card__subtitle a, h4 a, h4")
        location_el = card.select_one("span.job-search-card__location")

        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        jobs.append({
            "title": title,
            "company": company_el.get_text(strip=True) if company_el else "",
            "location": location_el.get_text(strip=True) if location_el else "",
            "job_url": href,
            "source": "linkedin",
            "description": "",
        })

    return jobs


def collect_linkedin_jobs(query: str, max_pages: int = LINKEDIN_MAX_PAGES) -> list[dict]:
    """Fetch job cards from LinkedIn's public guest search API."""
    print(f"Searching LinkedIn for: {query} (location: {LINKEDIN_LOCATION})")
    all_jobs: list[dict] = []

    for page_index in range(max_pages):
        start = page_index * LINKEDIN_JOBS_PER_PAGE
        url = build_linkedin_search_url(query, start=start)

        try:
            response = requests.get(url, headers=LINKEDIN_HEADERS, timeout=30)
        except requests.RequestException as error:
            print(f"  LinkedIn request failed (page {page_index + 1}): {error}")
            break

        if response.status_code == 429:
            print("  LinkedIn rate limit hit (429) — stopping this query.")
            break
        if response.status_code >= 400:
            print(f"  LinkedIn returned HTTP {response.status_code} — stopping this query.")
            break

        page_jobs = _parse_linkedin_cards(response.text)
        if not page_jobs:
            break

        all_jobs.extend(page_jobs)
        if len(page_jobs) < LINKEDIN_JOBS_PER_PAGE:
            break

        # Be polite between pages to avoid rate limiting.
        time.sleep(1.5)

    print(f"  LinkedIn returned {len(all_jobs)} job card(s)")
    return all_jobs


def _title_excluded(title: str, exclude_keywords: list[str]) -> bool:
    """True when a job title matches one of the category's exclude keywords."""
    title_l = (title or "").lower()
    return any(kw.lower() in title_l for kw in exclude_keywords if kw)


def save_jobs_to_db(
    jobs: list[dict],
    *,
    source_query: str,
    source_category: str,
    source_strategy_hash: str | None,
    exclude_keywords: list[str],
    seen_job_keys: set[str],
    known_db_keys: set[str],
    touched_job_keys: set[str],
) -> tuple[int, int, int, int, int, int, int]:
    """Upsert jobs into SQLite with strict run-level deduplication.

    Returns:
        (raw_found, unique_processed, duplicates_skipped, already_in_db,
         excluded, inserted, touched_once)
    """
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

        if job_key in known_db_keys:
            already_in_db += 1
            seen_job_keys.add(job_key)
            continue

        if _title_excluded(title, exclude_keywords):
            excluded += 1
            seen_job_keys.add(job_key)
            continue

        seen_job_keys.add(job_key)
        unique_processed += 1

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
        )
        if is_new:
            inserted += 1
            known_db_keys.add(job_key)
            touched_job_keys.add(job_key)
        elif job_id is not None and job_key not in touched_job_keys:
            touched_once += 1
            touched_job_keys.add(job_key)

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
    return {
        "drushim": collect_drushim_jobs,
        "linkedin": collect_linkedin_jobs,
        "gotfriends": collect_gotfriends_jobs,
    }


def _parse_sites_arg(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",")]
    return [part for part in parts if part]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect jobs from Drushim, LinkedIn, and GotFriends using AI-generated search queries"
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
        help="Comma-separated job boards to search (drushim, linkedin, gotfriends)",
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
    if COLLECT_MAX_CATEGORIES is not None or DEFAULT_MAX_QUERIES_PER_CATEGORY != 6:
        print(
            f"Collection limits: max {DEFAULT_MAX_QUERIES_PER_CATEGORY} queries/category, "
            f"{COLLECT_MAX_CATEGORIES or 'all'} categories"
        )

    seen_job_keys: set[str] = set()
    touched_job_keys: set[str] = set()
    searched_queries: set[str] = set()
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
        for entry in plan:
            category = entry.get("category", "")
            queries = entry.get("queries", [])[: args.max_queries]
            exclude_keywords = entry.get("exclude_keywords", [])
            print(f"\n{'=' * 60}")
            print(f"Category: {category} (priority {entry.get('priority', 0)})")
            print(f"Queries: {', '.join(queries)}")

            for query in queries:
                query_key = query.strip().lower()
                if not query_key or query_key in searched_queries:
                    continue
                searched_queries.add(query_key)
                total_queries += 1

                print(f"\n{'-' * 50}")

                searches = collection_searches(selected_sites, _job_collectors())

                for site_name, collect_fn in searches:
                    site = site_totals.setdefault(site_name, _SiteTotals())
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
