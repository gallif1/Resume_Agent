"""Regression tests for Postgres/SQLite registry migrations (multi-user cvs)."""

from __future__ import annotations

import db


def test_pg_full_schema_does_not_create_cvs_index_before_migration():
    """Legacy Postgres cvs tables lack user_id — index must run only after ALTER."""
    assert "idx_cvs_user_active" not in db._PG_FULL_SCHEMA


def test_legacy_cvs_without_user_id_migrates(tmp_path):
    reg = tmp_path / "registry.db"
    with db.get_connection(reg) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                hashed_password TEXT,
                display_name TEXT,
                created_at TEXT,
                updated_at TEXT
            );
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
        conn.commit()

    db.init_registry_db(reg)
    with db.get_connection(reg) as conn:
        columns = db._table_columns(conn, "cvs")
    assert "user_id" in columns
    assert "is_active" in columns


def test_ensure_auth_schema_skips_cvs_migration(tmp_path):
    """Login/register must not require cvs.user_id to exist."""
    reg = tmp_path / "registry.db"
    with db.get_connection(reg) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                hashed_password TEXT,
                display_name TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE cvs (
                id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL
            );
            """
        )
        conn.commit()

    db.ensure_auth_schema(reg)
    with db.get_connection(reg) as conn:
        columns = db._table_columns(conn, "cvs")
    assert "user_id" not in columns

    user = db.create_user(
        "u1",
        email="auth@example.com",
        hashed_password="hash",
        db_path=reg,
    )
    assert user["email"] == "auth@example.com"
