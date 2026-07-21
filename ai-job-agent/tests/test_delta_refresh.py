"""Tests for delta refresh: watermark identity + early-break helpers + API."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import api_server
import config
import cv_service
import db
import pytest
from collect_jobs import _apply_collect_filters, save_jobs_to_db
from conftest import authed_client, register_test_user
from job_identity import (
    compute_job_identity_key,
    job_matches_delta_identity,
    normalize_job_url,
    trim_jobs_before_delta_stop,
)


@pytest.fixture
def delta_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path):
    cvs_dir = tmp_path / "cvs"
    users_dir = tmp_path / "users"
    cvs_dir.mkdir()
    users_dir.mkdir()

    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    monkeypatch.setattr(config, "USERS_DIR", users_dir)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)
    monkeypatch.setattr(api_server, "_persist_scan_state", lambda: None)

    orig_upload = cv_service.upload_cv
    orig_get_cv = db.get_cv

    def _upload(filename, data, **kwargs):
        kwargs.pop("db_path", None)
        return orig_upload(filename, data, db_path=db_path, **kwargs)

    def _get_cv(cv_id, **kwargs):
        kwargs.pop("db_path", None)
        return orig_get_cv(cv_id, db_path=db_path, **kwargs)

    monkeypatch.setattr(api_server.cv_service, "upload_cv", _upload)
    monkeypatch.setattr(api_server.db, "get_cv", _get_cv)
    monkeypatch.setattr(cv_service.db, "get_cv", _get_cv)

    with api_server._scan_lock:
        api_server._scan_state.update(
            {
                "running": False,
                "mode": None,
                "cv_id": None,
                "user_id": None,
                "steps": [],
                "log": [],
                "warnings": [],
                "error": None,
                "finished_at": None,
            }
        )

    db.init_registry_db(db_path)
    if db.get_user_by_id(db.DEFAULT_USER_ID, db_path=db_path) is None:
        register_test_user(email="default@local", db_path=db_path)

    return {
        "db_path": db_path,
        "cvs_dir": cvs_dir,
        "authed_client": authed_client,
    }


def test_trim_jobs_before_delta_stop_keeps_newer_only():
    watermark_url = "https://www.drushim.co.il/job/100/"
    identity = {
        "job_url": normalize_job_url(watermark_url),
        "job_hash": compute_job_identity_key(watermark_url, "Old", "Acme", "TLV"),
        "identity_key": compute_job_identity_key(watermark_url, "Old", "Acme", "TLV"),
    }
    jobs = [
        {"title": "New", "job_url": "https://www.drushim.co.il/job/200/", "company": "A"},
        {
            "title": "Old",
            "job_url": watermark_url,
            "company": "Acme",
            "location": "TLV",
        },
        {"title": "Older", "job_url": "https://www.drushim.co.il/job/50/", "company": "B"},
    ]
    kept, hit = trim_jobs_before_delta_stop(jobs, identity)
    assert hit is True
    assert [j["title"] for j in kept] == ["New"]
    assert job_matches_delta_identity(jobs[1], identity)


def test_apply_collect_filters_delta_early_break():
    watermark = "https://www.drushim.co.il/job/100/"
    identity = {
        "job_url": normalize_job_url(watermark),
        "job_hash": "x",
        "identity_key": "x",
    }
    page = [
        {
            "title": "Fresh",
            "job_url": "https://www.drushim.co.il/job/200/",
            "posted_date": "היום",
        },
        {
            "title": "Watermark",
            "job_url": watermark,
            "posted_date": "היום",
        },
        {
            "title": "Stale",
            "job_url": "https://www.drushim.co.il/job/10/",
            "posted_date": "היום",
        },
    ]
    kept, _age, _known, _all_old, hit = _apply_collect_filters(
        page, delta_stop_identity=identity, apply_age_filter=False
    )
    assert hit is True
    assert [j["title"] for j in kept] == ["Fresh"]


def test_save_jobs_to_db_stops_at_watermark(monkeypatch: pytest.MonkeyPatch):
    watermark_url = normalize_job_url("https://www.linkedin.com/jobs/view/111")
    identity = {
        "job_url": watermark_url,
        "job_hash": "hash-known",
        "identity_key": "hash-known",
    }
    inserted_urls: list[str] = []

    def fake_upsert(**kwargs):
        url = normalize_job_url(kwargs["job_url"])
        inserted_urls.append(url)
        return len(inserted_urls), True

    monkeypatch.setattr("collect_jobs.upsert_collected_job", fake_upsert)

    scraped = [
        {
            "title": "Brand New",
            "job_url": "https://www.linkedin.com/jobs/view/222",
            "company": "Beta",
            "location": "TLV",
            "source": "linkedin",
            "posted_date": "היום",
        },
        {
            "title": "Known",
            "job_url": watermark_url,
            "company": "Acme",
            "location": "TLV",
            "source": "linkedin",
            "posted_date": "היום",
        },
        {
            "title": "Should Skip",
            "job_url": "https://www.linkedin.com/jobs/view/50",
            "company": "Gamma",
            "location": "TLV",
            "source": "linkedin",
            "posted_date": "היום",
        },
    ]
    (
        _raw,
        _unique,
        _dup,
        _already,
        _excl,
        inserted,
        _touched,
        hit_delta,
    ) = save_jobs_to_db(
        scraped,
        source_query="Fullstack",
        source_category="fullstack",
        source_strategy_hash=None,
        seen_job_keys=set(),
        known_db_keys=set(),
        touched_job_keys=set(),
        known_job_urls=set(),
        delta_stop_identity=identity,
    )
    assert hit_delta is True
    assert inserted == 1
    assert inserted_urls == [
        normalize_job_url("https://www.linkedin.com/jobs/view/222")
    ]


def test_get_latest_known_job_identity_prefers_category_and_source(tmp_path: Path):
    jobs_db = tmp_path / "jobs.db"
    db.init_db(jobs_db)
    db.insert_job(
        title="Older",
        job_url="https://www.drushim.co.il/job/1/",
        company="A",
        source="drushim",
        source_category="backend",
        db_path=jobs_db,
    )
    db.insert_job(
        title="Newer",
        job_url="https://www.drushim.co.il/job/2/",
        company="B",
        source="drushim",
        source_category="fullstack",
        db_path=jobs_db,
    )
    identity = db.get_latest_known_job_identity(
        jobs_db, source_category="fullstack", source="drushim"
    )
    assert identity is not None
    assert "job/2" in identity["job_url"]


def test_get_last_scan_criteria_reads_summary(delta_env):
    db_path = delta_env["db_path"]
    cv = db.create_cv(
        "cv-delta-1",
        file_name="cv.txt",
        stored_path="cv.txt",
        file_hash="abc",
        display_name="CV",
        db_path=db_path,
    )
    cv_db = cv_service.cv_db_path(cv["id"])
    db.init_db(cv_db)
    scan_id = db.create_scan(cv["id"], db_path=cv_db)
    summary = json.dumps(
        {
            "matches": 3,
            "domains": ["Fullstack Developer", "Backend Developer"],
            "job_sites": ["drushim", "linkedin"],
        },
        ensure_ascii=False,
    )
    db.finish_scan(scan_id, db.SCAN_SUCCESS, summary=summary, db_path=cv_db)
    db.set_cv_last_scan(cv["id"], db_path=db_path)

    criteria = cv_service.get_last_scan_criteria(cv["id"], db_path=db_path)
    assert criteria is not None
    assert criteria["domains"] == ["Fullstack Developer", "Backend Developer"]
    assert criteria["job_sites"] == ["drushim", "linkedin"]


def test_refresh_endpoint_reuses_last_criteria(delta_env, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_search(
        cv_id_arg: str,
        *,
        domains: list[str],
        skip_enrich: bool = False,
        job_sites: list[str] | None = None,
        delta: bool = False,
        **_kwargs: Any,
    ):
        captured.update(
            {
                "cv_id": cv_id_arg,
                "domains": domains,
                "job_sites": job_sites,
                "delta": delta,
                "skip_enrich": skip_enrich,
            }
        )
        return {
            "status": db.SCAN_SUCCESS,
            "warnings": [],
            "collection": None,
            "id": 1,
        }

    monkeypatch.setattr(api_server.cv_service, "run_search", fake_search)
    # Also patch criteria lookup to use the test registry path.
    orig_criteria = cv_service.get_last_scan_criteria

    def _criteria(cv_id: str, **kwargs):
        kwargs.pop("db_path", None)
        return orig_criteria(cv_id, db_path=delta_env["db_path"], **kwargs)

    monkeypatch.setattr(api_server.cv_service, "get_last_scan_criteria", _criteria)

    with delta_env["authed_client"]() as client:
        upload = client.post(
            "/cvs/upload",
            files={
                "file": ("resume.txt", b"Jane Doe\nPython developer", "text/plain")
            },
        )
        assert upload.status_code == 200, upload.text
        cv_id = upload.json()["cv"]["id"]

        missing = client.post(f"/cvs/{cv_id}/refresh")
        assert missing.status_code == 400

        cv_db = cv_service.cv_db_path(cv_id)
        db.init_db(cv_db)
        scan_id = db.create_scan(cv_id, db_path=cv_db)
        db.finish_scan(
            scan_id,
            db.SCAN_SUCCESS,
            summary=json.dumps(
                {
                    "domains": ["Python Developer"],
                    "job_sites": ["drushim"],
                    "matches": 1,
                },
                ensure_ascii=False,
            ),
            db_path=cv_db,
        )
        db.set_cv_last_scan(cv_id, db_path=delta_env["db_path"])

        with api_server._scan_lock:
            api_server._scan_state.update(
                {
                    "running": False,
                    "mode": None,
                    "cv_id": None,
                    "user_id": None,
                    "steps": [],
                    "log": [],
                    "warnings": [],
                    "error": None,
                    "finished_at": None,
                }
            )

        res = client.post(f"/cvs/{cv_id}/refresh")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["started"] is True
        assert body["delta"] is True
        assert body["domains"] == ["Python Developer"]

        for _ in range(50):
            if captured:
                break
            time.sleep(0.05)
        assert captured.get("delta") is True
        assert captured.get("domains") == ["Python Developer"]
        assert captured.get("job_sites") == ["drushim"]
