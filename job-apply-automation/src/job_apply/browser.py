"""Playwright browser helpers for the standalone apply automation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Playwright

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1366, "height": 900}
DEFAULT_LOCALE = "he-IL"
DEFAULT_TIMEZONE = "Asia/Jerusalem"
DEFAULT_ACCEPT_LANGUAGE = "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7"

STEALTH_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-extensions",
    "--mute-audio",
)

STEALTH_INIT_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = PACKAGE_ROOT / "logs"
DATA_DIR = PACKAGE_ROOT / "data"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"


def create_browser_context(
    playwright: Playwright,
    *,
    headless: bool = True,
    user_data_dir: str | Path | None = None,
    slow_mo_ms: int | None = None,
) -> tuple[BrowserContext, Page]:
    # When showing the browser live, slow actions slightly so the user can follow.
    effective_slow_mo = slow_mo_ms if slow_mo_ms is not None else (150 if not headless else 0)
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "slow_mo": effective_slow_mo,
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

    profile = str(user_data_dir) if user_data_dir else None
    if profile:
        Path(profile).mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile,
            accept_downloads=False,
            **launch_kwargs,
            **context_kwargs,
        )
        page = context.pages[0] if context.pages else context.new_page()
    else:
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

    page.add_init_script(STEALTH_INIT_SCRIPT)
    return context, page
