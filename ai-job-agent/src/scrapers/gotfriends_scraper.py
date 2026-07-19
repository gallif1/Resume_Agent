"""GotFriends scraper — HTTP + Playwright with resilient HTML/JSON parsing."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from browser_utils import (
    browser_http_headers,
    create_browser_context,
    is_cloudflare_blocked_html,
    is_http_blocked,
)
from collection_report import CollectionOutcome
from config import (
    GOTFRIENDS_BASE_URL,
    GOTFRIENDS_BROWSER_FALLBACK,
    GOTFRIENDS_BROWSER_PROFILE_DIR,
    GOTFRIENDS_MAX_PAGES,
    HEADLESS,
)
from date_utils import (
    JOB_MAX_AGE_DAYS,
    filter_jobs_by_max_age,
    normalize_posted_date,
    pick_raw_posted_date,
)
from job_identity import normalize_job_url
from scrapers.base import BaseScraper

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional at import time
    sync_playwright = None  # type: ignore[assignment]

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
]

_BROAD_KEYWORD_SLUG_HINTS: list[tuple[list[str], str, str]] = [
    (["software engineer", "software developer", "מהנדס תוכנה"], "software", "full-stack-developer"),
    (["software", "מפתח תוכנה", "developer", "engineer", "מפתח"], "software", "backend-developer"),
]

_QUERY_STOP_WORDS = frozenset({
    "and", "or", "the", "for", "with", "job", "role", "position",
    "משרת", "דרוש", "דרושים", "מפתח", "מהנדס", "junior", "senior",
})

_JOB_HREF_RE = re.compile(
    r"/jobslobby/[^\"'\s]+/\d+(?:-\d+)?/?",
    re.IGNORECASE,
)

_API_PATH_HINTS = ("/api/", "jobslobby", "jobs", "profession", "filter")

_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
)

_profession_slugs_cache: dict[str, list[str]] | None = None
_gotfriends_access_blocked: bool | None = None
_http_session: requests.Session | None = None
_ua_index = 0


def _mark_gotfriends_blocked(blocked: bool) -> None:
    global _gotfriends_access_blocked
    _gotfriends_access_blocked = blocked


def _gotfriends_known_blocked() -> bool:
    return _gotfriends_access_blocked is True


def _next_user_agent() -> str:
    global _ua_index
    ua = _USER_AGENTS[_ua_index % len(_USER_AGENTS)]
    _ua_index += 1
    return ua


def _session() -> requests.Session:
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
    return _http_session


def _headers(*, referer: str | None = None, accept_json: bool = False) -> dict[str, str]:
    headers = browser_http_headers(referer=referer or GOTFRIENDS_BASE_URL)
    headers["User-Agent"] = _next_user_agent()
    headers["Cache-Control"] = "no-cache"
    headers["Pragma"] = "no-cache"
    if accept_json:
        headers["Accept"] = "application/json, text/plain, */*"
        headers["Origin"] = GOTFRIENDS_BASE_URL
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Site"] = "same-origin"
    return headers


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


def _make_job(
    *,
    title: str,
    job_url: str,
    company: str = "",
    location: str = "",
    description: str = "",
    posted_date: str | None = None,
) -> dict[str, Any] | None:
    title = (title or "").strip()
    canonical = normalize_job_url(_absolute_gotfriends_url(job_url))
    if not title or len(title) < 5 or not canonical:
        return None
    return {
        "title": title,
        "company": (company or "").strip() or _extract_company_from_title(title),
        "location": (location or "").strip(),
        "job_url": canonical,
        "source": "gotfriends",
        "description": (description or "").strip(),
        "posted_date": normalize_posted_date(posted_date, default_to_today=True),
    }


def _warm_gotfriends_cookies() -> None:
    """Hit the homepage once so subsequent listing requests carry cookies."""
    try:
        _session().get(
            GOTFRIENDS_BASE_URL + "/",
            headers=_headers(),
            timeout=20,
        )
    except requests.RequestException:
        pass


def _fetch_gotfriends_html_http(url: str, *, referer: str | None = None) -> tuple[int, str]:
    try:
        response = _session().get(url, headers=_headers(referer=referer), timeout=30)
    except requests.RequestException as error:
        print(f"  GotFriends HTTP request failed for {url}: {error}")
        return 0, ""
    return response.status_code, response.text


def _looks_like_job_api_url(url: str) -> bool:
    lowered = (url or "").lower()
    if "gotfriends" not in lowered:
        return False
    return any(hint in lowered for hint in _API_PATH_HINTS)


def _parse_jobs_from_json_payload(payload: Any) -> list[dict[str, Any]]:
    """Best-effort extraction from GotFriends internal JSON API shapes."""
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        title = str(
            node.get("title")
            or node.get("jobTitle")
            or node.get("name")
            or node.get("Name")
            or ""
        ).strip()
        href = str(
            node.get("url")
            or node.get("link")
            or node.get("jobUrl")
            or node.get("permalink")
            or node.get("slug")
            or ""
        ).strip()
        job_id = node.get("id") or node.get("jobId") or node.get("JobId")
        if not href and job_id is not None:
            category = str(node.get("category") or node.get("Category") or "software").strip() or "software"
            href = f"/jobslobby/{category}/{job_id}/"

        if title and href and ("jobslobby" in href or str(job_id or "").isdigit()):
            raw_date = pick_raw_posted_date(
                node.get("datePosted"),
                node.get("postedAt"),
                node.get("publishDate"),
                node.get("createdAt"),
                node.get("updatedAt"),
                node.get("date"),
            )
            job = _make_job(
                title=title,
                job_url=href,
                company=str(node.get("company") or node.get("companyName") or "").strip(),
                location=str(node.get("location") or node.get("city") or "").strip(),
                description=str(
                    node.get("description")
                    or node.get("jobDescription")
                    or node.get("requirements")
                    or ""
                ).strip(),
                posted_date=str(raw_date).strip() if raw_date is not None else None,
            )
            if job and job["job_url"] not in seen:
                seen.add(job["job_url"])
                jobs.append(job)

        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value)

    walk(payload)
    return jobs


def _fetch_gotfriends_with_playwright(
    url: str,
    *,
    headless: bool = HEADLESS,
) -> tuple[int, str, list[dict[str, Any]]]:
    """Load a listing page in Chromium and capture HTML + intercepted JSON APIs."""
    if sync_playwright is None:
        return 0, "", []

    GOTFRIENDS_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    api_jobs: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        context, page = create_browser_context(
            playwright,
            headless=headless,
            user_data_dir=str(GOTFRIENDS_BROWSER_PROFILE_DIR),
        )
        try:
            def _on_response(response: Any) -> None:
                try:
                    resp_url = response.url or ""
                    if not _looks_like_job_api_url(resp_url):
                        return
                    content_type = (response.headers or {}).get("content-type", "")
                    if "json" not in content_type.lower() and "/api/" not in resp_url.lower():
                        return
                    payload = response.json()
                except Exception:
                    return
                api_jobs.extend(_parse_jobs_from_json_payload(payload))

            page.on("response", _on_response)
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            # Give client-side React/Next a chance to hydrate job cards.
            try:
                page.wait_for_selector(
                    "div.jobs_list h2, div.careers_list h2, a[href*='/jobslobby/'] h2, "
                    "[data-job-id], script#__NEXT_DATA__",
                    timeout=8000,
                )
            except Exception:
                pass
            status = response.status if response is not None else 0
            html = page.content()
            return status, html, api_jobs
        finally:
            context.close()


def fetch_gotfriends_html(
    url: str,
    *,
    headless: bool = HEADLESS,
    referer: str | None = None,
) -> tuple[int, str]:
    """Fetch a GotFriends page, falling back to Playwright when HTTP is blocked."""
    status, html, _api_jobs = fetch_gotfriends_page(
        url, headless=headless, referer=referer
    )
    return status, html


def fetch_gotfriends_page(
    url: str,
    *,
    headless: bool = HEADLESS,
    referer: str | None = None,
) -> tuple[int, str, list[dict[str, Any]]]:
    """Fetch page HTML and any intercepted API jobs."""
    if _gotfriends_known_blocked() and not GOTFRIENDS_BROWSER_FALLBACK:
        return 403, "", []

    if not _session().cookies:
        _warm_gotfriends_cookies()

    status, html = _fetch_gotfriends_html_http(url, referer=referer)
    if status == 200 and not is_cloudflare_blocked_html(html):
        _mark_gotfriends_blocked(False)
        return status, html, []

    if status and not is_http_blocked(status, html) and html and not is_cloudflare_blocked_html(html):
        return status, html, []

    if not GOTFRIENDS_BROWSER_FALLBACK:
        if status in (403, 429, 503) or is_cloudflare_blocked_html(html):
            _mark_gotfriends_blocked(True)
        return status or 403, html, []

    print(
        f"  GotFriends HTTP {status or 'error'} / anti-bot for {url} — "
        "retrying with Playwright..."
    )
    browser_status, browser_html, api_jobs = _fetch_gotfriends_with_playwright(
        url, headless=headless
    )
    if is_cloudflare_blocked_html(browser_html) and headless:
        print("  GotFriends still blocked in headless mode — retrying visibly...")
        browser_status, browser_html, api_jobs = _fetch_gotfriends_with_playwright(
            url, headless=False
        )

    if is_cloudflare_blocked_html(browser_html) and not api_jobs:
        _mark_gotfriends_blocked(True)
        print(
            "  GotFriends is blocked by Cloudflare. "
            "Open the site once in the agent browser profile, complete the challenge, then retry."
        )
    else:
        _mark_gotfriends_blocked(False)

    return browser_status, browser_html, api_jobs


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
        for keywords, category, slug in _BROAD_KEYWORD_SLUG_HINTS:
            if any(keyword in query_l for keyword in keywords):
                urls.append(f"{GOTFRIENDS_BASE_URL}/jobslobby/{category}/{slug}/")
                break

    if not urls:
        for category in GOTFRIENDS_CATEGORIES:
            urls.append(f"{GOTFRIENDS_BASE_URL}/jobslobby/{category}/")

    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped[:3]


def build_gotfriends_page_url(listing_url: str, page: int) -> str:
    """Append pagination to a GotFriends listing URL."""
    if page <= 1:
        return listing_url
    parts = urlsplit(listing_url)
    query = f"{parts.query}&page={page}" if parts.query else f"page={page}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _parse_next_data_jobs(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    if script is None or not script.string:
        return []
    try:
        payload = json.loads(script.string)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return _parse_jobs_from_json_payload(payload)


def _parse_json_ld_jobs(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") not in ("JobPosting", ["JobPosting"]):
                if item.get("@type") != "JobPosting":
                    continue
            title = str(item.get("title") or "").strip()
            href = str(item.get("url") or item.get("@id") or "").strip()
            org = item.get("hiringOrganization") if isinstance(item.get("hiringOrganization"), dict) else {}
            company = str(org.get("name") or "").strip()
            loc_obj = item.get("jobLocation")
            location = ""
            if isinstance(loc_obj, dict):
                address = loc_obj.get("address") if isinstance(loc_obj.get("address"), dict) else {}
                location = str(
                    address.get("addressLocality")
                    or address.get("addressRegion")
                    or ""
                ).strip()
            raw_date = pick_raw_posted_date(
                item.get("datePosted"),
                item.get("datePublished"),
                item.get("validThrough"),
            )
            job = _make_job(
                title=title,
                job_url=href,
                company=company,
                location=location,
                description=str(item.get("description") or "").strip(),
                posted_date=str(raw_date).strip() if raw_date is not None else None,
            )
            if job:
                jobs.append(job)
    return jobs


def parse_gotfriends_listing(html: str) -> list[dict[str, Any]]:
    """Parse job cards from a GotFriends listing page (classic + React/Next)."""
    if not html or is_cloudflare_blocked_html(html):
        return []

    jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def _add(job: dict[str, Any] | None) -> None:
        if not job:
            return
        url = job.get("job_url") or ""
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        jobs.append(job)

    for job in _parse_next_data_jobs(html):
        _add(job)
    for job in _parse_json_ld_jobs(html):
        _add(job)

    soup = BeautifulSoup(html, "html.parser")
    roots = soup.select("div.jobs_list, div.careers_list, main, [class*='jobs'], [class*='career']")
    if not roots:
        roots = [soup]

    for root in roots:
        for heading in root.select("h2, h3"):
            parent_link = heading.find_parent("a")
            if parent_link is None:
                sibling = heading.find_next("a", href=_JOB_HREF_RE)
                parent_link = sibling
            if parent_link is None:
                continue
            href = parent_link.get("href", "")
            if "jobslobby" not in href:
                continue
            _add(
                _make_job(
                    title=heading.get_text(" ", strip=True),
                    job_url=href,
                )
            )

        for anchor in root.select("a[href*='/jobslobby/']"):
            href = anchor.get("href", "")
            if not _JOB_HREF_RE.search(href):
                continue
            title = anchor.get_text(" ", strip=True)
            if not title or len(title) < 5:
                title_el = anchor.select_one("h2, h3, .title, [class*='title']")
                title = title_el.get_text(" ", strip=True) if title_el else title
            _add(_make_job(title=title, job_url=href))

        for card in root.select("[data-job-id], [data-id]"):
            job_id = card.get("data-job-id") or card.get("data-id")
            if not job_id or not str(job_id).isdigit():
                continue
            title_el = card.select_one("h2, h3, .title, [class*='title']")
            link = card.select_one("a[href*='/jobslobby/']")
            href = link.get("href") if link else f"/jobslobby/software/{job_id}/"
            title = title_el.get_text(" ", strip=True) if title_el else (link.get_text(" ", strip=True) if link else "")
            date_el = card.select_one("time[datetime], [class*='date'], [class*='Date']")
            raw_date = ""
            if date_el is not None:
                raw_date = (date_el.get("datetime") or date_el.get_text(" ", strip=True) or "").strip()
            if not raw_date:
                card_text = card.get_text(" ", strip=True)
                for token in (
                    "היום",
                    "אתמול",
                    "לפני יומיים",
                    "לפני חודשיים",
                    "לפני שנה",
                    "לפני שנתיים",
                    "לפני",
                ):
                    if token in card_text:
                        # Capture a short relative fragment from the card text.
                        m = re.search(
                            r"(היום|אתמול|לפני\s+יומיים|לפני\s+חודשיים|לפני\s+שנתיים|"
                            r"לפני\s+שנה|לפני\s+\d+\s*(?:ימים|יום|שעות|שעה|שבועות|שבוע|חודשים|חודש|שנים))",
                            card_text,
                        )
                        raw_date = m.group(0) if m else token
                        break
            _add(_make_job(title=title, job_url=href, posted_date=raw_date or None))

    return jobs


class GotfriendsScraper(BaseScraper):
    source_id = "gotfriends"
    label_he = "גוטפרנדס"

    def collect(
        self,
        query: str,
        *,
        max_pages: int = GOTFRIENDS_MAX_PAGES,
        headless: bool = HEADLESS,
        known_job_urls: set[str] | None = None,
        **_kwargs: Any,
    ) -> CollectionOutcome:
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
        last_status: int | None = None
        blocked = False
        total_age_skipped = 0
        total_known_skipped = 0

        for listing_url in listing_urls:
            for page_index in range(max_pages):
                page_url = build_gotfriends_page_url(listing_url, page_index + 1)
                status, html, api_jobs = fetch_gotfriends_page(
                    page_url,
                    headless=headless,
                    referer=listing_url,
                )
                last_status = status or last_status

                if status >= 400 or is_cloudflare_blocked_html(html):
                    if api_jobs:
                        page_jobs = api_jobs
                    else:
                        print(
                            f"  GotFriends returned HTTP {status} "
                            f"({listing_url}, page {page_index + 1})"
                        )
                        blocked = blocked or is_http_blocked(status, html) or is_cloudflare_blocked_html(html)
                        break
                else:
                    page_jobs = parse_gotfriends_listing(html)
                    if api_jobs:
                        # Prefer richer API payloads when available.
                        merged = {job["job_url"]: job for job in page_jobs}
                        for job in api_jobs:
                            merged[job["job_url"]] = job
                        page_jobs = list(merged.values())

                if not page_jobs:
                    break

                # Drop known URLs first, then apply 30-day freshness filter.
                candidates: list[dict[str, Any]] = []
                known_skipped = 0
                for job in page_jobs:
                    if broad_search and not _title_matches_query(job.get("title", ""), query):
                        continue
                    url = job.get("job_url", "")
                    if not url or url in seen_urls:
                        continue
                    canonical = normalize_job_url(url)
                    if known_job_urls and canonical and canonical in known_job_urls:
                        known_skipped += 1
                        continue
                    candidates.append(job)

                total_known_skipped += known_skipped
                kept, age_skipped, all_old = filter_jobs_by_max_age(candidates)
                total_age_skipped += age_skipped
                if all_old:
                    print(
                        f"  GotFriends page {page_index + 1}: all dated jobs older than "
                        f"{JOB_MAX_AGE_DAYS} days — early exit"
                    )
                    break

                added = 0
                for job in kept:
                    url = job.get("job_url", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    all_jobs.append(job)
                    added += 1

                if added == 0 and broad_search and not known_skipped and not age_skipped:
                    break
                if len(page_jobs) < 8:
                    break
                time.sleep(1.0)

        if total_age_skipped or total_known_skipped:
            print(
                f"  GotFriends filters: skipped {total_age_skipped} old, "
                f"{total_known_skipped} already in DB"
            )
        print(f"  GotFriends returned {len(all_jobs)} job card(s)")
        if all_jobs:
            return self.ok_outcome(all_jobs, http_status=last_status)
        if blocked or _gotfriends_known_blocked():
            return self.empty_outcome(
                status="blocked",
                reason="GotFriends blocked by Cloudflare / anti-bot",
                reason_he="גוטפרנדס חסם את הגישה (Cloudflare / anti-bot)",
                http_status=last_status,
            )
        return self.empty_outcome(
            status="empty",
            reason=f"No GotFriends jobs for '{query}'",
            reason_he=f"גוטפרנדס: לא נמצאו משרות לחיפוש '{query}'",
            http_status=last_status,
        )


def collect_gotfriends_jobs(
    query: str,
    *,
    max_pages: int = GOTFRIENDS_MAX_PAGES,
    headless: bool = HEADLESS,
    known_job_urls: set[str] | None = None,
) -> CollectionOutcome:
    """Fetch job cards from GotFriends listing pages for a search query."""
    return GotfriendsScraper().collect(
        query,
        max_pages=max_pages,
        headless=headless,
        known_job_urls=known_job_urls,
    )
