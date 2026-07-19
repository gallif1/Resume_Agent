"""Tests for Drushim job collection helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from collect_jobs import (
    EXTRACT_JOBS_JS,
    DrushimBrowserSession,
    _collect_drushim_with_page,
    _drushim_uses_browser,
    build_drushim_api_search_url,
    collect_drushim_jobs_api,
    collect_drushim_jobs_http,
    page_looks_blocked_drushim,
    parse_drushim_api_jobs,
    parse_drushim_search_html,
)

SAMPLE_DRUSHIM_HTML = """
<html><body>
  <div class="job-item preferred">
    <h3><span class="job-url">Python Developer</span></h3>
    <div class="job-details-top"><a><span>Acme Ltd</span></a></div>
    <div class="job-details-sub"><span class="display-18"><span>Tel Aviv|</span></span></div>
    <div class="job-details-sub"><span class="display-14">לפני יומיים</span></div>
    <div class="job-intro"><p>Great role</p></div>
    <a href="/job/12345/abc/">link</a>
  </div>
</body></html>
"""

SAMPLE_DRUSHIM_API_PAGE = {
    "ResultList": [
        {
            "Code": 111,
            "Company": {"CompanyDisplayName": "Acme"},
            "JobContent": {
                "Name": "Python Developer",
                "JobCode": 111,
                "Description": "<p>Build APIs</p>",
                "Regions": [{"NameInHebrew": "תל אביב"}],
            },
            "JobInfo": {
                "Link": "/job/111/abcd/",
                "Hash": "ABCD",
                "JumpDateString": "היום",
                "DateActual": "2026-07-18T08:00:00Z",
            },
        },
        {
            "Code": 222,
            "Company": {"NameInHebrew": "Beta"},
            "JobContent": {
                "Name": "Backend Engineer",
                "JobCode": 222,
                "Description": "Services",
                "Regions": [],
            },
            "JobInfo": {"Link": "/job/222/efgh/", "Hash": "EFGH"},
        },
    ],
    "NextPageNumber": 1,
    "TotalPagesNumber": 3,
    "Count": 2,
}


def test_parse_drushim_search_html_extracts_job_fields():
    jobs = parse_drushim_search_html(SAMPLE_DRUSHIM_HTML)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Python Developer"
    assert jobs[0]["company"] == "Acme Ltd"
    assert jobs[0]["location"] == "Tel Aviv"
    assert jobs[0]["job_url"] == "https://www.drushim.co.il/job/12345/abc/"
    assert jobs[0]["source"] == "drushim"
    assert jobs[0]["description"] == "Great role"
    assert jobs[0]["posted_date"]  # normalized ISO date from "לפני יומיים"


def test_parse_drushim_api_jobs_extracts_fields():
    jobs = parse_drushim_api_jobs(SAMPLE_DRUSHIM_API_PAGE)

    assert len(jobs) == 2
    assert jobs[0]["title"] == "Python Developer"
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["location"] == "תל אביב"
    assert jobs[0]["job_url"] == "https://www.drushim.co.il/job/111/abcd"
    assert jobs[0]["description"] == "Build APIs"
    assert jobs[0]["source"] == "drushim"
    assert jobs[0]["posted_date"] == "2026-07-18"


def test_build_drushim_api_search_url_pagination():
    assert "searchterm=python" in build_drushim_api_search_url("python")
    assert "page=" not in build_drushim_api_search_url("python")
    assert "page=2" in build_drushim_api_search_url("python", page=2)


def test_collect_drushim_jobs_api_paginates():
    page1 = dict(SAMPLE_DRUSHIM_API_PAGE)
    page2 = {
        "ResultList": [
            {
                "Code": 333,
                "Company": {"CompanyDisplayName": "Gamma"},
                "JobContent": {"Name": "Django Dev", "JobCode": 333, "Description": "", "Regions": []},
                "JobInfo": {"Link": "/job/333/zzzz/", "Hash": "ZZZZ"},
            }
        ],
        "NextPageNumber": -1,
        "TotalPagesNumber": 2,
        "Count": 1,
    }

    responses = [
        MagicMock(status_code=200, **{}),
        MagicMock(status_code=200, **{}),
    ]
    responses[0].json.return_value = page1
    responses[1].json.return_value = page2

    with patch("collect_jobs.requests.get", side_effect=responses) as mock_get, patch(
        "collect_jobs.time.sleep"
    ):
        outcome = collect_drushim_jobs_api("python", max_pages=3)

    assert outcome.status == "ok"
    assert len(outcome.jobs) == 3
    assert mock_get.call_count == 2


def test_collect_drushim_jobs_http_returns_jobs():
    with patch("collect_jobs.collect_drushim_jobs_api") as mock_api, patch(
        "collect_jobs.requests.get"
    ) as mock_get:
        mock_api.return_value = MagicMock(status="empty", jobs=[], reason_he=None)
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = SAMPLE_DRUSHIM_HTML

        outcome = collect_drushim_jobs_http("python")

    assert outcome.status == "ok"
    assert len(outcome.jobs) == 1


def test_collect_drushim_jobs_http_prefers_api():
    api_jobs = parse_drushim_api_jobs(SAMPLE_DRUSHIM_API_PAGE)
    with patch("collect_jobs.collect_drushim_jobs_api") as mock_api, patch(
        "collect_jobs.requests.get"
    ) as mock_get:
        mock_api.return_value = MagicMock(status="ok", jobs=api_jobs, reason_he=None)

        outcome = collect_drushim_jobs_http("python")

    assert outcome.status == "ok"
    assert len(outcome.jobs) == 2
    mock_get.assert_not_called()


def test_drushim_uses_browser_false_when_browser_fallback_disabled():
    with patch("collect_jobs.DRUSHIM_BROWSER_FALLBACK", False):
        assert _drushim_uses_browser() is False


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
    for key in (
        "title",
        "company",
        "location",
        "job_url",
        "source",
        "description",
        "posted_date",
    ):
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
    empty_api = MagicMock(status="empty", jobs=[], reason_he=None)

    with patch("collect_jobs.create_browser_context", return_value=(MagicMock(), page)), patch(
        "collect_jobs.sync_playwright"
    ) as mock_playwright, patch(
        "collect_jobs.collect_drushim_jobs_api", return_value=empty_api
    ):
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
