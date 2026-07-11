"""Collect job listings from GotFriends (gotfriends.co.il)."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from browser_utils import (
    browser_http_headers,
    fetch_html_with_playwright,
    is_cloudflare_blocked_html,
    is_http_blocked,
)
from config import (
    GOTFRIENDS_BASE_URL,
    GOTFRIENDS_BROWSER_PROFILE_DIR,
    GOTFRIENDS_MAX_PAGES,
    HEADLESS,
)
from job_identity import normalize_job_url

# Lobby categories on GotFriends (software is the default dev search).
GOTFRIENDS_CATEGORIES = (
    "software",
    "ai",
    "algorithm",
    "bibig_data",
    "datasecurity",
    "qa",
    "system",
    "hardware",
    "projects",
    "executive-position",
    "graduates",
)

# Map search keywords to (category, profession slug) pairs.
_KEYWORD_SLUG_HINTS: list[tuple[list[str], str, str]] = [
    (["python", "פייתון"], "software", "python-developer"),
    (["backend", "back-end", "צד שרת", "בקאנד"], "software", "backend-developer"),
    (["frontend", "front-end", "פרונט", "פרונטאנד"], "software", "frontend-developer"),
    (["full stack", "fullstack", "פול סטאק", "פולסטאק"], "software", "full-stack-developer"),
    (["react", "ריאקט"], "software", "react-developer"),
    (["angular", "אנגולר"], "software", "angular-developer"),
    (["node", "nodejs", "node.js"], "software", "nodejs-developer"),
    (["java", "ג'אווה", "גאווה"], "software", "java-developer"),
    (["kotlin", "קוטלין"], "software", "kotlin-developer"),
    (["android", "אנדרואיד"], "software", "android-developer"),
    (["ios", "swift"], "software", "ios-developer"),
    (["mobile", "מובייל"], "software", "mobile-programmer"),
    (["c++", "cplusplus"], "software", "cplusplus-programmer"),
    (["c#", ".net", "dotnet", "net developer"], "software", "net-developer"),
    (["golang", " go ", "go developer"], "software", "go-developer"),
    (["ruby", "רובי"], "software", "ruby-programmer"),
    (["php"], "software", "php-developer"),
    (["scala"], "software", "scala-developer"),
    (["data engineer", "data-engineer"], "software", "data-engineer"),
    (["big data", "big-data"], "bibig_data", "big-data-developer"),
    (["ai engineer", "ai-engineer", "machine learning", "llm"], "ai", "ai-engineer"),
    (["devops", "sre", "תשתיות", "platform"], "system", "system"),
    (["qa", "automation", "בדיקות", "אוטומציה", "tester"], "qa", "qa"),
    (["cyber", "security", "סייבר", "אבטחה"], "datasecurity", "datasecurity"),
    (["junior", "graduate", "בוגר", "ג'וניור", "גוניור"], "software", "graduate-with-high-honors"),
    (["software", "מפתח", "מהנדס תוכנה", "developer", "engineer"], "software", "backend-developer"),
]

_QUERY_STOP_WORDS = frozenset({
    "and", "or", "the", "for", "with", "job", "role", "position",
    "משרת", "דרוש", "דרושים", "מפתח", "מהנדס", "junior", "senior",
})

_profession_slugs_cache: dict[str, list[str]] | None = None
_gotfriends_access_blocked: bool | None = None


def _mark_gotfriends_blocked(blocked: bool) -> None:
    global _gotfriends_access_blocked
    _gotfriends_access_blocked = blocked


def _gotfriends_known_blocked() -> bool:
    return _gotfriends_access_blocked is True


def _fetch_gotfriends_html_http(url: str, *, referer: str | None = None) -> tuple[int, str]:
    headers = browser_http_headers(referer=referer or GOTFRIENDS_BASE_URL)
    try:
        response = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as error:
        print(f"  GotFriends HTTP request failed for {url}: {error}")
        return 0, ""
    return response.status_code, response.text


def fetch_gotfriends_html(
    url: str,
    *,
    headless: bool = HEADLESS,
    referer: str | None = None,
) -> tuple[int, str]:
    """Fetch a GotFriends page, falling back to Playwright when HTTP is blocked."""
    if _gotfriends_known_blocked():
        return 403, ""

    status, html = _fetch_gotfriends_html_http(url, referer=referer)
    if status == 200 and not is_cloudflare_blocked_html(html):
        _mark_gotfriends_blocked(False)
        return status, html

    if status and not is_http_blocked(status, html):
        return status, html

    if status in (403, 429, 503) or is_cloudflare_blocked_html(html):
        print(
            f"  GotFriends HTTP {status or 'error'} / Cloudflare block for {url} — "
            "retrying with browser..."
        )

    GOTFRIENDS_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    browser_status, html = fetch_html_with_playwright(
        url,
        headless=headless,
        user_data_dir=str(GOTFRIENDS_BROWSER_PROFILE_DIR),
    )
    if is_cloudflare_blocked_html(html) and headless:
        print("  GotFriends still blocked in headless mode — retrying with a visible browser...")
        browser_status, html = fetch_html_with_playwright(
            url,
            headless=False,
            user_data_dir=str(GOTFRIENDS_BROWSER_PROFILE_DIR),
        )

    if is_cloudflare_blocked_html(html):
        _mark_gotfriends_blocked(True)
        print(
            "  GotFriends is blocked by Cloudflare. "
            "Open the site once in the agent browser profile, complete the challenge, then retry."
        )
    else:
        _mark_gotfriends_blocked(False)

    return browser_status, html


def _absolute_gotfriends_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("/"):
        return f"{GOTFRIENDS_BASE_URL}{href}"
    if href.startswith("http"):
        return href
    return f"{GOTFRIENDS_BASE_URL}/{href.lstrip('/')}"


def _slugify_query(query: str) -> str:
    slug = query.lower().strip()
    slug = re.sub(r"[^\w\s\-]", " ", slug, flags=re.UNICODE)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug


def _extract_company_from_title(title: str) -> str:
    for pattern in (
        r"בחברת\s+(.+)$",
        r"בחברה\s+(.+)$",
        r"בתוך\s+(.+)$",
        r"\sב([\w\-].+)$",
    ):
        match = re.search(pattern, title)
        if match:
            return match.group(1).strip()
    return ""


def _query_tokens(query: str) -> list[str]:
    tokens = re.split(r"[^\w]+", query.lower(), flags=re.UNICODE)
    return [
        token
        for token in tokens
        if len(token) >= 3 and token not in _QUERY_STOP_WORDS
    ]


def _title_matches_query(title: str, query: str) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return True
    title_l = title.lower()
    return any(token in title_l for token in tokens)


def fetch_profession_slugs() -> dict[str, list[str]]:
    """Fetch profession slugs from each GotFriends lobby category (HTTP only)."""
    global _profession_slugs_cache
    if _profession_slugs_cache is not None:
        return _profession_slugs_cache
    if _gotfriends_known_blocked():
        _profession_slugs_cache = {}
        return _profession_slugs_cache

    slugs_by_category: dict[str, list[str]] = {}
    for category in GOTFRIENDS_CATEGORIES:
        listing_url = f"{GOTFRIENDS_BASE_URL}/jobslobby/{category}/"
        status, html = _fetch_gotfriends_html_http(listing_url)
        if status == 403 or is_cloudflare_blocked_html(html):
            break
        if status >= 400 or not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        slugs: list[str] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            match = re.match(rf".*/jobslobby/{re.escape(category)}/([^/]+)/$", href)
            if not match:
                continue
            slug = match.group(1)
            if slug.isdigit():
                continue
            if slug not in slugs:
                slugs.append(slug)
        if slugs:
            slugs_by_category[category] = slugs

    _profession_slugs_cache = slugs_by_category
    return slugs_by_category


def resolve_gotfriends_listing_urls(query: str) -> list[str]:
    """Map a free-text search query to one or more GotFriends listing URLs."""
    query_l = f" {query.lower()} "
    urls: list[str] = []

    for keywords, category, slug in _KEYWORD_SLUG_HINTS:
        if any(keyword in query_l for keyword in keywords):
            urls.append(f"{GOTFRIENDS_BASE_URL}/jobslobby/{category}/{slug}/")

    slug_candidate = _slugify_query(query)
    for category, slugs in fetch_profession_slugs().items():
        for slug in slugs:
            if slug == slug_candidate or slug_candidate in slug or slug in slug_candidate:
                urls.append(f"{GOTFRIENDS_BASE_URL}/jobslobby/{category}/{slug}/")

    if not urls:
        for category in GOTFRIENDS_CATEGORIES:
            urls.append(f"{GOTFRIENDS_BASE_URL}/jobslobby/{category}/")

    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def build_gotfriends_page_url(listing_url: str, page: int) -> str:
    """Append pagination to a GotFriends listing URL."""
    if page <= 1:
        return listing_url
    parts = urlsplit(listing_url)
    query = f"{parts.query}&page={page}" if parts.query else f"page={page}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def parse_gotfriends_listing(html: str) -> list[dict[str, Any]]:
    """Parse job cards from a GotFriends listing page."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("div.jobs_list") or soup.select_one("div.careers_list")
    if root is None:
        return []

    jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for heading in root.select("h2"):
        parent_link = heading.find_parent("a")
        if parent_link is None:
            continue

        href = _absolute_gotfriends_url(parent_link.get("href", ""))
        if not href or "gotfriends.co.il" not in href:
            continue

        canonical = normalize_job_url(href)
        if not canonical or canonical in seen_urls:
            continue
        seen_urls.add(canonical)

        title = heading.get_text(" ", strip=True)
        if not title or len(title) < 5:
            continue

        jobs.append({
            "title": title,
            "company": _extract_company_from_title(title),
            "location": "",
            "job_url": canonical,
            "source": "gotfriends",
            "description": "",
        })

    return jobs


def collect_gotfriends_jobs(
    query: str,
    *,
    max_pages: int = GOTFRIENDS_MAX_PAGES,
    headless: bool = HEADLESS,
) -> list[dict[str, Any]]:
    """Fetch job cards from GotFriends listing pages for a search query."""
    listing_urls = resolve_gotfriends_listing_urls(query)
    print(f"Searching GotFriends for: {query}")
    print(f"  Listing URLs: {', '.join(listing_urls)}")

    broad_search = any(
        url.rstrip("/").endswith(f"/{category}")
        for url in listing_urls
        for category in GOTFRIENDS_CATEGORIES
    )
    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for listing_url in listing_urls:
        for page_index in range(max_pages):
            page_url = build_gotfriends_page_url(listing_url, page_index + 1)
            status, html = fetch_gotfriends_html(
                page_url,
                headless=headless,
                referer=listing_url,
            )

            if status >= 400 or is_cloudflare_blocked_html(html):
                print(
                    f"  GotFriends returned HTTP {status} "
                    f"({listing_url}, page {page_index + 1})"
                )
                break

            page_jobs = parse_gotfriends_listing(html)
            if not page_jobs:
                break

            added = 0
            for job in page_jobs:
                if broad_search and not _title_matches_query(job.get("title", ""), query):
                    continue
                url = job.get("job_url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                all_jobs.append(job)
                added += 1

            if added == 0 and broad_search:
                break
            if len(page_jobs) < 8:
                break

            time.sleep(1.0)

    print(f"  GotFriends returned {len(all_jobs)} job card(s)")
    return all_jobs
