"""Tests for LinkedIn guest job collection resilience and pagination."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from collect_jobs import (
    _parse_linkedin_cards,
    build_linkedin_search_url,
    collect_linkedin_jobs,
    save_jobs_to_db,
)


SAMPLE_LINKEDIN_HTML = """
<html><body>
<ul>
  <li>
    <div class="base-card">
      <a class="base-card__full-link" href="https://il.linkedin.com/jobs/view/junior-swe-at-acme-1111111">
        <h3 class="base-search-card__title">Junior Software Engineer</h3>
      </a>
      <h4 class="base-search-card__subtitle"><a>Acme</a></h4>
      <span class="job-search-card__location">Tel Aviv, Israel</span>
      <time class="job-search-card__listdate--new" datetime="2026-07-18">1 day ago</time>
    </div>
  </li>
  <li>
    <div class="base-card">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/2222222">
        <h3 class="base-search-card__title">Senior Backend Engineer</h3>
      </a>
      <h4 class="base-search-card__subtitle"><a>Beta</a></h4>
      <span class="job-search-card__location">Israel</span>
      <time class="job-search-card__listdate" datetime="2026-07-10">1 week ago</time>
    </div>
  </li>
</ul>
</body></html>
"""


def test_build_linkedin_search_url_uses_broad_israel_defaults():
    url = build_linkedin_search_url("Software Engineer", start=10)
    assert "keywords=Software+Engineer" in url or "keywords=Software%20Engineer" in url
    assert "location=Israel" in url
    assert "geoId=101620260" in url
    assert "start=10" in url
    # Past-month guest time filter; no seniority / experience filters.
    assert "f_TPR=r2592000" in url
    assert "f_E=" not in url


def test_parse_linkedin_cards_extracts_jobs():
    jobs = _parse_linkedin_cards(SAMPLE_LINKEDIN_HTML)
    assert len(jobs) == 2
    assert jobs[0]["title"] == "Junior Software Engineer"
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["job_url"] == "https://www.linkedin.com/jobs/view/1111111"
    assert jobs[0]["source"] == "linkedin"
    assert jobs[0]["posted_date"] == "2026-07-18"
    assert jobs[1]["posted_date"] == "2026-07-10"


def test_collect_linkedin_jobs_paginates_with_actual_page_size():
    """Guest API returns ~10 cards; old code assumed 25 and stopped after page 1."""
    page_html = SAMPLE_LINKEDIN_HTML
    # Make two distinct pages with different ids.
    page2 = page_html.replace("1111111", "3333333").replace("2222222", "4444444")

    responses = [
        MagicMock(status_code=200, text=page_html),
        MagicMock(status_code=200, text=page2),
        MagicMock(status_code=200, text=""),
    ]

    with patch("collect_jobs.requests.get", side_effect=responses) as mock_get, patch(
        "collect_jobs.time.sleep"
    ), patch("collect_jobs.LINKEDIN_JOBS_PER_PAGE", 10), patch(
        "collect_jobs.LINKEDIN_MAX_RETRIES", 1
    ):
        outcome = collect_linkedin_jobs("Software Engineer", max_pages=3)

    assert outcome.status == "ok"
    assert len(outcome.jobs) == 4
    # First request start=0, second start=2 (adapted to actual parsed page size of 2)
    starts = []
    for call in mock_get.call_args_list:
        url = call.args[0] if call.args else call.kwargs.get("url", "")
        if "start=" in url:
            starts.append(url.split("start=")[1].split("&")[0])
    assert starts[0] == "0"
    assert starts[1] == "2"


def test_collect_linkedin_jobs_retries_on_429_with_backoff():
    ok = MagicMock(status_code=200, text=SAMPLE_LINKEDIN_HTML)
    limited = MagicMock(status_code=429, text="Too Many Requests")

    with patch("collect_jobs.requests.get", side_effect=[limited, ok]) as mock_get, patch(
        "collect_jobs.time.sleep"
    ) as mock_sleep, patch("collect_jobs.LINKEDIN_MAX_RETRIES", 3):
        outcome = collect_linkedin_jobs("Python Developer", max_pages=1)

    assert outcome.status == "ok"
    assert len(outcome.jobs) == 2
    assert mock_get.call_count == 2
    assert mock_sleep.called


def test_save_jobs_to_db_skips_known_urls_without_upsert():
    """Known job_url must be skipped before description persistence / upsert."""
    upsert_calls: list[str] = []

    def fake_upsert(**kwargs):
        upsert_calls.append(kwargs["job_url"])
        return 1, True

    known_url = "https://www.linkedin.com/jobs/view/999001"
    jobs = [
        {
            "title": "Already Seen",
            "company": "Acme",
            "location": "Israel",
            "job_url": known_url,
            "source": "linkedin",
            "description": "should not be written",
        },
        {
            "title": "Brand New",
            "company": "Beta",
            "location": "Israel",
            "job_url": "https://www.linkedin.com/jobs/view/999002",
            "source": "linkedin",
            "description": "ok",
        },
    ]
    with patch("collect_jobs.upsert_collected_job", side_effect=fake_upsert):
        raw, unique, _dup, already, _ex, inserted, _touched = save_jobs_to_db(
            jobs,
            source_query="Software Engineer",
            source_category="backend",
            source_strategy_hash="h",
            seen_job_keys=set(),
            known_db_keys=set(),
            touched_job_keys=set(),
            known_job_urls={known_url},
        )

    assert raw == 2
    assert unique == 2
    assert already == 1
    assert inserted == 1
    assert upsert_calls == ["https://www.linkedin.com/jobs/view/999002"]


def test_apply_collect_filters_skips_old_and_known():
    from collect_jobs import _apply_collect_filters
    from job_identity import normalize_job_url

    known = {normalize_job_url("https://www.drushim.co.il/job/1/")}
    jobs = [
        {
            "title": "Old",
            "job_url": "https://www.drushim.co.il/job/2/",
            "posted_date": "לפני חודשיים",
        },
        {
            "title": "Known",
            "job_url": "https://www.drushim.co.il/job/1/",
            "posted_date": "היום",
        },
        {
            "title": "Fresh",
            "job_url": "https://www.drushim.co.il/job/3/",
            "posted_date": "היום",
        },
    ]
    kept, age_skipped, known_skipped, all_old = _apply_collect_filters(
        jobs, known_job_urls=known
    )
    assert [j["title"] for j in kept] == ["Fresh"]
    assert age_skipped == 1
    assert known_skipped == 1
    assert all_old is False

    only_old = [
        {"title": "a", "job_url": "https://www.drushim.co.il/job/9/", "posted_date": "לפני שנה"},
        {"title": "b", "job_url": "https://www.drushim.co.il/job/8/", "posted_date": "לפני חודשיים"},
    ]
    kept2, age2, known2, all_old2 = _apply_collect_filters(only_old)
    assert kept2 == []
    assert age2 == 2
    assert known2 == 0
    assert all_old2 is True

