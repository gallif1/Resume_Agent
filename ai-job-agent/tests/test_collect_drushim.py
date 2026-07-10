"""Tests for Drushim job collection helpers."""

from __future__ import annotations

from collect_jobs import parse_drushim_search_html

SAMPLE_HTML = """
<html><body>
  <div class="job-item">
    <h3><a class="job-url" href="/job/12345/abc/">Backend Developer</a></h3>
    <div class="job-details-top"><a href="#"><span>Acme Ltd</span></a></div>
    <div class="job-details-sub"><span class="display-18"><span>Tel Aviv |</span></span></div>
    <div class="job-intro"><p>Great backend role.</p></div>
  </div>
</body></html>
"""


def test_parse_drushim_search_html_extracts_job_cards():
    jobs = parse_drushim_search_html(
        SAMPLE_HTML,
        page_url="https://www.drushim.co.il/jobs/search/?searchterm=backend",
    )
    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Backend Developer"
    assert job["company"] == "Acme Ltd"
    assert job["location"] == "Tel Aviv"
    assert job["source"] == "drushim"
    assert job["job_url"] == "https://www.drushim.co.il/job/12345/abc/"
    assert job["description"] == "Great backend role."


def test_parse_drushim_search_html_skips_incomplete_cards():
    html = '<html><body><div class="job-item"><h3></h3></div></body></html>'
    jobs = parse_drushim_search_html(
        html,
        page_url="https://www.drushim.co.il/jobs/search/",
    )
    assert jobs == []
