"""Live Job Scanner — collectors, baseline vs live, controls."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import db
from live_scanner.collectors.ashby import AshbyCollector
from live_scanner.collectors.greenhouse import GreenhouseCollector
from live_scanner.collectors.lever import LeverCollector
from live_scanner.collectors.workday import WorkdayCollector
from live_scanner.collectors.comeet import ComeetCollector
from live_scanner.collectors.base import CollectedJob, CollectResult
from live_scanner.scheduler import UserScannerWorker
from live_scanner.service import LiveScannerService
from live_scanner import store


@pytest.fixture
def user_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    users = tmp_path / "users"
    users.mkdir()
    monkeypatch.setattr("config.USERS_DIR", users)
    monkeypatch.setattr("live_scanner.store.user_db_path", lambda uid: users / uid / "jobs.db")
    # Also patch config.user_db_path used by pipeline
    import config

    monkeypatch.setattr(config, "USERS_DIR", users)
    user_id = "user-live-1"
    path = users / user_id / "jobs.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    db.init_db(path)
    store.ensure_live_scanner_schema(path)
    store.ensure_job_live_columns(path)
    return user_id, path


def test_lever_collector_parses_postings():
    sample = [
        {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "text": "Backend Engineer",
            "hostedUrl": "https://jobs.lever.co/leverdemo/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "categories": {"location": "Remote"},
            "createdAt": 1700000000000,
        }
    ]
    with patch(
        "live_scanner.collectors.lever.http_json",
        return_value=(sample, 200),
    ):
        result = LeverCollector().collect(board_identifier="leverdemo")
    assert result.status == "ok"
    assert len(result.jobs) == 1
    assert result.jobs[0].external_job_id.startswith("aaaa")
    assert "Backend" in result.jobs[0].title


def test_greenhouse_collector_parses_jobs():
    sample = {
        "jobs": [
            {
                "id": 12345,
                "title": "Staff SWE",
                "absolute_url": "https://boards.greenhouse.io/airbnb/jobs/12345",
                "location": {"name": "SF"},
            }
        ]
    }
    with patch(
        "live_scanner.collectors.greenhouse.http_json",
        return_value=(sample, 200),
    ):
        result = GreenhouseCollector().collect(board_identifier="airbnb")
    assert result.status == "ok"
    assert result.jobs[0].external_job_id == "12345"


def test_ashby_and_workday_collectors():
    ashby = {"jobs": [{"id": "11111111-1111-1111-1111-111111111111", "title": "PM", "jobUrl": "https://jobs.ashbyhq.com/ashby/11111111-1111-1111-1111-111111111111", "isListed": True}]}
    with patch("live_scanner.collectors.ashby.http_json", return_value=(ashby, 200)):
        assert AshbyCollector().collect(board_identifier="ashby").status == "ok"

    wd = {
        "total": 1,
        "jobPostings": [
            {
                "title": "CUDA Engineer",
                "externalPath": "/job/CUDA-Engineer_JR12345",
                "locationsText": "Santa Clara",
            }
        ],
    }
    with patch("live_scanner.collectors.workday.http_json", return_value=(wd, 200)):
        result = WorkdayCollector().collect(
            board_identifier="nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
        )
    assert result.status == "ok"
    assert result.jobs[0].external_job_id.endswith("JR12345")


def test_comeet_requires_token():
    result = ComeetCollector().collect(board_identifier="")
    assert result.status == "unsupported"
    assert "token" in (result.error or "").lower()


def test_baseline_then_live_new_job(user_db):
    user_id, path = user_db
    source = store.add_source(
        user_id,
        company_name="DemoCo",
        provider="lever",
        board_identifier="leverdemo",
        careers_url="https://jobs.lever.co/leverdemo",
        db_path=path,
    )

    baseline_jobs = [
        CollectedJob(
            external_job_id="job-1",
            title="Engineer 1",
            company="DemoCo",
            job_url="https://jobs.lever.co/leverdemo/job-1",
            location="Tel Aviv, Israel",
            country_code="IL",
        ),
        CollectedJob(
            external_job_id="job-2",
            title="Engineer 2",
            company="DemoCo",
            job_url="https://jobs.lever.co/leverdemo/job-2",
            location="Haifa, Israel",
            country_code="IL",
        ),
    ]
    live_jobs = baseline_jobs + [
        CollectedJob(
            external_job_id="job-3",
            title="Engineer 3 NEW",
            company="DemoCo",
            job_url="https://jobs.lever.co/leverdemo/job-3",
            location="Herzliya, Israel",
            country_code="IL",
        ),
        CollectedJob(
            external_job_id="job-us",
            title="US Only",
            company="DemoCo",
            job_url="https://jobs.lever.co/leverdemo/job-us",
            location="New York, USA",
            country_code="US",
        ),
    ]

    worker = UserScannerWorker(user_id)
    calls = {"n": 0}

    def fake_collect(**kwargs):
        calls["n"] += 1
        jobs = baseline_jobs if calls["n"] == 1 else live_jobs
        return CollectResult(jobs=list(jobs), status="ok", http_status=200)

    with patch("live_scanner.scheduler.get_collector") as gc:
        collector = type("C", (), {"collect": staticmethod(fake_collect)})()
        gc.return_value = collector

        # First scan = baseline
        worker._scan_source(source)
        source_after = store.get_source(user_id, source["id"], db_path=path)
        assert source_after["baseline_created_at"]
        assert source_after["status"] == "LIVE"
        state = store.get_state(user_id, db_path=path)
        assert state["new_jobs"] == 0
        assert state["jobs_checked"] == 2
        assert store.count_baseline_jobs(user_id, db_path=path) >= 2

        session_new = store.list_session_jobs(user_id, new_only=True, db_path=path)
        assert session_new == []

        # Second scan = live discovery
        source_live = store.get_source(user_id, source["id"], db_path=path)
        worker._scan_source(source_live)
        state2 = store.get_state(user_id, db_path=path)
        assert state2["new_jobs"] == 1
        session_new = store.list_session_jobs(user_id, new_only=True, db_path=path)
        assert len(session_new) == 1
        assert "NEW" in (session_new[0].get("title") or "")


def test_clear_preserves_baseline(user_db):
    user_id, path = user_db
    source = store.add_source(
        user_id,
        company_name="DemoCo",
        provider="lever",
        board_identifier="leverdemo",
        db_path=path,
    )
    store.upsert_known_job(
        user_id,
        source["id"],
        external_job_id="x1",
        canonical_url="https://jobs.lever.co/leverdemo/x1",
        job_id=None,
        is_baseline=True,
        db_path=path,
    )
    store.save_state(
        user_id,
        {
            "status": "RUNNING",
            "session_id": "abc",
            "jobs_checked": 100,
            "new_jobs": 5,
        },
        db_path=path,
    )
    store.add_activity(user_id, "noise", db_path=path)
    store.add_session_job(
        user_id,
        job_id=1,
        is_baseline=False,
        title="t",
        company="c",
        location="",
        provider="lever",
        source_id=source["id"],
        job_url="https://example.com/1",
        db_path=path,
    )

    svc = LiveScannerService(user_id)
    # Point service workspace to our temp db via monkeypatched user_db_path already
    svc.clear()

    assert store.count_baseline_jobs(user_id, db_path=path) == 1
    assert store.get_known_external_ids(user_id, source["id"], db_path=path) == {"x1"}
    state = store.get_state(user_id, db_path=path)
    assert state["jobs_checked"] == 0
    assert state["new_jobs"] == 0
    assert store.list_activity(user_id, db_path=path)  # clear adds a retention note
    # Session jobs wiped then maybe activity only
    assert store.list_session_jobs(user_id, new_only=False, db_path=path) == []


def test_play_twice_single_worker(user_db, monkeypatch):
    user_id, path = user_db
    # Avoid real network seeding collectors
    monkeypatch.setattr(
        "live_scanner.store.SEED_SOURCES",
        [
            {
                "company_name": "Demo",
                "provider": "lever",
                "board_identifier": "leverdemo",
                "careers_url": "https://jobs.lever.co/leverdemo",
                "enabled": True,
            }
        ],
    )

    worker = UserScannerWorker(user_id)

    def no_scan(self, source):
        return None

    monkeypatch.setattr(UserScannerWorker, "_scan_source", no_scan)
    s1 = worker.play()
    s2 = worker.play()
    assert worker.is_alive
    assert s1.get("session_id") == s2.get("session_id")
    worker.stop()


def test_pause_play_does_not_rebuild_baseline(user_db):
    user_id, path = user_db
    source = store.add_source(
        user_id,
        company_name="DemoCo",
        provider="lever",
        board_identifier="leverdemo",
        db_path=path,
    )
    store.update_source_scan_result(
        user_id,
        source["id"],
        status="LIVE",
        success=True,
        baseline_created_at="2026-01-01T00:00:00+00:00",
        baseline_job_count=10,
        db_path=path,
    )
    store.upsert_known_job(
        user_id,
        source["id"],
        external_job_id="keep",
        canonical_url="https://jobs.lever.co/leverdemo/keep",
        job_id=None,
        is_baseline=True,
        db_path=path,
    )
    before = store.get_source(user_id, source["id"], db_path=path)["baseline_created_at"]

    worker = UserScannerWorker(user_id)
    with patch.object(UserScannerWorker, "_scan_source", lambda self, s: None):
        worker.play()
        worker.pause()
        worker.play()
        worker.stop()

    after = store.get_source(user_id, source["id"], db_path=path)
    assert after["baseline_created_at"] == before
    assert store.get_known_external_ids(user_id, source["id"], db_path=path) == {"keep"}


def test_missing_job_not_immediately_closed(user_db):
    user_id, path = user_db
    source = store.add_source(
        user_id,
        company_name="DemoCo",
        provider="lever",
        board_identifier="leverdemo",
        db_path=path,
    )
    store.upsert_known_job(
        user_id,
        source["id"],
        external_job_id="gone",
        canonical_url="https://jobs.lever.co/leverdemo/gone",
        job_id=None,
        is_baseline=True,
        db_path=path,
    )
    # One successful scan missing the job → still active (count=1 < threshold 2)
    result = store.mark_missing_known_jobs(user_id, source["id"], set(), db_path=path)
    assert result["possibly_removed"] == 0
    with db.get_connection(path) as conn:
        row = conn.execute(
            "SELECT missing_scan_count, lifecycle_status FROM live_scanner_known_jobs WHERE external_job_id = ?",
            ("gone",),
        ).fetchone()
    assert row["missing_scan_count"] == 1
    assert row["lifecycle_status"] == "active"


def test_new_source_gets_own_baseline(user_db):
    user_id, path = user_db
    a = store.add_source(
        user_id, company_name="A", provider="lever", board_identifier="a", db_path=path
    )
    store.update_source_scan_result(
        user_id,
        a["id"],
        status="LIVE",
        success=True,
        baseline_created_at="2026-01-01T00:00:00+00:00",
        baseline_job_count=5,
        db_path=path,
    )
    b = store.add_source(
        user_id, company_name="B", provider="greenhouse", board_identifier="b", db_path=path
    )
    assert b["status"] == "NOT_INITIALIZED"
    assert b["baseline_created_at"] is None
    assert store.get_source(user_id, a["id"], db_path=path)["baseline_created_at"]


def test_job_identity_ats_keys():
    from job_identity import compute_job_identity_key

    assert compute_job_identity_key(
        "https://jobs.lever.co/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ).startswith("lever:job:")
    assert compute_job_identity_key(
        "https://boards.greenhouse.io/airbnb/jobs/999"
    ) == "greenhouse:job:999"


def test_provider_failure_does_not_stop_other_sources(user_db):
    user_id, path = user_db
    ok_source = store.add_source(
        user_id,
        company_name="OkCo",
        provider="lever",
        board_identifier="ok",
        db_path=path,
    )
    bad_source = store.add_source(
        user_id,
        company_name="BadCo",
        provider="greenhouse",
        board_identifier="bad",
        db_path=path,
    )
    worker = UserScannerWorker(user_id)

    def collect_for(board_identifier, **_kwargs):
        if board_identifier == "bad":
            return CollectResult(status="error", error="boom")
        return CollectResult(
            jobs=[
                CollectedJob(
                    external_job_id="il-1",
                    title="TLV Eng",
                    company="OkCo",
                    job_url="https://jobs.lever.co/ok/il-1",
                    location="Tel Aviv, Israel",
                    country_code="IL",
                )
            ],
            status="ok",
            http_status=200,
        )

    with patch("live_scanner.scheduler.get_collector") as gc:
        class FakeCollector:
            def collect(self, *, board_identifier: str, careers_url: str | None = None):
                return collect_for(board_identifier)

        gc.return_value = FakeCollector()
        worker._scan_source(bad_source)
        worker._scan_source(ok_source)

    bad_after = store.get_source(user_id, bad_source["id"], db_path=path)
    ok_after = store.get_source(user_id, ok_source["id"], db_path=path)
    assert bad_after["status"] == "ERROR"
    assert ok_after["status"] == "LIVE"
    assert ok_after["baseline_created_at"]
    state = store.get_state(user_id, db_path=path)
    assert state["new_jobs"] == 0
    assert int(state.get("israel_jobs") or 0) == 1


def test_foreign_jobs_never_reach_ai_matching(user_db):
    user_id, path = user_db
    source = store.add_source(
        user_id,
        company_name="GlobalCo",
        provider="lever",
        board_identifier="global",
        db_path=path,
    )
    # Establish Israel baseline first
    baseline = [
        CollectedJob(
            external_job_id="il-base",
            title="IL Base",
            company="GlobalCo",
            job_url="https://jobs.lever.co/global/il-base",
            location="Tel Aviv, Israel",
            country_code="IL",
        )
    ]
    live = baseline + [
        CollectedJob(
            external_job_id="us-new",
            title="US New Should Not Match",
            company="GlobalCo",
            job_url="https://jobs.lever.co/global/us-new",
            location="New York, USA",
            country_code="US",
        ),
        CollectedJob(
            external_job_id="il-new",
            title="IL New",
            company="GlobalCo",
            job_url="https://jobs.lever.co/global/il-new",
            location="Haifa, Israel",
            country_code="IL",
        ),
    ]
    worker = UserScannerWorker(user_id)
    calls = {"n": 0}

    def fake_collect(**_kwargs):
        calls["n"] += 1
        jobs = baseline if calls["n"] == 1 else live
        return CollectResult(jobs=list(jobs), status="ok", http_status=200)

    with (
        patch("live_scanner.scheduler.get_collector") as gc,
        patch("live_scanner.pipeline._try_match_job") as match_mock,
    ):
        match_mock.return_value = (50, False)
        collector = type("C", (), {"collect": staticmethod(fake_collect)})()
        gc.return_value = collector
        worker._scan_source(source)
        source_live = store.get_source(user_id, source["id"], db_path=path)
        worker._scan_source(source_live)

    # Matching runs only for the new Israeli job (baseline uses run_match=False).
    assert match_mock.call_count == 1
    matched_job_id = match_mock.call_args.args[1]
    # Foreign US job must not be in the jobs table as a live discovery
    with db.get_connection(path) as conn:
        rows = conn.execute(
            "SELECT title, location FROM jobs WHERE title LIKE ?",
            ("%US New%",),
        ).fetchall()
    assert rows == []
    state = store.get_state(user_id, db_path=path)
    assert state["new_jobs"] == 1
    assert int(state.get("foreign_filtered") or 0) >= 1
    assert matched_job_id is not None
