"""End-to-end tests for the multi-CV agent pipeline.

Covers the full flow from resume upload through every user-scan step
(parse → aggregate → analyze_roles → collect → enrich → match), plus the
regression for ``save_profile_for_cv`` being undefined during profile sync.
"""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import api_server
import config
import cv_service
import db
import pytest
from fastapi.testclient import TestClient


SAMPLE_CV_TEXT = """
Jane Doe
jane@example.com | Tel Aviv

Summary
Backend developer with Python, FastAPI, PostgreSQL, Docker and AWS.

Experience
Backend Developer @ Acme (2021 – Present)
- Built REST APIs with FastAPI and SQLAlchemy
- Operated PostgreSQL and Docker on AWS

Skills
Python, SQL, FastAPI, SQLAlchemy, WebSockets, REST API, PostgreSQL, SQLite, AWS, Docker

Education
BSc Computer Science, Tel Aviv University
"""


def _sample_cv_profile(**overrides: Any) -> dict[str, Any]:
    profile = {
        "contact": {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "",
            "location": "Tel Aviv",
            "linkedin": "",
            "github": "",
            "portfolio": "",
        },
        "skills": {
            "programming_languages": ["Python", "SQL"],
            "frameworks_libraries": ["FastAPI", "SQLAlchemy"],
            "databases": ["PostgreSQL", "SQLite"],
            "cloud_devops_tools": ["AWS", "Docker"],
        },
        "experience": {
            "job_titles": ["Backend Developer"],
            "companies": ["Acme"],
            "seniority_level": "mid",
            "years_of_experience": 4,
            "internship_or_student_experience": False,
        },
        "education": {
            "degrees": ["BSc Computer Science"],
            "institutions": ["Tel Aviv University"],
            "fields_of_study": ["Computer Science"],
        },
        "sections": {
            "summary": "Backend developer with Python and FastAPI.",
            "experience": "Backend Developer @ Acme",
            "skills": "Python, FastAPI, PostgreSQL, Docker, AWS",
            "education": "BSc Computer Science",
        },
        "best_fit_roles": ["Backend Developer", "Python Developer"],
        "raw_text": SAMPLE_CV_TEXT,
        "ai_insights": {"recommended_job_types": ["Backend Developer"]},
    }
    profile.update(overrides)
    return profile


@pytest.fixture
def pipeline_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path):
    """Isolate CV/user storage and the registry DB under tmp_path."""
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
    monkeypatch.setattr(api_server, "_workspace_match_count", lambda *a, **k: 0)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)
    monkeypatch.setattr(
        "scan_control.user_data_dir",
        lambda uid: users_dir / uid,
    )

    # Reset in-memory scan state between tests.
    with api_server._scan_lock:
        api_server._scan_state.update(
            {
                "running": False,
                "mode": None,
                "cv_id": None,
                "user_id": None,
                "scan_id": None,
                "started_at": None,
                "finished_at": None,
                "error": None,
                "warnings": [],
                "collection": None,
                "log": [],
                "current_detail": None,
                "steps": [],
            }
        )

    return {
        "db_path": db_path,
        "cvs_dir": cvs_dir,
        "users_dir": users_dir,
    }


def _write_parsed_profile(cv_id: str, profile: dict[str, Any] | None = None) -> Path:
    directory = config.cv_data_dir(cv_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "cv_profile.json"
    path.write_text(
        json.dumps(profile or _sample_cv_profile(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# --- Regression: save_profile_for_cv must be imported -----------------------


def test_sync_parsed_profile_saves_prefs_and_db(pipeline_env):
    """Regression for: name 'save_profile_for_cv' is not defined."""
    db_path = pipeline_env["db_path"]
    cv = cv_service.upload_cv("jane.pdf", b"jane-resume-bytes", db_path=db_path)
    _write_parsed_profile(cv["id"])

    # Must not raise NameError.
    cv_service.sync_parsed_profile(cv["id"], db_path=db_path)

    prefs_path = config.cv_profile_prefs_path(cv["id"])
    assert prefs_path.exists()
    prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert prefs["full_name"] == "Jane Doe"
    assert "Backend Developer" in prefs["target_roles"]

    stored = db.get_cv(cv["id"], db_path=db_path)
    assert stored is not None
    parsed = json.loads(stored["parsed_profile"])
    assert parsed["contact"]["name"] == "Jane Doe"


def test_save_profile_for_cv_is_exported_from_cv_service():
    """Ensure the helper used by sync_parsed_profile is importable in-module."""
    assert callable(getattr(cv_service, "save_profile_for_cv", None))


# --- Full user pipeline (upload → all steps) --------------------------------


def test_run_user_scan_completes_all_steps(pipeline_env, monkeypatch):
    db_path = pipeline_env["db_path"]
    cv_a = cv_service.upload_cv("a.pdf", b"resume-a-bytes", db_path=db_path)
    cv_b = cv_service.upload_cv("b.pdf", b"resume-b-bytes", db_path=db_path)

    step_calls: list[str] = []
    step_statuses: dict[str, str] = {}

    def fake_subprocess(args, *, env, log=None, on_line=None):
        script = Path(args[1]).name if len(args) > 1 else ""
        step_calls.append(script)
        if script == "parse_cv.py":
            cv_id = env.get("AGENT_CV_ID", "")
            assert cv_id, "parse_cv must run with AGENT_CV_ID"
            assert "AGENT_USER_ID" not in env
            _write_parsed_profile(cv_id)
            if log:
                log(f"Saved to: {config.cv_data_dir(cv_id) / 'cv_profile.json'}")
            return 0
        if script == "collect_jobs.py":
            summary = {
                "sites": {"linkedin": {"job_count": 2, "status": "ok"}},
                "total_jobs": 2,
            }
            line = f"COLLECT_SUMMARY:{json.dumps(summary)}"
            if on_line:
                on_line(line)
            if log:
                log(line)
            return 0
        if log:
            log(f"ok {script}")
        return 0

    monkeypatch.setattr(cv_service, "_run_logged_subprocess", fake_subprocess)
    monkeypatch.setattr(
        "universal_profile.is_ai_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "cv_aggregator_service.is_ai_available",
        lambda: False,
    )

    def set_step(key: str, status: str) -> None:
        step_statuses[key] = status

    logs: list[str] = []
    result = cv_service.run_user_scan(
        db.DEFAULT_USER_ID,
        skip_collect=False,
        skip_enrich=False,
        job_sites=["linkedin"],
        log=logs.append,
        set_step_status=set_step,
        db_path=db_path,
    )

    assert result["status"] == db.SCAN_SUCCESS
    assert result.get("error_message") in (None, "")
    assert result["cv_count"] == 2

    # Every pipeline stage must have succeeded.
    expected_keys = [key for key, *_ in cv_service.USER_SCAN_STEPS]
    assert set(step_statuses) == set(expected_keys)
    assert all(step_statuses[k] == "success" for k in expected_keys)

    # parse_cv once per uploaded file; then role/collect/enrich/match once.
    assert step_calls.count("parse_cv.py") == 2
    for script in (
        "analyze_roles.py",
        "collect_jobs.py",
        "enrich_jobs.py",
        "match_jobs.py",
    ):
        assert script in step_calls

    # Aggregated workspace artifacts written.
    user_id = db.DEFAULT_USER_ID
    assert config.user_cv_profile_path(user_id).exists()
    assert config.user_master_profile_path(user_id).exists()
    assert config.user_profile_prefs_path(user_id).exists()

    # Per-CV prefs written by sync_parsed_profile (the former NameError site).
    for cv in (cv_a, cv_b):
        assert config.cv_profile_prefs_path(cv["id"]).exists()
        stored = db.get_cv(cv["id"], db_path=db_path)
        assert stored and stored.get("parsed_profile")

    assert result.get("collection") is not None
    assert any("COLLECT_SUMMARY" in line or "אוחדו" in line for line in logs)


def test_run_user_scan_fails_cleanly_when_parse_subprocess_fails(
    pipeline_env, monkeypatch
):
    db_path = pipeline_env["db_path"]
    cv_service.upload_cv("a.pdf", b"resume-a", db_path=db_path)

    monkeypatch.setattr(
        cv_service,
        "_run_logged_subprocess",
        lambda *a, **k: 1,
    )

    steps: dict[str, str] = {}
    result = cv_service.run_user_scan(
        db.DEFAULT_USER_ID,
        set_step_status=lambda k, s: steps.__setitem__(k, s),
        db_path=db_path,
    )
    assert result["status"] == db.SCAN_FAILED
    assert steps.get("parse_cvs") == "failed"
    assert "ניתוח" in (result.get("error_message") or "")


def test_run_user_scan_skips_collect_and_enrich(pipeline_env, monkeypatch):
    db_path = pipeline_env["db_path"]
    cv_service.upload_cv("a.pdf", b"resume-skip", db_path=db_path)

    scripts: list[str] = []

    def fake_subprocess(args, *, env, log=None, on_line=None):
        script = Path(args[1]).name
        scripts.append(script)
        if script == "parse_cv.py":
            _write_parsed_profile(env["AGENT_CV_ID"])
        return 0

    monkeypatch.setattr(cv_service, "_run_logged_subprocess", fake_subprocess)
    monkeypatch.setattr("universal_profile.is_ai_available", lambda: False)
    monkeypatch.setattr("cv_aggregator_service.is_ai_available", lambda: False)

    steps: dict[str, str] = {}
    result = cv_service.run_user_scan(
        db.DEFAULT_USER_ID,
        skip_collect=True,
        skip_enrich=True,
        set_step_status=lambda k, s: steps.__setitem__(k, s),
        db_path=db_path,
    )
    assert result["status"] == db.SCAN_SUCCESS
    assert steps["collect"] == "skipped"
    assert steps["enrich"] == "skipped"
    assert "collect_jobs.py" not in scripts
    assert "enrich_jobs.py" not in scripts
    assert "match_jobs.py" in scripts


# --- Single-CV scan pipeline ------------------------------------------------


def test_run_scan_completes_all_steps(pipeline_env, monkeypatch):
    db_path = pipeline_env["db_path"]
    cv = cv_service.upload_cv("solo.pdf", b"solo-resume", db_path=db_path)

    class FakeProc:
        def __init__(self, script: str):
            self.script = script
            self.stdout = io.StringIO(f"ok {script}\n")
            self._code = 0

        def wait(self):
            return self._code

    scripts: list[str] = []

    def fake_popen(args, **kwargs):
        script = Path(args[1]).name
        scripts.append(script)
        if script == "parse_cv.py":
            _write_parsed_profile(cv["id"])
        return FakeProc(script)

    monkeypatch.setattr(cv_service.subprocess, "Popen", fake_popen)

    steps: dict[str, str] = {}
    result = cv_service.run_scan(
        cv["id"],
        set_step_status=lambda k, s: steps.__setitem__(k, s),
        db_path=db_path,
    )
    assert result["status"] == db.SCAN_SUCCESS
    assert all(
        steps[key] == "success" for key, *_ in cv_service.SCAN_STEPS
    )
    assert scripts == [
        "parse_cv.py",
        "analyze_roles.py",
        "collect_jobs.py",
        "enrich_jobs.py",
        "match_jobs.py",
    ]
    assert config.cv_profile_prefs_path(cv["id"]).exists()


# --- API: upload → start agent → status -------------------------------------


def _isolate_api_registry(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    """Force API handlers to use the temp registry (defaults are import-time)."""
    orig_upload = cv_service.upload_cv
    orig_list_cvs = db.list_cvs
    orig_list_active = db.list_active_cvs_for_user
    orig_get_cv = db.get_cv

    def _upload(filename, data, **kwargs):
        kwargs.pop("db_path", None)
        return orig_upload(filename, data, db_path=db_path, **kwargs)

    def _list_cvs(**kwargs):
        kwargs.pop("db_path", None)
        return orig_list_cvs(db_path=db_path, **kwargs)

    def _list_active(user_id=db.DEFAULT_USER_ID, **kwargs):
        kwargs.pop("db_path", None)
        return orig_list_active(user_id, db_path=db_path, **kwargs)

    def _get_cv(cv_id, **kwargs):
        kwargs.pop("db_path", None)
        return orig_get_cv(cv_id, db_path=db_path, **kwargs)

    monkeypatch.setattr(api_server.cv_service, "upload_cv", _upload)
    monkeypatch.setattr(api_server.db, "list_cvs", _list_cvs)
    monkeypatch.setattr(api_server.db, "list_active_cvs_for_user", _list_active)
    monkeypatch.setattr(api_server.db, "get_cv", _get_cv)


def test_api_upload_and_run_user_pipeline(pipeline_env, monkeypatch):
    """HTTP flow matching the web UI: upload CVs, start matcher, poll status."""
    db_path = pipeline_env["db_path"]
    _isolate_api_registry(monkeypatch, db_path)
    completed = threading.Event()

    def fake_run_user_scan(
        user_id,
        *,
        skip_collect=False,
        skip_enrich=False,
        job_sites=None,
        log=None,
        set_step_status=None,
        db_path=None,
    ):
        registry = db_path or pipeline_env["db_path"]
        for key, name, _, _ in cv_service.USER_SCAN_STEPS:
            if set_step_status:
                set_step_status(key, "running")
            if log:
                log(f">> {name}")
            if key == "parse_cvs":
                # Exercise the real sync path that previously crashed.
                for cv in db.list_active_cvs_for_user(user_id, db_path=registry):
                    _write_parsed_profile(cv["id"])
                    cv_service.sync_parsed_profile(cv["id"], db_path=registry)
            if set_step_status:
                set_step_status(key, "success")
        completed.set()
        return {
            "id": 1,
            "status": db.SCAN_SUCCESS,
            "cv_count": 2,
            "user_id": user_id,
            "warnings": [],
            "collection": {"total_jobs": 0},
        }

    # api_server imports the cv_service module object — patch there.
    monkeypatch.setattr(api_server.cv_service, "run_user_scan", fake_run_user_scan)
    monkeypatch.setattr(api_server, "_persist_scan_state", lambda: None)
    monkeypatch.setattr(api_server, "begin_scan", lambda: None)
    monkeypatch.setattr(api_server, "is_cancelled", lambda: False)

    from conftest import authed_client

    with authed_client() as client:
        for name, content in (("one.pdf", b"file-one"), ("two.pdf", b"file-two")):
            response = client.post(
                "/cvs/upload",
                files={"file": (name, content, "application/pdf")},
            )
            assert response.status_code == 200, response.text
            assert response.json()["cv"]["id"]

        listed = client.get("/cvs")
        assert listed.status_code == 200
        assert listed.json()["active_cv_count"] == 2

        started = client.post("/jobs/match", json={"skip_collect": False, "skip_enrich": False})
        assert started.status_code == 200
        assert started.json()["started"] is True
        assert started.json()["cv_count"] == 2

        assert completed.wait(timeout=5)

        # Allow the background thread to flip running=False.
        deadline = time.time() + 5
        status_payload: dict[str, Any] = {}
        while time.time() < deadline:
            status = client.get("/jobs/match-status")
            assert status.status_code == 200
            status_payload = status.json()
            if not status_payload.get("running"):
                break
            time.sleep(0.05)

    assert status_payload.get("running") is False
    assert status_payload.get("error") in (None, "")
    steps = {s["key"]: s["status"] for s in status_payload.get("steps") or []}
    for key, *_ in cv_service.USER_SCAN_STEPS:
        assert steps.get(key) == "success", steps

    # Profile prefs exist after sync_parsed_profile during the API run.
    active = db.list_active_cvs_for_user(db_path=db_path)
    assert len(active) == 2
    for cv in active:
        assert config.cv_profile_prefs_path(cv["id"]).exists()


def test_api_rejects_match_without_uploads(pipeline_env, monkeypatch):
    _isolate_api_registry(monkeypatch, pipeline_env["db_path"])
    monkeypatch.setattr(api_server, "_persist_scan_state", lambda: None)
    from conftest import authed_client

    with authed_client() as client:
        response = client.post("/jobs/match", json={})
    assert response.status_code == 400
    assert "להעלות" in response.json()["detail"]
