import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

from browser_utils import browser_http_headers, create_browser_context, page_looks_blocked
from config import (
    DRUSHIM_BASE_URL,
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

DEFAULT_MAX_QUERIES_PER_CATEGORY = 6

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


def collect_drushim_jobs(query: str, headless: bool = HEADLESS) -> list[dict]:
    """Open Drushim in Playwright and extract job cards."""
    search_url = build_drushim_search_url(query)
    print(f"Searching Drushim for: {query}")
    print(f"URL: {search_url}")
    print(f"Browser mode: {'headless' if headless else 'visible'}")

    with sync_playwright() as playwright:
        context, page = create_browser_context(playwright, headless=headless)

        try:
            response = page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            if response is not None and response.status >= 400:
                reason = f"Drushim returned HTTP {response.status}"
                save_debug_artifacts(page, reason)
                if headless:
                    print(f"{reason}. Retrying with a visible browser...")
                    context.close()
                    return collect_drushim_jobs(query, headless=False)

            try:
                page.wait_for_selector(".job-item", timeout=15000)
            except Exception:
                if page_looks_blocked_drushim(page):
                    reason = "Page may be blocked by captcha or anti-bot protection"
                else:
                    reason = "No job cards found on the page"
                save_debug_artifacts(page, reason)

                if headless:
                    print("Headless extraction failed. Retrying with a visible browser...")
                    context.close()
                    return collect_drushim_jobs(query, headless=False)

                print("Inspect the browser window, then press Enter to retry extraction.")
                input()
                jobs = extract_jobs_from_page(page)
                if not jobs:
                    save_debug_artifacts(page, "Extraction still failed after manual inspection")
                return jobs

            jobs = extract_jobs_from_page(page)
            if not jobs:
                reason = "Job cards were present but fields could not be parsed"
                save_debug_artifacts(page, reason)

                if headless:
                    print("Could not parse jobs in headless mode. Retrying visibly...")
                    context.close()
                    return collect_drushim_jobs(query, headless=False)

                print("Inspect the browser window, then press Enter to retry extraction.")
                input()
                jobs = extract_jobs_from_page(page)
                if not jobs:
                    save_debug_artifacts(page, "Extraction still failed after manual inspection")

            return jobs
        finally:
            context.close()


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
        "--max-categories", type=int, default=None,
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


if __name__ == "__main__":
    main()
