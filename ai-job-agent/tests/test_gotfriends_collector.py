"""Tests for GotFriends job collection helpers."""

from __future__ import annotations

from unittest.mock import patch

import gotfriends_collector as gf


SAMPLE_LISTING_HTML = """
<html>
  <body>
    <div class="jobs_list">
      <a href="/jobslobby/software/backend-developer/senior-backend-dev-at-acme-12345/">
        <h2>Senior Backend Developer בחברת Acme</h2>
      </a>
      <a href="https://www.gotfriends.co.il/jobslobby/software/python-developer/python-role-67890/">
        <h2>Python Developer בחברת Beta</h2>
      </a>
    </div>
  </body>
</html>
"""


def test_parse_gotfriends_listing_extracts_jobs():
    jobs = gf.parse_gotfriends_listing(SAMPLE_LISTING_HTML)

    assert len(jobs) == 2
    assert jobs[0]["title"] == "Senior Backend Developer בחברת Acme"
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["source"] == "gotfriends"
    assert jobs[0]["job_url"].startswith("https://www.gotfriends.co.il/")


def test_parse_gotfriends_listing_returns_empty_for_cloudflare_page():
    html = "<html><title>Attention Required! | Cloudflare</title></html>"
    assert gf.parse_gotfriends_listing(html) == []


def test_fetch_gotfriends_html_uses_playwright_on_http_403():
    blocked = "<html><title>Attention Required! | Cloudflare</title></html>"
    ok = SAMPLE_LISTING_HTML

    with patch("gotfriends_collector.requests.get") as mock_get, patch(
        "gotfriends_collector.fetch_html_with_playwright",
        return_value=(200, ok),
    ) as mock_playwright:
        mock_get.return_value.status_code = 403
        mock_get.return_value.text = blocked

        status, html = gf.fetch_gotfriends_html("https://www.gotfriends.co.il/jobslobby/software/")

    assert status == 200
    assert "jobs_list" in html
    mock_playwright.assert_called_once()


def test_collect_gotfriends_jobs_parses_listing_pages():
    with patch(
        "gotfriends_collector.resolve_gotfriends_listing_urls",
        return_value=["https://www.gotfriends.co.il/jobslobby/software/backend-developer/"],
    ), patch(
        "gotfriends_collector.fetch_gotfriends_html",
        return_value=(200, SAMPLE_LISTING_HTML),
    ):
        jobs = gf.collect_gotfriends_jobs("backend developer", max_pages=1)

    assert len(jobs) == 2
    assert all(job["source"] == "gotfriends" for job in jobs)


def test_resolve_gotfriends_prefers_specific_profession_over_broad_developer():
    with patch("gotfriends_collector.fetch_profession_slugs", return_value={}):
        urls = gf.resolve_gotfriends_listing_urls("Python Developer")

    assert any("python-developer" in url for url in urls)
    assert not any(url.rstrip("/").endswith("/backend-developer") for url in urls)


def test_resolve_gotfriends_caps_broad_lobby_fanout():
    with patch(
        "gotfriends_collector.fetch_profession_slugs",
        return_value={
            "software": [f"slug-{i}" for i in range(10)],
        },
    ):
        urls = gf.resolve_gotfriends_listing_urls("software engineer slug-1")

    assert len(urls) <= 3


def test_fetch_gotfriends_html_skips_playwright_on_server_mode():
    blocked = "<html><title>Attention Required! | Cloudflare</title></html>"

    with patch("gotfriends_collector.requests.get") as mock_get, patch(
        "gotfriends_collector.fetch_html_with_playwright"
    ) as mock_playwright, patch.dict("os.environ", {"AGENT_CV_ID": "cv-test"}, clear=False), patch(
        "gotfriends_collector.AGENT_CV_ID", "cv-test"
    ):
        mock_get.return_value.status_code = 403
        mock_get.return_value.text = blocked

        status, html = gf.fetch_gotfriends_html("https://www.gotfriends.co.il/jobslobby/software/")

    assert status == 403
    assert html == blocked
    mock_playwright.assert_not_called()
