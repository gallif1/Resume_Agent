"""Tests for delete/re-upload and workspace reset (results + files)."""

from __future__ import annotations

import json
from pathlib import Path

import api_server
import config
import cv_service
import db
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def reset_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path):
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
    monkeypatch.setattr(api_server, "_workspace_match_count", lambda *a, **k: 0)
    monkeypatch.setattr(
        "scan_control.user_data_dir",
        lambda uid: users_dir / uid,
    )
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
    return {"db_path": db_path, "cvs_dir": cvs_dir, "users_dir": users_dir}


def _isolate_api(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    orig_upload = cv_service.upload_cv
    orig_list_cvs = db.list_cvs
    orig_list_active = db.list_active_cvs_for_user
    orig_get_cv = db.get_cv
    orig_delete = cv_service.delete_cv
    orig_reset_results = cv_service.reset_user_results
    orig_reset_files = cv_service.reset_user_files

    monkeypatch.setattr(
        api_server.cv_service,
        "upload_cv",
        lambda filename, data, **kw: orig_upload(
            filename, data, db_path=db_path, **{k: v for k, v in kw.items() if k != "db_path"}
        ),
    )
    monkeypatch.setattr(
        api_server.db,
        "list_cvs",
        lambda **kw: orig_list_cvs(
            db_path=db_path, **{k: v for k, v in kw.items() if k != "db_path"}
        ),
    )
    monkeypatch.setattr(
        api_server.db,
        "list_active_cvs_for_user",
        lambda user_id=db.DEFAULT_USER_ID, **kw: orig_list_active(
            user_id, db_path=db_path, **{k: v for k, v in kw.items() if k != "db_path"}
        ),
    )
    monkeypatch.setattr(
        api_server.db,
        "get_cv",
        lambda cv_id, **kw: orig_get_cv(
            cv_id, db_path=db_path, **{k: v for k, v in kw.items() if k != "db_path"}
        ),
    )
    monkeypatch.setattr(
        api_server.cv_service,
        "delete_cv",
        lambda cv_id, **kw: orig_delete(
            cv_id, db_path=db_path, **{k: v for k, v in kw.items() if k != "db_path"}
        ),
    )
    monkeypatch.setattr(
        api_server.cv_service,
        "reset_user_results",
        lambda user_id=db.DEFAULT_USER_ID, **kw: orig_reset_results(
            user_id, db_path=db_path, **{k: v for k, v in kw.items() if k != "db_path"}
        ),
    )
    monkeypatch.setattr(
        api_server.cv_service,
        "reset_user_files",
        lambda user_id=db.DEFAULT_USER_ID, **kw: orig_reset_files(
            user_id, db_path=db_path, **{k: v for k, v in kw.items() if k != "db_path"}
        ),
    )


def test_delete_cv_without_jobs_tables_allows_reupload(reset_env):
    """Regression: delete must succeed even when resolved DB has no jobs tables."""
    db_path = reset_env["db_path"]
    # Registry-only row (no per-CV jobs.db) — previously raised OperationalError.
    with db.get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO cvs (
                id, user_id, file_name, stored_path, file_hash,
                created_at, updated_at, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                "orphan-cv",
                db.DEFAULT_USER_ID,
                "cv.pdf",
                "missing",
                cv_service.compute_file_hash(b"same-bytes"),
                "t",
                "t",
            ),
        )
        conn.commit()

    summary = cv_service.delete_cv("orphan-cv", db_path=db_path)
    assert summary["cv_id"] == "orphan-cv"
    assert db.get_cv("orphan-cv", db_path=db_path) is None

    # Same file content can be uploaded again.
    uploaded = cv_service.upload_cv("cv.pdf", b"same-bytes", db_path=db_path)
    assert uploaded["id"] != "orphan-cv"
    assert db.get_cv(uploaded["id"], db_path=db_path) is not None


def test_delete_then_reupload_same_file(reset_env):
    db_path = reset_env["db_path"]
    first = cv_service.upload_cv("resume.pdf", b"unique-resume-body", db_path=db_path)
    cv_service.delete_cv(first["id"], db_path=db_path)
    second = cv_service.upload_cv("resume.pdf", b"unique-resume-body", db_path=db_path)
    assert second["id"] != first["id"]
    assert not (reset_env["cvs_dir"] / first["id"]).exists()
    assert (reset_env["cvs_dir"] / second["id"] / "resume.pdf").exists()


def test_reset_user_results_keeps_files_clears_matches(reset_env, monkeypatch):
    db_path = reset_env["db_path"]
    users_dir = reset_env["users_dir"]
    cv = cv_service.upload_cv("a.pdf", b"keep-me", db_path=db_path)

    user_id = db.DEFAULT_USER_ID
    user_db = config.user_db_path(user_id)
    db.init_db(user_db)
    scan_id = db.create_scan(db.WORKSPACE_CV_ID, db_path=user_db)
    job_id = db.insert_job(
        title="Dev",
        job_url="https://example.com/j1",
        company="Acme",
        db_path=user_db,
    )
    db.upsert_cv_job_match(
        db.WORKSPACE_CV_ID,
        job_id,
        {
            "match_score": 80,
            "match_reason": "ok",
            "match_method": "local",
            "match_category": "backend",
            "matched_skills": "[]",
            "missing_skills": "[]",
            "candidate_strategy_hash": "h",
        },
        scan_id=scan_id,
        db_path=user_db,
    )
    db.set_cv_last_scan(cv["id"], db_path=db_path)

    state_path = users_dir / user_id / "scan_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"running": False, "error": "old"}), encoding="utf-8")

    summary = cv_service.reset_user_results(user_id, db_path=db_path)
    assert summary["reset"] == "results"
    assert db.get_cv(cv["id"], db_path=db_path) is not None
    assert (reset_env["cvs_dir"] / cv["id"] / "resume.pdf").exists()
    assert db.get_cv_matches(db.WORKSPACE_CV_ID, db_path=user_db) == []
    assert db.get_latest_scan(db.WORKSPACE_CV_ID, db_path=user_db) is None
    assert db.get_cv(cv["id"], db_path=db_path)["last_scan_at"] is None
    assert not state_path.exists()


def test_reset_user_files_removes_cvs_and_workspace(reset_env):
    db_path = reset_env["db_path"]
    user_id = db.DEFAULT_USER_ID
    cv_a = cv_service.upload_cv("a.pdf", b"file-a", db_path=db_path)
    cv_b = cv_service.upload_cv("b.pdf", b"file-b", db_path=db_path)

    workspace = config.user_data_dir(user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "master_profile.json").write_text("{}", encoding="utf-8")
    (workspace / "cv_profile.json").write_text("{}", encoding="utf-8")

    summary = cv_service.reset_user_files(user_id, db_path=db_path)
    assert summary["deleted_count"] == 2
    assert set(summary["deleted_cv_ids"]) == {cv_a["id"], cv_b["id"]}
    assert db.list_cvs(db_path=db_path) == []
    assert not (reset_env["cvs_dir"] / cv_a["id"]).exists()
    assert not (workspace / "master_profile.json").exists()
    assert not (workspace / "cv_profile.json").exists()


def test_api_reset_results_and_files(reset_env, monkeypatch):
    db_path = reset_env["db_path"]
    _isolate_api(monkeypatch, db_path)
    monkeypatch.setattr(api_server, "_persist_scan_state", lambda: None)

    from conftest import authed_client

    with authed_client() as client:
        up = client.post(
            "/cvs/upload",
            files={"file": ("a.pdf", b"api-file", "application/pdf")},
        )
        assert up.status_code == 200

        res = client.post("/jobs/matches/reset")
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert res.json()["reset"] == "results"
        assert len(db.list_cvs(db_path=db_path)) == 1

        files = client.post("/cvs/reset")
        assert files.status_code == 200
        body = files.json()
        assert body["ok"] is True
        assert body["deleted_count"] == 1
        assert db.list_cvs(db_path=db_path) == []


def test_api_reset_blocked_while_scan_running(reset_env, monkeypatch):
    _isolate_api(monkeypatch, reset_env["db_path"])
    with api_server._scan_lock:
        api_server._scan_state.update(
            {
                "running": True,
                "mode": "user",
                "user_id": db.DEFAULT_USER_ID,
            }
        )
    from conftest import authed_client

    try:
        with authed_client() as client:
            assert client.post("/jobs/matches/reset").status_code == 409
            assert client.post("/cvs/reset").status_code == 409
    finally:
        with api_server._scan_lock:
            api_server._scan_state["running"] = False
