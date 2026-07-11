"""Tests for Drushim job collection helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from collect_jobs import (
    EXTRACT_JOBS_JS,
    DrushimBrowserSession,
    _collect_drushim_with_page,
    page_looks_blocked_drushim,
)


def test_page_looks_blocked_drushim_does_not_flag_search_results_with_meta_robots():
    page = MagicMock()
    page.url = "https://www.drushim.co.il/jobs/search/?searchterm=python"
    page.title.return_value = "דרושים python"
    page.evaluate.return_value = "Senior Python Developer"
    page.content.return_value = (
        "<html><head><meta name='robots' content='index, follow'></head>"
        "<body><div class='job-item preferred'>job</div></body></html>"
    )

    assert page_looks_blocked_drushim(page) is False


def test_extract_jobs_js_includes_expected_job_fields():
    for key in ("title", "company", "location", "job_url", "source", "description"):
        assert key in EXTRACT_JOBS_JS


def test_drushim_session_reuses_page_without_relaunching_browser():
    page = MagicMock()
    page.evaluate.return_value = [
        {
            "title": "Python Dev",
            "company": "Acme",
            "location": "Tel Aviv",
            "job_url": "https://www.drushim.co.il/job/1/",
            "source": "drushim",
            "description": "",
        }
    ]
    response = MagicMock()
    response.status = 200
    page.goto.return_value = response

    with patch("collect_jobs.create_browser_context", return_value=(MagicMock(), page)), patch(
        "collect_jobs.sync_playwright"
    ) as mock_playwright:
        mock_playwright.return_value.start.return_value = MagicMock()
        with DrushimBrowserSession(headless=True) as session:
            outcome1 = session.collect("python")
            outcome2 = session.collect("backend")

    assert outcome1.status == "ok"
    assert outcome2.status == "ok"
    assert page.goto.call_count == 2
    mock_playwright.return_value.start.assert_called_once()


def test_collect_drushim_with_page_skips_visible_retry_when_disabled():
    page = MagicMock()
    response = MagicMock()
    response.status = 403
    page.goto.return_value = response

    with patch("collect_jobs.save_debug_artifacts", return_value=MagicMock()), patch(
        "collect_jobs.collect_drushim_jobs"
    ) as mock_retry:
        outcome = _collect_drushim_with_page(
            page, "python", headless=True, allow_visible_retry=False
        )

    assert outcome.status == "http_error"
    mock_retry.assert_not_called()
