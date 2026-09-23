"""Collector tests for new Live Job Scanner providers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from live_scanner.collectors.comeet import ComeetCollector
from live_scanner.collectors.recruitee import RecruiteeCollector
from live_scanner.collectors.smartrecruiters import SmartRecruitersCollector
from live_scanner.collectors.teamtailor import TeamtailorCollector
from live_scanner.collectors.workable import WorkableCollector
from live_scanner.collectors import get_collector, list_providers
from live_scanner.israel_filter import filter_israel_jobs


def test_providers_registered():
    ids = {p["id"] for p in list_providers()}
    for name in (
        "lever",
        "greenhouse",
        "ashby",
        "workday",
        "comeet",
        "smartrecruiters",
        "workable",
        "teamtailor",
        "recruitee",
    ):
        assert name in ids
        assert get_collector(name) is not None


def test_smartrecruiters_parses_and_keeps_israel_only_via_filter():
    sample = {
        "totalFound": 2,
        "content": [
            {
                "id": "us1",
                "name": "US Role",
                "location": {
                    "city": "Austin",
                    "country": "us",
                    "fullLocation": "Austin, TX, United States",
                    "remote": False,
                },
                "company": {"name": "Acme"},
            },
            {
                "id": "il1",
                "name": "Israel Role",
                "location": {
                    "city": "Tel Aviv",
                    "country": "il",
                    "fullLocation": "Tel Aviv, , Israel",
                    "remote": False,
                },
                "company": {"name": "Acme"},
            },
        ],
    }
    with patch(
        "live_scanner.collectors.smartrecruiters.http_json",
        return_value=(sample, 200),
    ):
        result = SmartRecruitersCollector().collect(board_identifier="acme")
    assert result.status == "ok"
    assert len(result.jobs) == 2
    kept, fetched, foreign = filter_israel_jobs(result.jobs)
    assert fetched == 2 and foreign == 1
    assert kept[0].external_job_id == "il1"
    assert kept[0].country_code.lower() in {"il", "israel"} or "israel" in kept[0].location.lower()


def test_workable_collector():
    sample = {
        "name": "Crayon",
        "jobs": [
            {
                "title": "AE",
                "shortcode": "ABC123",
                "url": "https://apply.workable.com/crayon/j/ABC123",
                "city": "Boston",
                "country": "United States",
                "location": "Boston",
                "telecommuting": False,
            },
            {
                "title": "IL Dev",
                "shortcode": "IL999",
                "url": "https://apply.workable.com/crayon/j/IL999",
                "city": "Tel Aviv",
                "country": "Israel",
                "location": "Tel Aviv",
                "telecommuting": False,
            },
        ],
    }
    with patch(
        "live_scanner.collectors.workable.http_json",
        return_value=(sample, 200),
    ):
        result = WorkableCollector().collect(board_identifier="crayon")
    assert result.status == "ok"
    kept, _, foreign = filter_israel_jobs(result.jobs)
    assert foreign == 1
    assert len(kept) == 1 and kept[0].external_job_id == "IL999"


def test_teamtailor_json_feed():
    sample = {
        "title": "Demo Co",
        "items": [
            {
                "id": "a",
                "title": "UK Role",
                "url": "https://career.example.com/jobs/a",
                "location": None,
                "date_published": "2026-01-01T00:00:00Z",
                "_jobposting": {
                    "jobLocation": {
                        "@type": "Place",
                        "address": {
                            "addressLocality": "London",
                            "addressCountry": "GB",
                        },
                    }
                },
            },
            {
                "id": "b",
                "title": "TLV Role",
                "url": "https://career.example.com/jobs/b",
                "location": None,
                "date_published": "2026-01-02T00:00:00Z",
                "_jobposting": {
                    "jobLocation": [
                        {
                            "@type": "Place",
                            "address": {
                                "addressLocality": "Berlin",
                                "addressCountry": "DE",
                            },
                        },
                        {
                            "@type": "Place",
                            "address": {
                                "addressLocality": "Tel Aviv",
                                "addressCountry": "IL",
                            },
                        },
                    ]
                },
            },
        ],
    }
    with patch(
        "live_scanner.collectors.teamtailor.http_json",
        return_value=(sample, 200),
    ):
        result = TeamtailorCollector().collect(board_identifier="career.example.com")
    assert result.status == "ok"
    kept, _, _ = filter_israel_jobs(result.jobs)
    assert [j.external_job_id for j in kept] == ["b"]
    assert kept[0].country_code.upper() == "IL"


def test_recruitee_collector():
    sample = {
        "offers": [
            {
                "id": 1,
                "title": "Berlin Eng",
                "country_code": "DE",
                "country": "Germany",
                "city": "Berlin",
                "location": "Berlin, Germany",
                "careers_url": "https://demo.recruitee.com/o/1",
            },
            {
                "id": 2,
                "title": "TLV Eng",
                "country_code": "IL",
                "country": "Israel",
                "city": "Tel Aviv",
                "location": "Tel Aviv, Israel",
                "careers_url": "https://demo.recruitee.com/o/2",
            },
        ]
    }
    with patch(
        "live_scanner.collectors.recruitee.http_json",
        return_value=(sample, 200),
    ):
        result = RecruiteeCollector().collect(board_identifier="demo")
    assert result.status == "ok"
    kept, fetched, foreign = filter_israel_jobs(result.jobs)
    assert fetched == 2 and foreign == 1 and kept[0].external_job_id == "2"


def test_comeet_requires_board_url():
    result = ComeetCollector().collect(board_identifier="")
    assert result.status == "error"
    assert "careers" in (result.error or "").lower() or "comeet" in (result.error or "").lower()


def test_comeet_resolves_public_page_like_other_ats():
    html = (
        '<script>var COMPANY_DATA = {"name": "WeSki", "company_uid": "F8.00C", '
        '"token": "PUBLICTOKEN123", "slug": "weski"};</script>'
    )
    sample = [
        {
            "uid": "il1",
            "name": "Backend",
            "company_name": "WeSki",
            "url_comeet_hosted_page": "https://www.comeet.com/jobs/weski/F8.00C/backend/il1",
            "location": {
                "name": "Herzliya, Israel",
                "city": "Herzliya",
                "country": "IL",
            },
        },
        {
            "uid": "uk1",
            "name": "UK Role",
            "company_name": "WeSki",
            "url_comeet_hosted_page": "https://www.comeet.com/jobs/weski/F8.00C/uk/uk1",
            "location": {"name": "London", "city": "London", "country": "GB"},
        },
    ]
    with (
        patch(
            "live_scanner.collectors.comeet.http_text",
            return_value=(html, 200),
        ),
        patch(
            "live_scanner.collectors.comeet.http_json",
            return_value=(sample, 200),
        ) as mock_json,
    ):
        result = ComeetCollector().collect(board_identifier="weski/F8.00C")
    assert result.status == "ok"
    assert mock_json.call_args.args[0].endswith(
        "company/F8.00C/positions?token=PUBLICTOKEN123&details=true"
    )
    kept, fetched, foreign = filter_israel_jobs(result.jobs)
    assert fetched == 2 and foreign == 1 and kept[0].external_job_id == "il1"
