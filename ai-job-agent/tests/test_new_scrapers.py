"""Unit tests for AllJobs / Indeed / Secret Tel Aviv / Geektime scrapers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from collection_report import CollectionOutcome
from scrapers.alljobs_scraper import (
    build_alljobs_search_url,
    collect_alljobs_jobs,
    parse_alljobs_listing,
)
from scrapers.geektime_scraper import parse_geektime_api_jobs, parse_geektime_listing
from scrapers.indeed_israel_scraper import build_indeed_search_url, parse_indeed_listing
from scrapers.secret_tel_aviv_scraper import parse_secret_tel_aviv_listing


ALLJOBS_HTML = """
<html><body>
  <div class="job-content-top">
    <div class="job-content-top-title">
      <a title="דרושים | Python Developer" href="/Search/UploadSingle.aspx?JobID=8740250">Python Developer</a>
      <a class="T14">Unitask</a>
    </div>
    <div class="job-content-top-location">מיקום המשרה: חיפה</div>
    <div class="job-content-top-desc">Great Python role in Haifa</div>
  </div>
</body></html>
"""

INDEED_HTML = """
<html><body>
  <div class="job_seen_beacon" data-jk="abc123def456">
    <h2 class="jobTitle"><a data-jk="abc123def456" href="/viewjob?jk=abc123def456">Backend Engineer</a></h2>
    <span data-testid="company-name">Acme IL</span>
    <div data-testid="text-location">Tel Aviv</div>
    <div data-testid="jobsnippet">Build APIs with Python</div>
  </div>
</body></html>
"""

STA_HTML = """
<html><body>
  <div class="wpjb-job-list">
    <div class="wpjb-grid-row">
      <a href="/job/senior-product-manager-rounds/">Senior Product Manager</a>
      <span class="wpjb-job-company">Rounds</span>
      <span class="wpjb-job-location">Tel Aviv</span>
    </div>
  </div>
</body></html>
"""

GEEKTIME_HTML = """
<html><body>
  <script>
    var jobs = [{"id": 55, "title": "Machine Learning Engineer", "company_name": "FintechCo", "city": "Tel Aviv"}];
  </script>
  <a href="https://insider.geektime.co.il/jobs/#jid=55"><h3>Machine Learning Engineer</h3></a>
</body></html>
"""


def test_parse_alljobs_listing_extracts_fields():
    jobs = parse_alljobs_listing(ALLJOBS_HTML)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Python Developer"
    assert jobs[0]["company"] == "Unitask"
    assert jobs[0]["location"] == "חיפה"
    assert jobs[0]["source"] == "alljobs"
    assert "JobID=8740250" in jobs[0]["job_url"]
    assert jobs[0]["description"].startswith("Great Python")


def test_build_alljobs_search_url_includes_freetxt_and_page():
    url = build_alljobs_search_url("python developer", page=2)
    assert "freetxt=python" in url
    assert "page=2" in url


def test_collect_alljobs_jobs_paginates(monkeypatch):
    responses = [
        MagicMock(status_code=200, text="<html></html>"),  # warm homepage
        MagicMock(status_code=200, text=ALLJOBS_HTML),
        MagicMock(status_code=200, text="<html><body></body></html>"),
    ]

    session = MagicMock()
    session.get.side_effect = responses

    with patch("scrapers.alljobs_scraper.requests.Session", return_value=session):
        outcome = collect_alljobs_jobs("python", max_pages=2)

    assert isinstance(outcome, CollectionOutcome)
    assert outcome.status == "ok"
    assert len(outcome.jobs) == 1


def test_parse_indeed_listing_extracts_job_key():
    jobs = parse_indeed_listing(INDEED_HTML)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Backend Engineer"
    assert jobs[0]["company"] == "Acme IL"
    assert "jk=abc123def456" in jobs[0]["job_url"]
    assert jobs[0]["source"] == "indeed"


def test_build_indeed_search_url_encodes_query_safely():
    url = build_indeed_search_url("c++ / .net", start=15)
    assert "q=c%2B%2B" in url or "q=c%2B%2B+%2F" in url or "c%2B%2B" in url
    assert "start=15" in url
    assert "il.indeed.com/jobs?" in url


def test_parse_secret_tel_aviv_listing():
    jobs = parse_secret_tel_aviv_listing(STA_HTML)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Product Manager"
    assert jobs[0]["company"] == "Rounds"
    assert jobs[0]["source"] == "secret_tel_aviv"
    assert "/job/senior-product-manager-rounds" in jobs[0]["job_url"]


def test_parse_geektime_api_and_html():
    api_jobs = parse_geektime_api_jobs(
        [{"id": 9, "title": "Staff Engineer", "company_name": "monday", "city": "TLV"}]
    )
    assert api_jobs[0]["title"] == "Staff Engineer"
    assert api_jobs[0]["source"] == "geektime"

    html_jobs = parse_geektime_listing(GEEKTIME_HTML)
    assert any("Machine Learning" in job["title"] for job in html_jobs)


def test_orchestrator_registers_all_collectors():
    from collect_jobs import _job_collectors

    collectors = _job_collectors()
    assert set(collectors) == {
        "drushim",
        "linkedin",
        "gotfriends",
        "alljobs",
        "indeed",
        "secret_tel_aviv",
        "geektime",
    }
