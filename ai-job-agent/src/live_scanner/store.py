"""Persistence for Live Job Scanner sources, known jobs, state, and activity.

Tables live in the user's workspace jobs DB (same file as existing jobs).
CLEAR only wipes session display data — never baseline / known IDs / jobs.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import db
from config import user_db_path
from live_scanner.constants import (
    ACTIVITY_LIMIT_DEFAULT,
    DISCOVERED_BY,
    LIFECYCLE_ACTIVE,
    SESSION_JOBS_LIMIT_DEFAULT,
    SOURCE_DISABLED,
    SOURCE_NOT_INITIALIZED,
    STATUS_STOPPED,
)
from live_scanner.defaults import SEED_SOURCES, default_interval_for

logger = logging.getLogger("live_scanner.store")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def workspace_db(user_id: str) -> Path:
    path = user_db_path(user_id)
    db.ensure_jobs_schema(path)
    ensure_live_scanner_schema(path)
    ensure_job_live_columns(path)
    return path


def ensure_job_live_columns(db_path: Path) -> None:
    """Additive columns on jobs for live-scanner metadata (idempotent)."""
    columns = [
        ("discovered_by", "TEXT"),
        ("is_baseline", "INTEGER DEFAULT 0"),
        ("baseline_created_at", "TEXT"),
        ("external_job_id", "TEXT"),
        ("provider", "TEXT"),
        ("live_source_id", "TEXT"),
        ("job_lifecycle_status", "TEXT"),
    ]
    with db.get_connection(db_path) as conn:
        for name, col_type in columns:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {col_type}")
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                if not db._is_operational_error(exc):
                    raise
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_scanner_sources (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    board_identifier TEXT NOT NULL DEFAULT '',
    careers_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    scan_interval_seconds INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'NOT_INITIALIZED',
    last_scan_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    baseline_created_at TEXT,
    baseline_job_count INTEGER DEFAULT 0,
    notes TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_sources_user
    ON live_scanner_sources (user_id);

CREATE TABLE IF NOT EXISTS live_scanner_known_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    external_job_id TEXT NOT NULL,
    canonical_url TEXT,
    job_id INTEGER,
    is_baseline INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    missing_scan_count INTEGER NOT NULL DEFAULT 0,
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    UNIQUE (source_id, external_job_id)
);

CREATE INDEX IF NOT EXISTS idx_live_known_source
    ON live_scanner_known_jobs (source_id);
CREATE INDEX IF NOT EXISTS idx_live_known_user
    ON live_scanner_known_jobs (user_id);

CREATE TABLE IF NOT EXISTS live_scanner_state (
    user_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'STOPPED',
    session_id TEXT,
    session_started_at TEXT,
    last_scan_at TEXT,
    next_scan_at TEXT,
    jobs_checked INTEGER NOT NULL DEFAULT 0,
    new_jobs INTEGER NOT NULL DEFAULT 0,
    duplicates_skipped INTEGER NOT NULL DEFAULT 0,
    relevant_jobs INTEGER NOT NULL DEFAULT 0,
    baseline_jobs INTEGER NOT NULL DEFAULT 0,
    jobs_fetched INTEGER NOT NULL DEFAULT 0,
    foreign_filtered INTEGER NOT NULL DEFAULT 0,
    israel_jobs INTEGER NOT NULL DEFAULT 0,
    sources_monitored INTEGER NOT NULL DEFAULT 0,
    sources_initialized INTEGER NOT NULL DEFAULT 0,
    sources_failed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS live_scanner_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    source_id TEXT,
    job_id INTEGER,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_activity_user
    ON live_scanner_activity (user_id, id DESC);

CREATE TABLE IF NOT EXISTS live_scanner_session_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    discovered_at TEXT NOT NULL,
    is_baseline INTEGER NOT NULL DEFAULT 0,
    title TEXT,
    company TEXT,
    location TEXT,
    provider TEXT,
    source_id TEXT,
    job_url TEXT,
    match_score INTEGER
);

CREATE INDEX IF NOT EXISTS idx_live_session_jobs_user
    ON live_scanner_session_jobs (user_id, id DESC);
"""


def ensure_live_scanner_schema(db_path: Path) -> None:
    with db.get_connection(db_path) as conn:
        if db.uses_postgres():
            # Adapt AUTOINCREMENT → IDENTITY for Postgres
            schema = _SCHEMA.replace(
                "INTEGER PRIMARY KEY AUTOINCREMENT",
                "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            )
            for stmt in schema.split(";"):
                s = stmt.strip()
                if s:
                    try:
                        conn.execute(s)
                        conn.commit()
                    except Exception as exc:  # noqa: BLE001
                        if not db._is_operational_error(exc):
                            raise
                        try:
                            conn.rollback()
                        except Exception:  # noqa: BLE001
                            pass
        else:
            conn.executescript(_SCHEMA)
            conn.commit()

    # Additive columns for older DBs
    for column, col_type in (
        ("jobs_fetched", "INTEGER NOT NULL DEFAULT 0"),
        ("foreign_filtered", "INTEGER NOT NULL DEFAULT 0"),
        ("israel_jobs", "INTEGER NOT NULL DEFAULT 0"),
        ("is_demo", "INTEGER NOT NULL DEFAULT 0"),
    ):
        table = "live_scanner_sources" if column == "is_demo" else "live_scanner_state"
        try:
            with db.get_connection(db_path) as conn:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            if not db._is_operational_error(exc):
                raise
            try:
                with db.get_connection(db_path) as conn:
                    conn.rollback()
            except Exception:  # noqa: BLE001
                pass


def seed_default_sources(user_id: str, db_path: Path | None = None) -> int:
    """Insert seed sources if the user has none yet. Returns inserted count."""
    path = db_path or workspace_db(user_id)
    existing = list_sources(user_id, db_path=path)
    if existing:
        return 0
    inserted = 0
    for spec in SEED_SOURCES:
        provider = str(spec["provider"])
        board = str(spec.get("board_identifier") or "")
        # Skip Comeet seed without credentials
        if provider == "comeet" and not board:
            continue
        add_source(
            user_id,
            company_name=str(spec["company_name"]),
            provider=provider,
            board_identifier=board,
            careers_url=str(spec.get("careers_url") or "") or None,
            enabled=bool(spec.get("enabled", True)),
            notes=str(spec.get("notes") or "") or None,
            is_demo=bool(spec.get("is_demo", False)),
            db_path=path,
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def list_sources(user_id: str, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM live_scanner_sources
            WHERE user_id = ?
            ORDER BY lower(company_name) ASC
            """,
            (user_id,),
        ).fetchall()
        return [_row(r) for r in rows]


def get_source(
    user_id: str, source_id: str, *, db_path: Path | None = None
) -> dict[str, Any] | None:
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        row = conn.execute(
            "SELECT * FROM live_scanner_sources WHERE user_id = ? AND id = ?",
            (user_id, source_id),
        ).fetchone()
        return _row(row) if row else None


def add_source(
    user_id: str,
    *,
    company_name: str,
    provider: str,
    board_identifier: str,
    careers_url: str | None = None,
    enabled: bool = True,
    scan_interval_seconds: int | None = None,
    notes: str | None = None,
    is_demo: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    path = db_path or workspace_db(user_id)
    now = _utc_now()
    source_id = uuid.uuid4().hex
    interval = scan_interval_seconds or default_interval_for(provider)
    status = SOURCE_NOT_INITIALIZED if enabled else SOURCE_DISABLED
    with db.get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO live_scanner_sources (
                id, user_id, company_name, provider, board_identifier, careers_url,
                enabled, scan_interval_seconds, status, notes, is_demo, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                user_id,
                company_name.strip(),
                provider.strip().lower(),
                (board_identifier or "").strip(),
                (careers_url or "").strip() or None,
                1 if enabled else 0,
                int(interval),
                status,
                notes,
                1 if is_demo else 0,
                now,
                now,
            ),
        )
        conn.commit()
    return get_source(user_id, source_id, db_path=path)  # type: ignore[return-value]


def update_source(
    user_id: str,
    source_id: str,
    *,
    company_name: str | None = None,
    board_identifier: str | None = None,
    careers_url: str | None = None,
    enabled: bool | None = None,
    scan_interval_seconds: int | None = None,
    notes: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    path = db_path or workspace_db(user_id)
    current = get_source(user_id, source_id, db_path=path)
    if not current:
        return None
    fields: dict[str, Any] = {"updated_at": _utc_now()}
    if company_name is not None:
        fields["company_name"] = company_name.strip()
    if board_identifier is not None:
        fields["board_identifier"] = board_identifier.strip()
    if careers_url is not None:
        fields["careers_url"] = careers_url.strip() or None
    if scan_interval_seconds is not None:
        fields["scan_interval_seconds"] = max(60, int(scan_interval_seconds))
    if notes is not None:
        fields["notes"] = notes
    if enabled is not None:
        fields["enabled"] = 1 if enabled else 0
        if not enabled:
            fields["status"] = SOURCE_DISABLED
        elif current.get("status") == SOURCE_DISABLED:
            # Re-enable: restore LIVE if baseline exists, else NOT_INITIALIZED
            if current.get("baseline_created_at"):
                fields["status"] = "LIVE"
            else:
                fields["status"] = SOURCE_NOT_INITIALIZED
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id, source_id]
    with db.get_connection(path) as conn:
        conn.execute(
            f"UPDATE live_scanner_sources SET {assignments} WHERE user_id = ? AND id = ?",
            values,
        )
        conn.commit()
    return get_source(user_id, source_id, db_path=path)


def delete_source(user_id: str, source_id: str, *, db_path: Path | None = None) -> bool:
    """Delete a source and its known-job index. Does NOT delete jobs from jobs table."""
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        cur = conn.execute(
            "DELETE FROM live_scanner_sources WHERE user_id = ? AND id = ?",
            (user_id, source_id),
        )
        conn.execute(
            "DELETE FROM live_scanner_known_jobs WHERE user_id = ? AND source_id = ?",
            (user_id, source_id),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0


def update_source_scan_result(
    user_id: str,
    source_id: str,
    *,
    status: str,
    last_error: str | None = None,
    success: bool = False,
    baseline_created_at: str | None = None,
    baseline_job_count: int | None = None,
    db_path: Path | None = None,
) -> None:
    path = db_path or workspace_db(user_id)
    now = _utc_now()
    fields: dict[str, Any] = {
        "status": status,
        "last_scan_at": now,
        "updated_at": now,
        "last_error": last_error,
    }
    if success:
        fields["last_success_at"] = now
    if baseline_created_at is not None:
        fields["baseline_created_at"] = baseline_created_at
    if baseline_job_count is not None:
        fields["baseline_job_count"] = int(baseline_job_count)
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id, source_id]
    with db.get_connection(path) as conn:
        conn.execute(
            f"UPDATE live_scanner_sources SET {assignments} WHERE user_id = ? AND id = ?",
            values,
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Known jobs (baseline / dedupe index)
# ---------------------------------------------------------------------------


def get_known_external_ids(
    user_id: str, source_id: str, *, db_path: Path | None = None
) -> set[str]:
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT external_job_id FROM live_scanner_known_jobs
            WHERE user_id = ? AND source_id = ?
            """,
            (user_id, source_id),
        ).fetchall()
        return {str(r["external_job_id"]) for r in rows}


def upsert_known_job(
    user_id: str,
    source_id: str,
    *,
    external_job_id: str,
    canonical_url: str | None,
    job_id: int | None,
    is_baseline: bool,
    db_path: Path | None = None,
) -> None:
    path = db_path or workspace_db(user_id)
    now = _utc_now()
    with db.get_connection(path) as conn:
        existing = conn.execute(
            """
            SELECT id FROM live_scanner_known_jobs
            WHERE source_id = ? AND external_job_id = ?
            """,
            (source_id, external_job_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE live_scanner_known_jobs
                SET last_seen_at = ?, missing_scan_count = 0,
                    lifecycle_status = ?,
                    canonical_url = COALESCE(?, canonical_url),
                    job_id = COALESCE(?, job_id)
                WHERE id = ?
                """,
                (
                    now,
                    LIFECYCLE_ACTIVE,
                    canonical_url,
                    job_id,
                    existing["id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO live_scanner_known_jobs (
                    user_id, source_id, external_job_id, canonical_url, job_id,
                    is_baseline, first_seen_at, last_seen_at, missing_scan_count,
                    lifecycle_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    user_id,
                    source_id,
                    external_job_id,
                    canonical_url,
                    job_id,
                    1 if is_baseline else 0,
                    now,
                    now,
                    LIFECYCLE_ACTIVE,
                ),
            )
        conn.commit()


def mark_missing_known_jobs(
    user_id: str,
    source_id: str,
    present_external_ids: set[str],
    *,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Increment missing_scan_count for IDs not seen in this successful scan.

    Conservative: does not delete jobs from the main jobs table.
    """
    from live_scanner.constants import (
        LIFECYCLE_CLOSED,
        LIFECYCLE_POSSIBLY_REMOVED,
        MISSING_SCANS_BEFORE_CLOSED,
        MISSING_SCANS_BEFORE_POSSIBLY_REMOVED,
    )

    path = db_path or workspace_db(user_id)
    possibly = 0
    closed = 0
    with db.get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT id, external_job_id, missing_scan_count, job_id
            FROM live_scanner_known_jobs
            WHERE user_id = ? AND source_id = ?
            """,
            (user_id, source_id),
        ).fetchall()
        for row in rows:
            ext = str(row["external_job_id"])
            if ext in present_external_ids:
                continue
            new_count = int(row["missing_scan_count"] or 0) + 1
            if new_count >= MISSING_SCANS_BEFORE_CLOSED:
                lifecycle = LIFECYCLE_CLOSED
                closed += 1
            elif new_count >= MISSING_SCANS_BEFORE_POSSIBLY_REMOVED:
                lifecycle = LIFECYCLE_POSSIBLY_REMOVED
                possibly += 1
            else:
                lifecycle = LIFECYCLE_ACTIVE
            conn.execute(
                """
                UPDATE live_scanner_known_jobs
                SET missing_scan_count = ?, lifecycle_status = ?
                WHERE id = ?
                """,
                (new_count, lifecycle, row["id"]),
            )
            if row["job_id"] and lifecycle != LIFECYCLE_ACTIVE:
                try:
                    conn.execute(
                        """
                        UPDATE jobs SET job_lifecycle_status = ?
                        WHERE id = ?
                        """,
                        (lifecycle, row["job_id"]),
                    )
                except Exception:  # noqa: BLE001
                    pass
        conn.commit()
    return {"possibly_removed": possibly, "closed": closed}


def count_baseline_jobs(user_id: str, *, db_path: Path | None = None) -> int:
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM live_scanner_known_jobs
            WHERE user_id = ? AND is_baseline = 1
            """,
            (user_id,),
        ).fetchone()
        return int(row["c"] if row else 0)


# ---------------------------------------------------------------------------
# Scanner state / activity / session jobs
# ---------------------------------------------------------------------------


def get_state(user_id: str, *, db_path: Path | None = None) -> dict[str, Any]:
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        row = conn.execute(
            "SELECT * FROM live_scanner_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row:
            return _row(row)
    return _default_state(user_id)


def save_state(user_id: str, patch: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    path = db_path or workspace_db(user_id)
    current = get_state(user_id, db_path=path)
    merged = {**current, **patch, "user_id": user_id, "updated_at": _utc_now()}
    cols = [
        "user_id",
        "status",
        "session_id",
        "session_started_at",
        "last_scan_at",
        "next_scan_at",
        "jobs_checked",
        "new_jobs",
        "duplicates_skipped",
        "relevant_jobs",
        "baseline_jobs",
        "jobs_fetched",
        "foreign_filtered",
        "israel_jobs",
        "sources_monitored",
        "sources_initialized",
        "sources_failed",
        "updated_at",
    ]
    values = [merged.get(c) for c in cols]
    placeholders = ", ".join("?" for _ in cols)
    assignments = ", ".join(f"{c} = ?" for c in cols if c != "user_id")
    with db.get_connection(path) as conn:
        if db.uses_postgres():
            conn.execute(
                f"""
                INSERT INTO live_scanner_state ({', '.join(cols)})
                VALUES ({placeholders})
                ON CONFLICT (user_id) DO UPDATE SET {assignments}
                """,
                values + [merged.get(c) for c in cols if c != "user_id"],
            )
        else:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO live_scanner_state ({', '.join(cols)})
                VALUES ({placeholders})
                """,
                values,
            )
        conn.commit()
    return get_state(user_id, db_path=path)


def clear_session_display(user_id: str, *, db_path: Path | None = None) -> dict[str, Any]:
    """CLEAR: wipe activity log + session counters + session job rows only."""
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        conn.execute("DELETE FROM live_scanner_activity WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM live_scanner_session_jobs WHERE user_id = ?", (user_id,))
        conn.commit()
    stats = source_stats(user_id, db_path=path)
    return save_state(
        user_id,
        {
            "jobs_checked": 0,
            "new_jobs": 0,
            "duplicates_skipped": 0,
            "relevant_jobs": 0,
            "jobs_fetched": 0,
            "foreign_filtered": 0,
            "israel_jobs": 0,
            **stats,
        },
        db_path=path,
    )


def add_activity(
    user_id: str,
    message: str,
    *,
    level: str = "info",
    source_id: str | None = None,
    job_id: int | None = None,
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> None:
    path = db_path or workspace_db(user_id)
    state = get_state(user_id, db_path=path)
    with db.get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO live_scanner_activity (
                user_id, session_id, created_at, level, message, source_id, job_id, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                state.get("session_id"),
                _utc_now(),
                level,
                message,
                source_id,
                job_id,
                json.dumps(payload, ensure_ascii=False) if payload else None,
            ),
        )
        conn.commit()


def list_activity(
    user_id: str, *, limit: int = ACTIVITY_LIMIT_DEFAULT, db_path: Path | None = None
) -> list[dict[str, Any]]:
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM live_scanner_activity
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, max(1, min(limit, 500))),
        ).fetchall()
        return [_row(r) for r in rows]


def add_session_job(
    user_id: str,
    *,
    job_id: int,
    is_baseline: bool,
    title: str,
    company: str,
    location: str,
    provider: str,
    source_id: str,
    job_url: str,
    match_score: int | None = None,
    db_path: Path | None = None,
) -> None:
    path = db_path or workspace_db(user_id)
    state = get_state(user_id, db_path=path)
    session_id = state.get("session_id") or "none"
    with db.get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO live_scanner_session_jobs (
                user_id, session_id, job_id, discovered_at, is_baseline,
                title, company, location, provider, source_id, job_url, match_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                session_id,
                job_id,
                _utc_now(),
                1 if is_baseline else 0,
                title,
                company,
                location,
                provider,
                source_id,
                job_url,
                match_score,
            ),
        )
        conn.commit()


def list_session_jobs(
    user_id: str,
    *,
    limit: int = SESSION_JOBS_LIMIT_DEFAULT,
    new_only: bool = True,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        if new_only:
            rows = conn.execute(
                """
                SELECT * FROM live_scanner_session_jobs
                WHERE user_id = ? AND is_baseline = 0
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, max(1, min(limit, 500))),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM live_scanner_session_jobs
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, max(1, min(limit, 500))),
            ).fetchall()
        return [_row(r) for r in rows]


def list_known_jobs_for_display(
    user_id: str,
    *,
    limit: int = SESSION_JOBS_LIMIT_DEFAULT,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return recent known/baseline jobs for the live jobs table when the session feed is empty."""
    path = db_path or workspace_db(user_id)
    with db.get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT
                k.id AS id,
                k.job_id AS job_id,
                COALESCE(k.last_seen_at, k.first_seen_at) AS discovered_at,
                k.is_baseline AS is_baseline,
                COALESCE(j.title, '') AS title,
                COALESCE(j.company, s.company_name, '') AS company,
                COALESCE(j.location, '') AS location,
                COALESCE(j.provider, s.provider, '') AS provider,
                k.source_id AS source_id,
                COALESCE(j.job_url, k.canonical_url, '') AS job_url,
                j.match_score AS match_score
            FROM live_scanner_known_jobs k
            LEFT JOIN jobs j ON j.id = k.job_id
            LEFT JOIN live_scanner_sources s ON s.id = k.source_id
            WHERE k.user_id = ?
            ORDER BY COALESCE(k.last_seen_at, k.first_seen_at) DESC
            LIMIT ?
            """,
            (user_id, max(1, min(limit, 500))),
        ).fetchall()
        return [_row(r) for r in rows]


def annotate_job_live_metadata(
    job_id: int,
    *,
    discovered_by: str = DISCOVERED_BY,
    is_baseline: bool,
    baseline_created_at: str | None,
    external_job_id: str,
    provider: str,
    live_source_id: str,
    lifecycle_status: str = LIFECYCLE_ACTIVE,
    db_path: Path,
) -> None:
    with db.get_connection(db_path) as conn:
        try:
            conn.execute(
                """
                UPDATE jobs SET
                    discovered_by = ?,
                    is_baseline = ?,
                    baseline_created_at = COALESCE(baseline_created_at, ?),
                    external_job_id = ?,
                    provider = ?,
                    live_source_id = ?,
                    job_lifecycle_status = COALESCE(job_lifecycle_status, ?)
                WHERE id = ?
                """,
                (
                    discovered_by,
                    1 if is_baseline else 0,
                    baseline_created_at if is_baseline else None,
                    external_job_id,
                    provider,
                    live_source_id,
                    lifecycle_status,
                    job_id,
                ),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not annotate job %s live metadata: %s", job_id, exc)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass


def source_stats(user_id: str, *, db_path: Path | None = None) -> dict[str, int]:
    path = db_path or workspace_db(user_id)
    sources = list_sources(user_id, db_path=path)
    enabled = [s for s in sources if s.get("enabled")]
    initialized = [
        s for s in enabled if s.get("baseline_created_at") or s.get("status") == "LIVE"
    ]
    failed = [s for s in enabled if s.get("status") == "ERROR"]
    return {
        "sources_monitored": len(enabled),
        "sources_initialized": len(initialized),
        "sources_failed": len(failed),
        "baseline_jobs": count_baseline_jobs(user_id, db_path=path),
    }


def _default_state(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "status": STATUS_STOPPED,
        "session_id": None,
        "session_started_at": None,
        "last_scan_at": None,
        "next_scan_at": None,
        "jobs_checked": 0,
        "new_jobs": 0,
        "duplicates_skipped": 0,
        "relevant_jobs": 0,
        "baseline_jobs": 0,
        "jobs_fetched": 0,
        "foreign_filtered": 0,
        "israel_jobs": 0,
        "sources_monitored": 0,
        "sources_initialized": 0,
        "sources_failed": 0,
        "updated_at": None,
    }


def _row(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}
