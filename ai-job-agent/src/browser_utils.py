"""Shared browser helpers for scraping job boards."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from config import BROWSER_PROFILE_DIR, HEADLESS

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1366, "height": 900}
DEFAULT_LOCALE = "he-IL"
DEFAULT_TIMEZONE = "Asia/Jerusalem"
DEFAULT_ACCEPT_LANGUAGE = "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7"

BROWSER_HTTP_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Strong anti-bot indicators only — avoid generic words like "robot" (meta robots).
STRONG_BLOCK_SIGNALS = (
    "verify you are human",
    "אימות אנושי",
    "human verification",
    "access denied",
    "request blocked",
    "unusual traffic",
    "bot detection",
    "checking your browser",
    "cf-browser-verification",
    "please complete the security check",
    "attention required! | cloudflare",
    "just a moment",
    "enable javascript and cookies",
)

CLOUDFLARE_HTML_MARKERS = (
    "cf-challenge",
    "challenge-platform",
    "cf-browser-verification",
)

STEALTH_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--mute-audio",
)

STEALTH_INIT_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)


def format_browser_launch_error(error: Exception) -> str:
    """Return a user-facing Hebrew explanation for Playwright launch failures."""
    text = str(error)
    if "Executable doesn't exist" in text or "browserType.launch" in text:
        return (
            "דפדפן השרת (Playwright Chromium) לא מותקן או לא תואם לגרסת Playwright. "
            "בסביבת Render יש לפרוס מחדש עם Docker image שמריץ "
            "`python -m playwright install chromium`."
        )
    return f"שגיאה בהפעלת הדפדפן: {text[:200]}"


def browser_http_headers(*, referer: str | None = None) -> dict[str, str]:
    """Return browser-like HTTP headers for plain requests."""
    headers = dict(BROWSER_HTTP_HEADERS)
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    return headers


def is_cloudflare_blocked_html(html: str) -> bool:
    """True when HTML looks like a Cloudflare challenge or hard block page."""
    if not html:
        return False
    lowered = html.lower()
    if "attention required! | cloudflare" in lowered:
        return True
    if any(marker in lowered for marker in CLOUDFLARE_HTML_MARKERS):
        return True
    return False


def is_http_blocked(status_code: int, html: str) -> bool:
    """True when an HTTP response should be treated as blocked."""
    if status_code in (401, 403, 429, 503):
        return True
    return status_code >= 400 and is_cloudflare_blocked_html(html)


def _visible_text(page: Page, limit: int = 2000) -> str:
    try:
        return (page.evaluate("() => document.body.innerText || ''") or "")[:limit]
    except Exception:
        return ""


def page_looks_blocked(page: Page, *, job_page_url_fragment: str | None = None) -> bool:
    """Detect captcha / anti-bot pages using strong evidence only."""
    url = (page.url or "").lower()
    if job_page_url_fragment and job_page_url_fragment in url:
        return False

    title = (page.title() or "").lower()
    visible = _visible_text(page).lower()
    combined = f"{title}\n{visible}"

    if any(signal in combined for signal in STRONG_BLOCK_SIGNALS):
        return True

    if len(visible.strip()) < 500 and any(
        token in combined for token in ("captcha", "recaptcha", "hcaptcha")
    ):
        return True

    try:
        html = page.content().lower()
    except Exception:
        return False

    if is_cloudflare_blocked_html(html):
        return True

    return False


def create_browser_context(
    playwright: Playwright,
    *,
    headless: bool = HEADLESS,
    slowmo: int = 0,
    user_data_dir: str | None = None,
) -> tuple[BrowserContext, Page]:
    """Launch Chromium with realistic locale, viewport, and headers."""
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "slow_mo": slowmo,
        "args": list(STEALTH_LAUNCH_ARGS),
        "ignore_default_args": ["--enable-automation"],
    }
    context_kwargs: dict[str, Any] = {
        "locale": DEFAULT_LOCALE,
        "timezone_id": DEFAULT_TIMEZONE,
        "viewport": DEFAULT_VIEWPORT,
        "user_agent": BROWSER_USER_AGENT,
        "extra_http_headers": {"Accept-Language": DEFAULT_ACCEPT_LANGUAGE},
    }

    if user_data_dir:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=False,
                **launch_kwargs,
                **context_kwargs,
            )
        except Exception as error:
            raise RuntimeError(format_browser_launch_error(error)) from error
        page = context.pages[0] if context.pages else context.new_page()
    else:
        try:
            browser = playwright.chromium.launch(**launch_kwargs)
        except Exception as error:
            raise RuntimeError(format_browser_launch_error(error)) from error
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

    page.add_init_script(STEALTH_INIT_SCRIPT)
    return context, page


def fetch_html_with_playwright(
    url: str,
    *,
    headless: bool = HEADLESS,
    user_data_dir: str | None = None,
    wait_after_load_ms: int = 1500,
    goto_timeout_ms: int = 60000,
) -> tuple[int, str]:
    """Fetch a page with Playwright and return (status_code, html)."""
    with sync_playwright() as playwright:
        context, page = create_browser_context(
            playwright,
            headless=headless,
            user_data_dir=user_data_dir,
        )
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=goto_timeout_ms,
            )
            page.wait_for_timeout(wait_after_load_ms)
            status = response.status if response is not None else 0
            return status, page.content()
        finally:
            context.close()
