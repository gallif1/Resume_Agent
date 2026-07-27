"""Regression: Postgres match list must not require a local jobs.db file."""

from __future__ import annotations

from contextlib import contextmanager

import db
from conftest import insert_job


def test_sqlite_path_missing_false_when_postgres(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "uses_postgres", lambda: True)
    assert db._sqlite_path_missing(tmp_path / "cvs" / "x" / "jobs.db") is False


def test_sqlite_path_missing_true_when_sqlite_file_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "uses_postgres", lambda: False)
    assert db._sqlite_path_missing(tmp_path / "nope.db") is True


def test_get_cv_matches_works_without_sqlite_file_when_path_check_skipped(tmp_path, monkeypatch):
    """UI showed match_count from PG COUNT while GET matches returned [] because
    get_cv_matches bailed when data/cvs/<id>/jobs.db was missing."""
    real_db = tmp_path / "shared.db"
    db.init_db(real_db)
    missing_logical = tmp_path / "cvs" / "cv-pg" / "jobs.db"
    assert not missing_logical.exists()

    job_id = insert_job(real_db, title="Backend", url="https://example.com/j/1")
    scan_id = db.create_scan("cv-pg", db_path=real_db)
    db.upsert_cv_job_match(
        "cv-pg",
        job_id,
        {
            "match_score": 88,
            "match_reason": "strong",
            "match_method": "test",
            "ai_explanation": "fit",
        },
        scan_id=scan_id,
        db_path=real_db,
    )

    # Simulate Postgres: logical path has no file, but shared DB still has rows.
    monkeypatch.setattr(db, "_sqlite_path_missing", lambda _p: False)
    real_get = db.get_connection

    @contextmanager
    def _redirect(_db_path=None, *a, **k):
        with real_get(real_db) as conn:
            yield conn

    monkeypatch.setattr(db, "get_connection", _redirect)

    rows = db.get_cv_matches("cv-pg", latest_only=True, db_path=missing_logical)
    assert len(rows) == 1
    assert rows[0]["match_score"] == 88
    assert rows[0]["title"] == "Backend"


def test_sqlite_still_returns_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "uses_postgres", lambda: False)
    missing = tmp_path / "nope.db"
    assert db.get_cv_matches("cv-x", db_path=missing) == []
