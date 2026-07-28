"""Regressions for two Postgres-only bugs found in production:

1. Every user's aggregated "workspace" scan shared the same literal
   ``owner_cv_id``/``cv_id`` ("workspace") in the shared Postgres schema, so
   resetting/reading one user's workspace results touched every user's data
   (or, combined with bug 2 below, silently touched nothing at all).
2. ``cv_tailor_versions.job_id`` has a Postgres foreign key to ``jobs`` with
   no ``ON DELETE CASCADE``. Deleting a CV/resetting a job pool after tailoring
   at least one CV raised an uncaught ForeignKeyViolation -> HTTP 500.

SQLite doesn't enforce foreign keys by default, so these tests use a real
sqlite connection with ``PRAGMA foreign_keys = ON`` (and a simulated
Postgres ``db_path`` redirect) to exercise the same failure modes without a
live Postgres instance.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import cv_service
import db
from conftest import insert_job


def test_workspace_scope_id_namespaces_per_user_only_under_postgres(monkeypatch):
    monkeypatch.setattr(db, "uses_postgres", lambda: False)
    assert db.workspace_scope_id("user-a") == db.WORKSPACE_CV_ID

    monkeypatch.setattr(db, "uses_postgres", lambda: True)
    assert db.workspace_scope_id("user-a") == f"{db.WORKSPACE_CV_ID}:user-a"
    assert db.workspace_scope_id("user-a") != db.workspace_scope_id("user-b")


def test_owner_cv_id_for_path_resolves_user_workspace_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "uses_postgres", lambda: True)
    monkeypatch.setattr(db, "USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(db, "CVS_DIR", tmp_path / "cvs")

    user_path = tmp_path / "users" / "user-a" / "jobs.db"
    assert db.owner_cv_id_for_path(user_path) == db.workspace_scope_id("user-a")

    cv_path = tmp_path / "cvs" / "cv-123" / "jobs.db"
    assert db.owner_cv_id_for_path(cv_path) == "cv-123"

    unrelated = tmp_path / "elsewhere" / "jobs.db"
    assert db.owner_cv_id_for_path(unrelated) == db.LEGACY_OWNER_CV_ID


def _enable_fk(conn) -> None:
    conn.execute("PRAGMA foreign_keys = ON")


@contextmanager
def _fk_enforced_connection(db_path: Path):
    """Real sqlite connection with FK enforcement on, mimicking Postgres."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _enable_fk(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def test_reset_cv_job_pool_does_not_violate_tailor_versions_fk(tmp_path, monkeypatch):
    """Regression: reset_cv_job_pool must delete cv_tailor_versions before jobs,
    or a Postgres-enforced FK raises and the reset request 500s."""
    # In real Postgres, cvs/jobs/cv_tailor_versions all live in one shared
    # schema, so use the full registry+jobs schema (not a bare jobs-only DB).
    db_path = tmp_path / "cv.db"
    db.init_registry_db(db_path)
    db.init_db(db_path)
    cv_id = "cv-1"
    job_id = insert_job(db_path, title="Dev", url="https://example.com/j1")
    with db.get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO cv_tailor_versions "
            "(cv_id, job_id, score_before, score_after, tailored_cv_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cv_id, job_id, 50, 80, "/tmp/x.md", "2026-01-01T00:00:00"),
        )
        conn.commit()

    monkeypatch.setattr(db, "get_connection", lambda p=db_path: _fk_enforced_connection(p))
    monkeypatch.setattr(db, "uses_postgres", lambda: True)

    # Must not raise sqlite3.IntegrityError (stand-in for Postgres ForeignKeyViolation).
    db.reset_cv_job_pool(cv_id, db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM cv_tailor_versions WHERE cv_id = ?", (cv_id,)
        ).fetchone()["n"]
        remaining_jobs = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE owner_cv_id = ?", (cv_id,)
        ).fetchone()["n"]
    assert remaining == 0
    assert remaining_jobs == 0


def test_delete_cv_does_not_violate_tailor_versions_fk(tmp_path, monkeypatch):
    """Regression: delete_cv must delete cv_tailor_versions before jobs."""
    db_path = tmp_path / "cv.db"
    db.init_registry_db(db_path)
    db.init_db(db_path)
    cv_id = "cv-2"

    # Enable FK enforcement + Postgres mode *before* inserting the job, so
    # insert_job tags it with owner_cv_id="cv-2" the same way real Postgres
    # per-CV collection does (via owner_cv_id_for_path).
    monkeypatch.setattr(db, "get_connection", lambda p=db_path: _fk_enforced_connection(p))
    monkeypatch.setattr(db, "uses_postgres", lambda: True)
    monkeypatch.setattr(db, "owner_cv_id_for_path", lambda _p: cv_id)

    job_id = insert_job(db_path, title="Dev2", url="https://example.com/j2")
    with db.get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO cv_tailor_versions "
            "(cv_id, job_id, score_before, score_after, tailored_cv_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cv_id, job_id, 50, 80, "/tmp/y.md", "2026-01-01T00:00:00"),
        )
        conn.commit()

    # Must not raise sqlite3.IntegrityError (stand-in for Postgres ForeignKeyViolation).
    summary = db.delete_cv(cv_id, db_path=db_path)
    assert summary["deleted_jobs"] == 1


def test_get_known_job_urls_scoped_by_owner_under_postgres(tmp_path, monkeypatch):
    """Regression: under Postgres (one shared jobs table), get_known_job_urls
    must not return URLs collected by a *different* CV/user — otherwise
    incremental collection early-breaks immediately and "can't find new jobs"
    on every board, since virtually every popular listing is already known
    globally after the very first scan by anyone.
    """
    shared_db = tmp_path / "shared.db"
    db.init_registry_db(shared_db)
    db.init_db(shared_db)

    monkeypatch.setattr(db, "uses_postgres", lambda: True)
    monkeypatch.setattr(
        db,
        "get_connection",
        lambda p=None: _fk_enforced_connection(shared_db),
    )
    monkeypatch.setattr(
        db,
        "_table_names",
        lambda conn: {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        },
    )
    monkeypatch.setattr(db, "owner_cv_id_for_path", lambda p: str(p))

    cv_a_path = tmp_path / "cv-a-logical.db"
    cv_b_path = tmp_path / "cv-b-logical.db"

    db.insert_job(
        title="Shared listing",
        job_url="https://example.com/popular-job",
        db_path=cv_a_path,
    )
    db.insert_job(
        title="Other CV's job",
        job_url="https://example.com/other-job",
        db_path=cv_b_path,
    )

    urls_a = db.get_known_job_urls(db_path=cv_a_path)
    urls_b = db.get_known_job_urls(db_path=cv_b_path)

    assert "https://example.com/popular-job" in urls_a
    assert "https://example.com/other-job" not in urls_a
    assert "https://example.com/other-job" in urls_b
    assert "https://example.com/popular-job" not in urls_b


def test_reset_user_results_scopes_by_user_under_simulated_postgres(
    tmp_path, monkeypatch
):
    """Two users' workspace matches must not collide (or be reset together)
    once Postgres ignores db_path and only cv_id/owner_cv_id disambiguate rows."""
    shared_db = tmp_path / "shared.db"
    db.init_registry_db(shared_db)
    db.init_db(shared_db)

    monkeypatch.setattr(db, "uses_postgres", lambda: True)

    @contextmanager
    def _redirect(_db_path=None, *a, **k):
        with _fk_enforced_connection(shared_db) as conn:
            yield conn

    monkeypatch.setattr(db, "get_connection", _redirect)
    monkeypatch.setattr(cv_service.db, "get_connection", _redirect)
    monkeypatch.setattr(cv_service.db, "uses_postgres", lambda: True)
    monkeypatch.setattr(
        cv_service, "user_db_path", lambda uid: tmp_path / "users" / uid / "jobs.db"
    )
    monkeypatch.setattr(cv_service.db, "list_cvs", lambda **kw: [])
    # The underlying connection is real sqlite (for FK enforcement), so table
    # introspection must use sqlite_master, not Postgres' pg_catalog.
    monkeypatch.setattr(
        db,
        "_table_names",
        lambda conn: {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        },
    )

    ws_a = db.workspace_scope_id("user-a")
    ws_b = db.workspace_scope_id("user-b")
    assert ws_a != ws_b

    job_a = db.insert_job(
        title="A-job", job_url="https://example.com/a", db_path=shared_db
    )
    job_b = db.insert_job(
        title="B-job", job_url="https://example.com/b", db_path=shared_db
    )
    scan_a = db.create_scan(ws_a, db_path=shared_db)
    scan_b = db.create_scan(ws_b, db_path=shared_db)
    db.upsert_cv_job_match(
        ws_a,
        job_a,
        {
            "match_score": 90,
            "match_reason": "ok",
            "match_method": "local",
            "candidate_strategy_hash": "h",
        },
        scan_id=scan_a,
        db_path=shared_db,
    )
    db.upsert_cv_job_match(
        ws_b,
        job_b,
        {
            "match_score": 70,
            "match_reason": "ok",
            "match_method": "local",
            "candidate_strategy_hash": "h",
        },
        scan_id=scan_b,
        db_path=shared_db,
    )

    assert len(db.get_cv_matches(ws_a, db_path=shared_db)) == 1
    assert len(db.get_cv_matches(ws_b, db_path=shared_db)) == 1

    summary = cv_service.reset_user_results("user-a")
    assert summary["cleared_workspace_db"] is True

    assert db.get_cv_matches(ws_a, db_path=shared_db) == []
    # User B's workspace results must survive user A's reset.
    assert len(db.get_cv_matches(ws_b, db_path=shared_db)) == 1
