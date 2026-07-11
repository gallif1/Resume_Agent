"""Automated site login and session persistence for job applications."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page

from application_providers.provider_utils import detect_linkedin_auth_wall
from apply_jobs import (
    DRUSHIM_BASE_URL,
    attempt_credential_login as drushim_attempt_login,
    is_logged_in as drushim_is_logged_in,
)
from config import LINKEDIN_BASE_URL, cv_data_dir
from site_credentials import get_drushim_credentials, get_linkedin_credentials

LINKEDIN_LOGIN_URL = f"{LINKEDIN_BASE_URL}/login"
LINKEDIN_USERNAME_SELECTOR = "#username"
LINKEDIN_PASSWORD_SELECTOR = "#password"
LINKEDIN_SUBMIT_SELECTOR = "button[type='submit']"


def cv_browser_profile_dir(cv_id: str) -> Path:
    """Per-CV Chromium profile — cookies persist between apply attempts."""
    path = cv_data_dir(cv_id) / "browser_profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def linkedin_storage_state_path(cv_id: str) -> Path:
    return cv_data_dir(cv_id) / "browser" / "linkedin_storage_state.json"


def drushim_storage_state_path(cv_id: str) -> Path:
    return cv_data_dir(cv_id) / "browser" / "drushim_storage_state.json"


def save_storage_state(context: BrowserContext, path: Path) -> None:
    """Persist Playwright cookies/localStorage for reuse after deploy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))


def apply_storage_state(context: BrowserContext, path: Path) -> bool:
    """Load cookies from a saved Playwright storage_state JSON file."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    cookies = data.get("cookies")
    if not cookies:
        return False
    try:
        context.add_cookies(cookies)
        return True
    except Exception:
        return False


def import_linkedin_storage_state(cv_id: str, payload: dict[str, Any]) -> Path:
    """Save user-provided Playwright storage_state for LinkedIn."""
    path = linkedin_storage_state_path(cv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def bootstrap_site_sessions(context: BrowserContext, cv_id: str) -> None:
    """Seed browser context with any saved per-CV session cookies."""
    apply_storage_state(context, linkedin_storage_state_path(cv_id))
    apply_storage_state(context, drushim_storage_state_path(cv_id))


def has_linkedin_session_cookie(page: Page) -> bool:
    try:
        cookies = page.context.cookies()
        return any(cookie.get("name") == "li_at" and cookie.get("value") for cookie in cookies)
    except Exception:
        return False


def is_linkedin_logged_in(page: Page) -> bool:
    if has_linkedin_session_cookie(page):
        return True
    if detect_linkedin_auth_wall(page):
        return False
    return "feed" in (page.url or "").lower()


def attempt_linkedin_credential_login(page: Page, email: str, password: str) -> bool:
    """Best-effort LinkedIn email/password login (no 2FA support)."""
    try:
        page.goto(LINKEDIN_LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        page.locator(LINKEDIN_USERNAME_SELECTOR).first.fill(email, timeout=10000)
        page.locator(LINKEDIN_PASSWORD_SELECTOR).first.fill(password, timeout=10000)
        submit = page.locator(LINKEDIN_SUBMIT_SELECTOR).first
        if submit.count():
            submit.click()
        else:
            page.locator(LINKEDIN_PASSWORD_SELECTOR).first.press("Enter")
        page.wait_for_timeout(4000)
    except Exception:
        return False
    return has_linkedin_session_cookie(page) or not detect_linkedin_auth_wall(page)


def ensure_linkedin_session(page: Page, cv_id: str) -> bool:
    """Return True when LinkedIn session is ready for Easy Apply automation."""
    if is_linkedin_logged_in(page):
        return True

    email, password = get_linkedin_credentials(cv_id)
    if email and password:
        if attempt_linkedin_credential_login(page, email, password):
            save_storage_state(page.context, linkedin_storage_state_path(cv_id))
            return True

    return is_linkedin_logged_in(page)


def ensure_drushim_session(page: Page, cv_id: str) -> bool:
    """Return True when Drushim session is ready for one-click apply."""
    try:
        if not drushim_is_logged_in(page):
            page.goto(DRUSHIM_BASE_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
    except Exception:
        pass

    if drushim_is_logged_in(page):
        return True

    email, password = get_drushim_credentials(cv_id)
    if email and password:
        if drushim_attempt_login(page, email, password):
            save_storage_state(page.context, drushim_storage_state_path(cv_id))
            return True

    return drushim_is_logged_in(page)
