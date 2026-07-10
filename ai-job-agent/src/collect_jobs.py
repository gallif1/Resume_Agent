import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

from config import (
    DRUSHIM_BASE_URL,
    DRUSHIM_MAX_SCROLL_ROUNDS,
    GOTFRIENDS_ENABLED,
    HEADLESS,
    LINKEDIN_BASE_URL,
    LINKEDIN_ENABLED,
    LINKEDIN_LOCATION,
    LINKEDIN_MAX_PAGES,
    LOGS_DIR,
)
from enrich_jobs import BROWSER_USER_AGENT, create_browser_context
from db import get_known_job_identity_keys, init_db, upsert_collected_job
from gotfriends_collector import collect_gotfriends_jobs
from job_identity import (
    compute_candidate_strategy_hash,
    compute_job_identity_key,
    extract_drushim_job_id,
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

DEFAULT_MAX_QUERIES_PER_CATEGORY = 6

DRUSHIM_HTTP_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
}

DRUSHIM_JOB_URL_RE = re.compile(r"/job/(\d+)/[a-f0-9]+/?", re.IGNORECASE)

EXTRACT_JOBS_JS = """
() => {
    const items = [...document.querySelectorAll(".job-item")];
    const jobs = [];

    for (const item of items) {
        const title =
            item.querySelector(
                "h3 .job-url, h3 span, .job-url, [class*='job-title'] span"
            )?.innerText?.trim() || "";
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


def build_drushim_search_urls(query: str) -> list[str]:
    """Return Drushim search URL variants to try (querystring + path style)."""
    trimmed = query.strip()
    if not trimmed:
        return []

    encoded = quote(trimmed)
    slug = quote(trimmed.replace(" ", "-"))
    urls = [
        f"{DRUSHIM_BASE_URL}/jobs/search/?searchterm={encoded}",
        f"{DRUSHIM_BASE_URL}/jobs/search/{encoded}/",
        f"{DRUSHIM_BASE_URL}/jobs/search/{slug}/",
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def _drushim_http_session() -> requests.Session:
    """HTTP session warmed against the Drushim homepage (helps some bot filters)."""
    session = requests.Session()
    session.headers.update(DRUSHIM_HTTP_HEADERS)
    try:
        session.get(DRUSHIM_BASE_URL, timeout=20)
    except requests.RequestException:
        pass
    return session


def _interactive_terminal() -> bool:
    """True when the process can prompt the user on stdin."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _parse_drushim_job_item(item: Any, *, page_url: str) -> dict | None:
    """Parse one Drushim search-result card."""
    title_el = item.select_one(
        "h3 .job-url, h3 span, .job-url, [class*='job-title'] span"
    )
    title = title_el.get_text(strip=True) if title_el else ""
    company_el = item.select_one(".job-details-top a span, .job-details-top span")
    company = company_el.get_text(strip=True) if company_el else ""
    location_el = item.select_one(".job-details-sub .display-18 span, .job-details-sub span")
    location = (location_el.get_text(strip=True) if location_el else "").rstrip(" |")
    description_el = item.select_one(".job-intro p, .vacancyMain p, .job-intro")
    description = description_el.get_text(strip=True) if description_el else ""
    link = item.select_one('a[href*="/job/"]')
    href = urljoin(page_url, link.get("href", "")) if link else ""

    if not title or not href:
        return None

    return {
        "title": title,
        "company": company,
        "location": location,
        "job_url": href,
        "source": "drushim",
        "description": description or "",
    }


def _parse_drushim_jobs_from_links(soup: BeautifulSoup, *, page_url: str) -> list[dict]:
    """Fallback parser: collect job links when card markup is incomplete."""
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    for link in soup.select('a[href*="/job/"]'):
        href = urljoin(page_url, (link.get("href") or "").strip())
        job_id = extract_drushim_job_id(href)
        if not job_id or job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        title = link.get_text(strip=True) or (link.get("title") or "").strip()
        if len(title) < 3:
            continue

        jobs.append({
            "title": title,
            "company": "",
            "location": "",
            "job_url": normalize_job_url(href) or href,
            "source": "drushim",
            "description": "",
        })

    return jobs


def _dedupe_drushim_jobs(jobs: list[dict]) -> list[dict]:
    """Deduplicate Drushim jobs by canonical job id."""
    unique: list[dict] = []
    seen_ids: set[str] = set()
    for job in jobs:
        job_id = extract_drushim_job_id(job.get("job_url", ""))
        if not job_id or job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        unique.append(job)
    return unique


def parse_drushim_search_html(html: str, *, page_url: str) -> list[dict]:
    """Parse Drushim search-result cards from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []

    for item in soup.select(".job-item"):
        parsed = _parse_drushim_job_item(item, page_url=page_url)
        if parsed is not None:
            jobs.append(parsed)

    if not jobs:
        jobs = _parse_drushim_jobs_from_links(soup, page_url=page_url)

    return _dedupe_drushim_jobs(jobs)


def _log_drushim_http_diagnostics(html: str, *, page_url: str, status_code: int) -> None:
    """Print parse diagnostics when Drushim HTTP returns no jobs."""
    soup = BeautifulSoup(html, "html.parser")
    card_count = len(soup.select(".job-item"))
    link_count = len(DRUSHIM_JOB_URL_RE.findall(html))
    print(
        f"  Drushim HTTP diagnostics ({page_url}): "
        f"status={status_code}, html={len(html)} bytes, "
        f".job-item={card_count}, /job/ links={link_count}"
    )


def collect_drushim_jobs_http(query: str) -> list[dict]:
    """Fetch Drushim search results over plain HTTP (no browser)."""
    session = _drushim_http_session()
    all_jobs: list[dict] = []

    for search_url in build_drushim_search_urls(query):
        try:
            response = session.get(search_url, timeout=30)
        except requests.RequestException as error:
            print(f"  Drushim HTTP request failed ({search_url}): {error}")
            continue

        if response.status_code >= 400:
            print(f"  Drushim HTTP returned status {response.status_code} ({search_url})")
            continue

        jobs = parse_drushim_search_html(response.text, page_url=response.url)
        if jobs:
            all_jobs.extend(jobs)
            break

        _log_drushim_http_diagnostics(
            response.text,
            page_url=response.url,
            status_code=response.status_code,
        )

    all_jobs = _dedupe_drushim_jobs(all_jobs)
    if all_jobs:
        print(f"  Drushim HTTP returned {len(all_jobs)} job card(s)")
    return all_jobs


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


def page_looks_blocked(page: Page) -> bool:
    """Detect captcha / anti-bot pages without false positives on search results."""
    if page.locator(".job-item").count() > 0:
        return False

    title = (page.title() or "").lower()
    try:
        visible = (page.evaluate("() => document.body.innerText || ''") or "")[:2000].lower()
    except Exception:
        visible = ""
    combined = f"{title}\n{visible}"

    blocked_signals = [
        "verify you are human",
        "אימות אנושי",
        "access denied",
        "request blocked",
        "unusual traffic",
        "checking your browser",
        "cf-browser-verification",
        "just a moment",
    ]
    if any(signal in combined for signal in blocked_signals):
        return True

    try:
        html = page.content().lower()
    except Exception:
        return False

    challenge_markers = (
        "cf-challenge",
        "challenge-platform",
        "cf-turnstile",
        "g-recaptcha",
    )
    return any(marker in html for marker in challenge_markers)


def _scroll_drushim_results(page: Page, *, max_rounds: int = DRUSHIM_MAX_SCROLL_ROUNDS) -> None:
    """Scroll the search page to load additional Drushim job cards."""
    previous_count = 0
    for _ in range(max_rounds):
        current_count = page.locator(".job-item").count()
        if current_count <= previous_count:
            break
        previous_count = current_count
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)


def extract_jobs_from_page(page: Page) -> list[dict]:
    """Extract job cards from the current Drushim search page."""
    jobs = page.evaluate(EXTRACT_JOBS_JS)
    if jobs:
        return jobs
    return parse_drushim_search_html(page.content(), page_url=page.url)


def collect_drushim_jobs_playwright(query: str, *, headless: bool) -> list[dict]:
    """Open Drushim in Playwright and extract job cards."""
    search_urls = build_drushim_search_urls(query)
    search_url = search_urls[0] if search_urls else build_drushim_search_url(query)
    print(f"  Drushim browser mode: {'headless' if headless else 'visible'}")

    with sync_playwright() as playwright:
        context, page = create_browser_context(playwright, headless=headless)
        browser = context.browser

        try:
            try:
                page.goto(DRUSHIM_BASE_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1000)
            except Exception:
                pass

            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            try:
                page.wait_for_selector(".job-item, a[href*='/job/']", timeout=15000)
            except Exception:
                if page_looks_blocked(page):
                    reason = "Page may be blocked by captcha or anti-bot protection"
                else:
                    reason = "No job cards found on the page"
                save_debug_artifacts(page, reason)

                if headless and _interactive_terminal():
                    print("Headless extraction failed. Retrying with a visible browser...")
                    return collect_drushim_jobs_playwright(query, headless=False)

                if _interactive_terminal():
                    print("Inspect the browser window, then press Enter to retry extraction.")
                    input()
                    jobs = extract_jobs_from_page(page)
                    if not jobs:
                        save_debug_artifacts(page, "Extraction still failed after manual inspection")
                    return _dedupe_drushim_jobs(jobs)

                return []

            _scroll_drushim_results(page)
            jobs = _dedupe_drushim_jobs(extract_jobs_from_page(page))
            if not jobs:
                reason = "Job cards were present but fields could not be parsed"
                save_debug_artifacts(page, reason)

                if headless and _interactive_terminal():
                    print("Could not parse jobs in headless mode. Retrying visibly...")
                    return collect_drushim_jobs_playwright(query, headless=False)

                if _interactive_terminal():
                    print("Inspect the browser window, then press Enter to retry extraction.")
                    input()
                    jobs = _dedupe_drushim_jobs(extract_jobs_from_page(page))
                    if not jobs:
                        save_debug_artifacts(page, "Extraction still failed after manual inspection")

            return jobs
        finally:
            context.close()
            if browser is not None:
                browser.close()


def collect_drushim_jobs(query: str, headless: bool = HEADLESS) -> list[dict]:
    """Collect Drushim job cards — HTTP first, Playwright as fallback."""
    search_url = build_drushim_search_url(query)
    print(f"Searching Drushim for: {query}")
    print(f"URL: {search_url}")

    jobs = collect_drushim_jobs_http(query)
    if jobs:
        return jobs

    print("  Drushim HTTP returned no jobs — trying browser...")
    jobs = collect_drushim_jobs_playwright(query, headless=headless)
    if jobs:
        print(f"  Drushim browser returned {len(jobs)} job card(s)")
        return jobs

    print("  Drushim returned no jobs (HTTP and browser both failed)")
    return []


LINKEDIN_JOBS_PER_PAGE = 25

LINKEDIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,he-IL;q=0.8,he;q=0.7",
}


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


def _enabled_job_boards_label() -> str:
    sites = ["drushim"]
    if LINKEDIN_ENABLED:
        sites.append("linkedin")
    if GOTFRIENDS_ENABLED:
        sites.append("gotfriends")
    return " + ".join(sites)


def _collection_searches() -> list[tuple[str, Any]]:
    searches: list[tuple[str, Any]] = [("drushim", collect_drushim_jobs)]
    if LINKEDIN_ENABLED:
        searches.append(("linkedin", collect_linkedin_jobs))
    if GOTFRIENDS_ENABLED:
        searches.append(("gotfriends", collect_gotfriends_jobs))
    return searches


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect jobs from Drushim, LinkedIn, and GotFriends using AI-generated search queries"
    )
    parser.add_argument(
        "--max-categories", type=int, default=None,
        help="Limit how many categories to search",
    )
    parser.add_argument(
        "--max-queries", type=int, default=DEFAULT_MAX_QUERIES_PER_CATEGORY,
        help=f"Max query variations per category (default: {DEFAULT_MAX_QUERIES_PER_CATEGORY})",
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

    enabled_sites = _enabled_job_boards_label()
    print(f"Job boards: {enabled_sites}")
    print(f"Search source: {source_label}")
    if strategy_hash:
        print(f"Strategy hash: {strategy_hash[:12]}...")
    print(f"Categories to search: {len(plan)}")

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
    source_totals: dict[str, int] = {"drushim": 0, "linkedin": 0, "gotfriends": 0}

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

            searches = _collection_searches()

            for site_name, collect_fn in searches:
                try:
                    jobs = collect_fn(query)
                except KeyboardInterrupt:
                    print("\nInterrupted by user — stopping collection.")
                    raise
                except Exception as error:
                    print(f"  [{site_name}] Search failed for '{query}': {error}")
                    continue

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
                source_totals[site_name] = source_totals.get(site_name, 0) + inserted

                print(
                    f"  [{site_name}] '{query}': raw {raw}, unique {unique}, duplicates {duplicates}, "
                    f"already in db {already_in_db}, new {inserted}, touched {touched}, "
                    f"excluded {excluded}"
                )

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
    print("  New jobs by source:")
    for site_name, count in sorted(source_totals.items()):
        if count:
            print(f"    {site_name}: {count}")


if __name__ == "__main__":
    main()
