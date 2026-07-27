"""Jobs migration / COALESCE type-safety for Postgres."""

from __future__ import annotations

import db


def test_coalesce_seen_at_casts_created_at():
    expr = db._coalesce_seen_at_expr("first_seen_at", "collected_at", "created_at")
    assert "CAST(created_at AS TEXT)" in expr
    assert "first_seen_at" in expr
    assert "collected_at" in expr


def test_coalesce_seen_at_with_table_prefix():
    expr = db._coalesce_seen_at_expr("j.first_seen_at", "j.collected_at", "j.created_at")
    assert "CAST(j.created_at AS TEXT)" in expr
    assert "CAST(j.first_seen_at" not in expr


def test_match_sort_date_casts_created_at():
    assert "CAST(j.created_at AS TEXT)" in db._MATCH_SORT_COLUMNS["date"]


def test_jobs_migration_backfills_posted_date_on_sqlite(tmp_path):
    path = tmp_path / "jobs.db"
    db.init_db(path)
    with db.get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (title, company, location, job_url, collected_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "Engineer",
                "Acme",
                "TLV",
                "https://example.com/job/1",
                "2026-07-20T12:00:00+00:00",
                "2026-07-19 10:00:00",
            ),
        )
        conn.commit()

    db.ensure_jobs_schema(path)
    with db.get_connection(path) as conn:
        row = conn.execute("SELECT posted_date, first_seen_at FROM jobs").fetchone()
    assert row["posted_date"] == "2026-07-20"
    assert row["first_seen_at"] == "2026-07-20T12:00:00+00:00"
