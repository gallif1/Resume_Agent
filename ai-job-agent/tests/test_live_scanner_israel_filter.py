"""Tests for Live Job Scanner Israel market filter."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from live_scanner.collectors.base import CollectedJob
from live_scanner.israel_filter import (
    classify_israel_job,
    filter_israel_jobs,
    is_israel_job,
)


def _job(**kwargs) -> CollectedJob:
    defaults = {
        "external_job_id": "1",
        "title": "Engineer",
        "company": "Acme",
        "job_url": "https://example.com/1",
        "location": "",
    }
    defaults.update(kwargs)
    return CollectedJob(**defaults)


def test_accept_israel_locations():
    accept = [
        _job(location="Tel Aviv, Israel"),
        _job(location="Haifa, Israel"),
        _job(location="Jerusalem, Israel"),
        _job(location="Herzliya, Israel"),
        _job(location="Yokneam, Israel"),
        _job(location="Israel"),
        _job(location="Remote - Israel"),
        _job(location="Israel - Remote"),
        _job(location="תל אביב"),
        _job(location="חיפה"),
        _job(location="ירושלים"),
        _job(location="הרצליה, ישראל"),
        _job(country_code="IL", location="Remote"),
        _job(country="Israel", location=""),
        _job(raw={"location": {"city": "Petah Tikva", "country": "il", "remote": False}}),
    ]
    for job in accept:
        assert is_israel_job(job), job.location or job.country_code or job.raw


def test_reject_foreign_and_bare_remote():
    reject = [
        _job(location="New York, USA"),
        _job(location="London, UK"),
        _job(location="Berlin, Germany"),
        _job(location="Bangalore, India"),
        _job(location="Remote - US"),
        _job(location="Remote - Europe"),
        _job(location="Remote"),
        _job(location="Worldwide Remote"),
        _job(country_code="US", location="Remote"),
        _job(country="Germany", location="Berlin"),
        _job(location=""),
    ]
    for job in reject:
        decision = classify_israel_job(job)
        assert not decision.accepted, (job.location, decision)


def test_description_israel_mention_is_not_enough():
    job = _job(
        location="New York, NY",
        country_code="US",
        description="We have an office in Israel but this role is US-based.",
    )
    assert not is_israel_job(job)


def test_filter_mixed_board():
    jobs = [
        _job(external_job_id="us", location="Austin, TX, United States", country_code="US"),
        _job(external_job_id="il1", location="Tel Aviv, Israel", country_code="IL"),
        _job(external_job_id="de", location="Berlin, Germany", country_code="DE"),
        _job(external_job_id="il2", location="Remote - Israel"),
        _job(external_job_id="remote", location="Remote"),
    ]
    kept, fetched, foreign = filter_israel_jobs(jobs)
    assert fetched == 5
    assert foreign == 3
    assert {j.external_job_id for j in kept} == {"il1", "il2"}
