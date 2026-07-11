"""Tests for shared browser scraping helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from browser_utils import (
    is_cloudflare_blocked_html,
    is_http_blocked,
    page_looks_blocked,
)


def test_is_cloudflare_blocked_html_detects_challenge_page():
    html = "<html><title>Attention Required! | Cloudflare</title><body></body></html>"
    assert is_cloudflare_blocked_html(html) is True


def test_is_cloudflare_blocked_html_allows_normal_page():
    html = "<html><head><meta name='robots' content='index, follow'></head><body>jobs</body></html>"
    assert is_cloudflare_blocked_html(html) is False


def test_is_http_blocked_treats_403_as_blocked():
    assert is_http_blocked(403, "<html>blocked</html>") is True


def test_page_looks_blocked_ignores_meta_robots():
    page = MagicMock()
    page.url = "https://www.drushim.co.il/jobs/search/?searchterm=python"
    page.title.return_value = "דרושים python"
    page.evaluate.return_value = "Senior Python Developer at CodeValue"
    page.content.return_value = (
        "<html><head><meta name='robots' content='index, follow'></head>"
        "<body><div class='job-item'>job</div></body></html>"
    )

    assert page_looks_blocked(page) is False


def test_format_browser_launch_error_detects_missing_executable():
    from browser_utils import format_browser_launch_error

    message = format_browser_launch_error(
        RuntimeError(
            "BrowserType.launch: Executable doesn't exist at "
            "/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-linux64/"
        )
    )
    assert "Playwright Chromium" in message
    assert "לא מותקן" in message
    page = MagicMock()
    page.url = "https://www.gotfriends.co.il/jobslobby/software/"
    page.title.return_value = "Attention Required! | Cloudflare"
    page.evaluate.return_value = "Checking your browser before accessing"
    page.content.return_value = "<html><body>cf-challenge</body></html>"

    assert page_looks_blocked(page) is True
