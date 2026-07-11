"""Tests for Drushim job collection helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from collect_jobs import EXTRACT_JOBS_JS, page_looks_blocked_drushim


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
