import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from browser_utils import (
    BROWSER_USER_AGENT,
    STRONG_BLOCK_SIGNALS,
    browser_http_headers,
    create_browser_context as _create_browser_context,
)
from config import ENRICH_BLOCKED_DEBUG_DIR, HEADLESS, LINKEDIN_BASE_URL
from console_utils import configure_console, safe_print
from gotfriends_collector import fetch_gotfriends_html
from job_identity import extract_linkedin_job_id
from db import (
    ENRICH_BLOCKED,
    ENRICH_DEFAULT_MAX_ATTEMPTS,
    ENRICH_DEFAULT_RETRY_AFTER_DAYS,
    ENRICH_FAILED,
    ENRICH_NO_DESCRIPTION,
    ENRICH_SUCCESS,
    ENRICH_TIMEOUT,
    enrich_skip_reason,
    get_jobs,
    init_db,
    record_enrichment_attempt,
)

START_MARKERS = ["תיאור משרה", "Job Description"]
END_MARKERS = [
    "לפרופיל החברה",
    "קטגוריה",
    "דרושים IL אתר",
    "קבל התראות",
    "למשרות נוספות",
]

JOB_PAGE_MARKERS = [
    "תיאור משרה",
    "Job Description",
    "שלח/י קורות חיים",
    "דרושים",
    "דרוש/ה",
    "משרה מלאה",
]

DESCRIPTION_SELECTORS = [
    ".vacancyMain",
    ".job-intro",
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[class*="requirement"]',
    "article",
    "main",
]

# Playwright timeouts (milliseconds).
GOTO_TIMEOUT_MS = 45000
WAIT_AFTER_LOAD_MS = 2500
DEFAULT_TIMEOUT_MS = 20000


def _visible_text(page: Page, limit: int = 3000) -> str:
    try:
        return (page.evaluate("() => document.body.innerText || ''") or "")[:limit]
    except Exception:
        return ""


def page_looks_like_job_page(page: Page) -> bool:
    """True when the page has clear Drushim job-page content."""
    url = (page.url or "").lower()
    if "/job/" not in url:
        return False

    text = _visible_text(page, limit=5000).lower()
    if any(marker.lower() in text for marker in JOB_PAGE_MARKERS):
        return True

    try:
        if page.locator(".vacancyMain, .job-intro, #cv-send-btn").count() > 0:
            return True
    except Exception:
        pass
    return False


def page_looks_blocked(page: Page) -> bool:
    """Detect captcha / anti-bot pages using strong evidence only.

    Normal Drushim job pages are never classified as blocked, even when the
    description is missing or the visible text is short.
    """
    if page_looks_like_job_page(page):
        return False

    title = (page.title() or "").lower()
    visible = _visible_text(page, limit=2000).lower()
    combined = f"{title}\n{visible}"

    if any(signal in combined for signal in STRONG_BLOCK_SIGNALS):
        return True

    # Captcha widgets on an otherwise empty/challenge page.
    if len(visible.strip()) < 500 and any(
        token in combined for token in ("captcha", "recaptcha", "hcaptcha")
    ):
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


def extract_from_visible_text(text: str) -> str:
    if not text:
        return ""

    start = -1
    for marker in START_MARKERS:
        start = text.find(marker)
        if start != -1:
            break
    if start == -1:
        return ""

    section = text[start:]
    end = len(section)
    for marker in END_MARKERS:
        pos = section.find(marker, len(START_MARKERS[0]))
        if pos != -1:
            end = min(end, pos)
    return section[:end].strip()


def extract_full_description(page: Page) -> str:
    """Pull job description text using multiple selectors, then visible text."""
    for selector in DESCRIPTION_SELECTORS:
        try:
            locator = page.locator(selector)
            if locator.count():
                text = locator.first.inner_text().strip()
                if len(text) > 80:
                    return text
        except Exception:
            continue

    text = _visible_text(page, limit=12000)
    extracted = extract_from_visible_text(text)
    if extracted:
        return extracted

    # Last resort: meaningful body text from a job URL.
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(cleaned) > 200 and "/job/" in (page.url or "").lower():
        return cleaned[:8000]
    return ""


def fetch_description_http(url: str) -> str:
    """Direct HTTP fallback when Playwright extraction fails."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    headers = browser_http_headers()
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code >= 400:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        extracted = extract_from_visible_text(text)
        if extracted:
            return extracted
        if len(text) > 200:
            return text[:8000]
    except Exception:
        return ""
    return ""


def enrich_linkedin_one(job: dict) -> tuple[str, str | None, str | None]:
    """Fetch a LinkedIn job description via the public guest API (no browser needed)."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return ENRICH_FAILED, None, "requests/beautifulsoup4 not installed"

    url = job.get("job_url", "")
    linkedin_id = extract_linkedin_job_id(url)
    if not linkedin_id:
        return ENRICH_FAILED, None, f"could not extract LinkedIn job id from {url}"

    headers = browser_http_headers()

    candidate_urls = [
        f"{LINKEDIN_BASE_URL}/jobs-guest/jobs/api/jobPosting/{linkedin_id}",
        f"{LINKEDIN_BASE_URL}/jobs/view/{linkedin_id}",
    ]

    last_error: str | None = None
    for fetch_url in candidate_urls:
        try:
            response = requests.get(fetch_url, headers=headers, timeout=30)
        except requests.RequestException as error:
            last_error = f"request failed: {str(error)[:200]}"
            continue

        if response.status_code == 429:
            return ENRICH_BLOCKED, None, "LinkedIn rate limit (HTTP 429)"
        if response.status_code >= 400:
            last_error = f"HTTP {response.status_code} from {fetch_url}"
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        section = soup.select_one(
            ".show-more-less-html__markup, .description__text, "
            "[class*='description'] .core-section-container__content"
        )
        if section is not None:
            text = section.get_text("\n", strip=True)
            if len(text) > 80:
                return ENRICH_SUCCESS, text[:12000], None

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        if len(text) > 300:
            return ENRICH_SUCCESS, text[:8000], None

    return ENRICH_NO_DESCRIPTION, None, last_error


GOTFRIENDS_DESCRIPTION_START = "תיאור המשרה"
GOTFRIENDS_DESCRIPTION_END_MARKERS = [
    "דרישות המשרה",
    "מס' משרה",
    "שלחו קורות חיים",
    "משרות דומות",
]


def extract_gotfriends_description(text: str) -> str:
    """Pull the job description block from a GotFriends job page."""
    if not text:
        return ""

    start = text.find(GOTFRIENDS_DESCRIPTION_START)
    if start == -1:
        return ""

    section = text[start:]
    end = len(section)
    for marker in GOTFRIENDS_DESCRIPTION_END_MARKERS:
        pos = section.find(marker, len(GOTFRIENDS_DESCRIPTION_START))
        if pos != -1:
            end = min(end, pos)
    return section[:end].strip()


def enrich_gotfriends_one(job: dict) -> tuple[str, str | None, str | None]:
    """Fetch a GotFriends job description, with browser fallback on HTTP blocks."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ENRICH_FAILED, None, "beautifulsoup4 not installed"

    url = job.get("job_url", "")
    if not url:
        return ENRICH_FAILED, None, "missing job_url"

    status, html = fetch_gotfriends_html(url)
    if status >= 400:
        return ENRICH_FAILED, None, f"HTTP {status}"

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)
    extracted = extract_gotfriends_description(text)
    if extracted:
        requirements_start = text.find("דרישות המשרה")
        if requirements_start != -1:
            requirements_end = len(text)
            for marker in ("מס' משרה", "שלחו קורות חיים"):
                pos = text.find(marker, requirements_start)
                if pos != -1:
                    requirements_end = min(requirements_end, pos)
            requirements = text[requirements_start:requirements_end].strip()
            if requirements:
                extracted = f"{extracted}\n\n{requirements}"
        return ENRICH_SUCCESS, extracted[:12000], None

    if len(text) > 300:
        return ENRICH_SUCCESS, text[:8000], None

    return ENRICH_NO_DESCRIPTION, None, "no description markers found"


def save_blocked_debug_artifacts(page: Page, job: dict, error: str | None) -> Path:
    """Save diagnostics when a page is classified as blocked."""
    ENRICH_BLOCKED_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_ref = job.get("job_hash") or job.get("id") or "unknown"
    prefix = ENRICH_BLOCKED_DEBUG_DIR / f"job_{job_ref}_{timestamp}"

    screenshot_path = Path(f"{prefix}.png")
    html_path = Path(f"{prefix}.html")
    log_path = Path(f"{prefix}.log")

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        screenshot_path = Path("")

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        html_path = Path("")

    visible = _visible_text(page, limit=1000)
    status = None
    try:
        response = page.evaluate(
            "() => (window.performance.getEntriesByType('navigation')[0] || {}).responseStatus"
        )
        status = response if isinstance(response, int) else None
    except Exception:
        status = None

    log_path.write_text(
        "\n".join([
            f"Time: {datetime.now().isoformat()}",
            f"Job id: {job.get('id')}",
            f"Job hash: {job.get('job_hash')}",
            f"Title: {job.get('title')}",
            f"Company: {job.get('company')}",
            f"Requested URL: {job.get('job_url')}",
            f"Final URL: {page.url}",
            f"HTTP status: {status if status is not None else 'unknown'}",
            f"Page title: {page.title()}",
            f"Error: {error or ''}",
            f"Screenshot: {screenshot_path}",
            f"HTML snapshot: {html_path}",
            "",
            "Visible text (first 1000 chars):",
            visible,
        ]),
        encoding="utf-8",
    )
    safe_print(f"    Saved blocked debug artifacts: {log_path}")
    return log_path


def create_browser_context(
    playwright,
    *,
    headless: bool,
    slowmo: int = 0,
) -> tuple[BrowserContext, Page]:
    context, page = _create_browser_context(playwright, headless=headless, slowmo=slowmo)
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)
    return context, page


def enrich_one(
    page: Page,
    job: dict,
    *,
    debug_blocked: bool = False,
    use_http_fallback: bool = True,
) -> tuple[str, str | None, str | None]:
    """Enrich a single job page. Never raises for page-level failures."""
    url = job.get("job_url", "")
    if not url:
        return ENRICH_FAILED, None, "missing job_url"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
    except PlaywrightTimeoutError as error:
        return ENRICH_TIMEOUT, None, f"goto timeout: {str(error)[:200]}"
    except Exception as error:
        return ENRICH_FAILED, None, f"goto failed: {str(error)[:200]}"

    try:
        page.wait_for_timeout(WAIT_AFTER_LOAD_MS)
    except Exception:
        pass

    if page_looks_blocked(page):
        error = "page blocked (captcha/anti-bot)"
        if debug_blocked:
            save_blocked_debug_artifacts(page, job, error)
        return ENRICH_BLOCKED, None, error

    try:
        description = extract_full_description(page)
    except PlaywrightTimeoutError as error:
        return ENRICH_TIMEOUT, None, f"extract timeout: {str(error)[:200]}"
    except Exception as error:
        return ENRICH_FAILED, None, f"extract failed: {str(error)[:200]}"

    if description:
        return ENRICH_SUCCESS, description, None

    if use_http_fallback:
        http_description = fetch_description_http(url)
        if http_description:
            return ENRICH_SUCCESS, http_description, None

    return ENRICH_NO_DESCRIPTION, None, None


def _job_label(job: dict) -> str:
    return f"{job.get('title', '') or '(no title)'} @ {job.get('company', '') or '(no company)'}"


def enrich_jobs(
    limit: int | None,
    *,
    redo: bool = False,
    retry_failed: bool = False,
    max_attempts: int = ENRICH_DEFAULT_MAX_ATTEMPTS,
    retry_after_days: int = ENRICH_DEFAULT_RETRY_AFTER_DAYS,
    headless: bool = HEADLESS,
    slowmo: int = 0,
    debug_blocked: bool = False,
) -> tuple[dict[str, int], int]:
    """Fetch full descriptions for Drushim, LinkedIn, and GotFriends jobs."""
    all_jobs = [
        job for job in get_jobs()
        if job.get("source") in ("drushim", "linkedin", "gotfriends")
    ]
    pending: list[tuple[dict, str]] = []
    skipped = 0

    for job in all_jobs:
        needs, reason = enrich_skip_reason(
            job,
            redo=redo,
            retry_failed=retry_failed,
            max_attempts=max_attempts,
            retry_after_days=retry_after_days,
        )
        if needs:
            pending.append((job, reason))
        else:
            skipped += 1
            status = job.get("enrich_status") or (
                ENRICH_SUCCESS if job.get("full_description") else "unknown"
            )
            verb = "already enriched" if status == ENRICH_SUCCESS else "already attempted"
            safe_print(f"Skipping {verb} job: {_job_label(job)} — status: {status}")

    if limit is not None:
        pending = pending[:limit]

    counters = {
        ENRICH_SUCCESS: 0,
        ENRICH_NO_DESCRIPTION: 0,
        ENRICH_FAILED: 0,
        ENRICH_TIMEOUT: 0,
        ENRICH_BLOCKED: 0,
    }

    if not pending:
        safe_print("No jobs need enriching.")
        return counters, skipped

    safe_print(f"Enriching {len(pending)} job(s)...")

    # LinkedIn and GotFriends are enriched over plain HTTP; only Drushim needs a browser.
    linkedin_pending = [(job, reason) for job, reason in pending if job.get("source") == "linkedin"]
    gotfriends_pending = [(job, reason) for job, reason in pending if job.get("source") == "gotfriends"]
    drushim_pending = [
        (job, reason)
        for job, reason in pending
        if job.get("source") not in ("linkedin", "gotfriends")
    ]

    for index, (job, reason) in enumerate(linkedin_pending, start=1):
        safe_print(f"[linkedin {index}/{len(linkedin_pending)}] Enriching: {_job_label(job)}")
        status, description, error = enrich_linkedin_one(job)
        record_enrichment_attempt(
            job["id"], status, full_description=description, error=error
        )
        counters[status] = counters.get(status, 0) + 1
        if status == ENRICH_SUCCESS:
            safe_print(f"    -> success ({len(description or '')} chars)")
        else:
            detail = f" — {error}" if error else ""
            safe_print(f"    -> {status}{detail}")
        time.sleep(1.0)

    for index, (job, reason) in enumerate(gotfriends_pending, start=1):
        safe_print(f"[gotfriends {index}/{len(gotfriends_pending)}] Enriching: {_job_label(job)}")
        status, description, error = enrich_gotfriends_one(job)
        record_enrichment_attempt(
            job["id"], status, full_description=description, error=error
        )
        counters[status] = counters.get(status, 0) + 1
        if status == ENRICH_SUCCESS:
            safe_print(f"    -> success ({len(description or '')} chars)")
        else:
            detail = f" — {error}" if error else ""
            safe_print(f"    -> {status}{detail}")
        time.sleep(1.0)

    if not drushim_pending:
        return counters, skipped

    with sync_playwright() as playwright:
        context, page = create_browser_context(playwright, headless=headless, slowmo=slowmo)
        browser = context.browser

        try:
            for index, (job, reason) in enumerate(drushim_pending, start=1):
                if reason.startswith("retry"):
                    prev = job.get("enrich_status") or "unknown"
                    safe_print(
                        f"[{index}/{len(drushim_pending)}] Retrying {prev} job ({reason}): "
                        f"{_job_label(job)}"
                    )
                else:
                    safe_print(f"[{index}/{len(drushim_pending)}] Enriching: {_job_label(job)}")

                status, description, error = enrich_one(
                    page,
                    job,
                    debug_blocked=debug_blocked,
                )
                record_enrichment_attempt(
                    job["id"], status, full_description=description, error=error
                )
                counters[status] = counters.get(status, 0) + 1

                if status == ENRICH_SUCCESS:
                    safe_print(f"    -> success ({len(description or '')} chars)")
                else:
                    detail = f" — {error}" if error else ""
                    safe_print(f"    -> {status}{detail}")

                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
        except KeyboardInterrupt:
            safe_print("\nInterrupted by user — closing browser cleanly...")
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

    return counters, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch full job descriptions from Drushim, LinkedIn, and GotFriends"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to enrich this run")
    parser.add_argument(
        "--redo",
        action="store_true",
        help="Re-fetch every job, even already-enriched ones",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry jobs that previously failed/timed out/were blocked/had no description",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=ENRICH_DEFAULT_MAX_ATTEMPTS,
        help=f"Max enrichment attempts per job (default: {ENRICH_DEFAULT_MAX_ATTEMPTS})",
    )
    parser.add_argument(
        "--retry-after-days",
        type=int,
        default=ENRICH_DEFAULT_RETRY_AFTER_DAYS,
        help=(
            "Retry soft-failed jobs only if the last attempt is older than this "
            f"many days (default: {ENRICH_DEFAULT_RETRY_AFTER_DAYS})"
        ),
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in visible mode (overrides HEADLESS env)",
    )
    parser.add_argument(
        "--slowmo",
        type=int,
        default=0,
        help="Slow down Playwright actions by N milliseconds (debugging)",
    )
    parser.add_argument(
        "--debug-blocked",
        action="store_true",
        help="Save screenshot/HTML/log artifacts when a page is classified as blocked",
    )
    args = parser.parse_args()
    configure_console()

    init_db()
    counters, skipped = enrich_jobs(
        limit=args.limit,
        redo=args.redo,
        retry_failed=args.retry_failed,
        max_attempts=args.max_attempts,
        retry_after_days=args.retry_after_days,
        headless=False if args.headed else HEADLESS,
        slowmo=args.slowmo,
        debug_blocked=args.debug_blocked,
    )

    enriched = counters.get(ENRICH_SUCCESS, 0)
    safe_print(
        "\nEnrichment summary: "
        f"success={enriched}, "
        f"no_description={counters.get(ENRICH_NO_DESCRIPTION, 0)}, "
        f"failed={counters.get(ENRICH_FAILED, 0)}, "
        f"timeout={counters.get(ENRICH_TIMEOUT, 0)}, "
        f"blocked={counters.get(ENRICH_BLOCKED, 0)}, "
        f"skipped={skipped}"
    )
    safe_print("Run: python src/match_jobs.py")


if __name__ == "__main__":
    main()
