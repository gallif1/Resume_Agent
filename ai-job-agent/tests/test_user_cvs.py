"""Tests for user-level multi-CV database helpers."""

from __future__ import annotations

import api_server
import cv_service
import db


def test_create_cv_assigns_default_user(db_path):
    cv = db.create_cv(
        "cv-1",
        file_name="a.pdf",
        stored_path="data/cvs/cv-1/resume.pdf",
        db_path=db_path,
    )
    assert cv["user_id"] == db.DEFAULT_USER_ID
    assert cv["is_active"] == 1


def test_list_active_cvs_for_user(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    db.create_cv("cv-b", file_name="b.pdf", stored_path="b", db_path=db_path)
    db.update_cv("cv-b", {"is_active": 0}, db_path=db_path)

    active = db.list_active_cvs_for_user(db_path=db_path)
    assert len(active) == 1
    assert active[0]["id"] == "cv-a"


def test_upload_multiple_cvs_same_user(db_path, cvs_dir):
    cv_a = cv_service.upload_cv("alice.pdf", b"alice-resume", db_path=db_path)
    cv_b = cv_service.upload_cv("bob.pdf", b"bob-resume", db_path=db_path)
    active = db.list_active_cvs_for_user(db_path=db_path)
    assert len(active) == 2
    assert {cv["id"] for cv in active} == {cv_a["id"], cv_b["id"]}


def test_legacy_registry_without_user_id_migrates(tmp_path):
    """Old registries (pre multi-user) must migrate without 500 on /cvs."""
    reg = tmp_path / "registry.db"
    with db.get_connection(reg) as conn:
        conn.executescript(
            """
            CREATE TABLE cvs (
                id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                display_name TEXT,
                stored_path TEXT,
                file_ext TEXT,
                file_size INTEGER,
                file_hash TEXT,
                parsed_profile TEXT,
                created_at TEXT,
                updated_at TEXT,
                last_scan_at TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO cvs (
                id, file_name, display_name, stored_path, file_ext, file_size,
                file_hash, parsed_profile, created_at, updated_at, last_scan_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                "legacy1",
                "old.pdf",
                "old.pdf",
                "data/cvs/legacy1/resume.pdf",
                ".pdf",
                12,
                "hash-legacy",
                "2020-01-01T00:00:00+00:00",
                "2020-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()

    db.init_registry_db(reg)
    rows = db.list_cvs(db_path=reg)
    assert len(rows) == 1
    assert rows[0]["user_id"] == db.DEFAULT_USER_ID
    assert rows[0]["is_active"] == 1
    active = db.list_active_cvs_for_user(db_path=reg)
    assert len(active) == 1



def test_list_cvs_does_not_auto_import_legacy_resume(monkeypatch):
    """Listing CVs must not import bundled resumes/cv.* into the registry."""
    adopt_calls: list[bool] = []
    monkeypatch.setattr(
        cv_service,
        "adopt_legacy_cv",
        lambda *args, **kwargs: adopt_calls.append(True),
    )
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)
    monkeypatch.setattr(api_server.db, "list_cvs", lambda **kwargs: [])
    monkeypatch.setattr(api_server, "_workspace_match_count", lambda: 0)
    monkeypatch.setattr(api_server.db, "list_active_cvs_for_user", lambda **kwargs: [])

    from fastapi.testclient import TestClient

    client = TestClient(api_server.app)
    response = client.get("/cvs")

    assert response.status_code == 200
    assert response.json()["cvs"] == []
    assert adopt_calls == []
