"""Database helpers — PostgreSQL via DATABASE_URL, or local SQLite fallback."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from config import (
    CVS_DIR,
    DATABASE_URL,
    DB_PATH,
    LEGACY_DB_PATH,
    REGISTRY_DB_PATH,
    cv_db_path,
)
from date_utils import normalize_posted_date, today_iso
from job_identity import (
    compute_job_content_hash,
    compute_job_hash,
    extract_drushim_job_id,
    extract_gotfriends_job_id,
    extract_linkedin_job_id,
    normalize_job_url,
)

# Sentinel owner_cv_id for the legacy / global jobs scope (Postgres shared schema).
LEGACY_OWNER_CV_ID = "legacy"

_pg_engine = None


def uses_postgres() -> bool:
    """True when DATABASE_URL is configured (single shared Postgres schema)."""
    return bool(DATABASE_URL)


def _normalize_database_url(url: str) -> str:
    """Ensure SQLAlchemy uses the psycopg3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+psycopg" not in url.split("://", 1)[0]:
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def get_engine():
    """Lazy SQLAlchemy engine for PostgreSQL (pooled)."""
    global _pg_engine
    if _pg_engine is None:
        from sqlalchemy import create_engine

        _pg_engine = create_engine(
            _normalize_database_url(DATABASE_URL),
            pool_pre_ping=True,
            pool_size=5,
        )
    return _pg_engine


def owner_cv_id_for_path(db_path: Path) -> str:
    """Map a logical db_path to jobs.owner_cv_id (Postgres multi-CV isolation)."""
    try:
        resolved = db_path.resolve()
    except OSError:
        resolved = db_path

    try:
        cvs_root = CVS_DIR.resolve()
        rel = resolved.relative_to(cvs_root)
        if rel.parts:
            return rel.parts[0]
    except ValueError:
        pass

    # Fallback: .../cvs/<cv_id>/jobs.db even if CVS_DIR differs (tests).
    if resolved.name == "jobs.db" and resolved.parent.parent.name == "cvs":
        return resolved.parent.name

    return LEGACY_OWNER_CV_ID


def _jobs_scope_sql(
    db_path: Path, *, alias: str = "", column: str = "owner_cv_id"
) -> tuple[str, list[Any]]:
    """Return (sql_fragment, params) to filter jobs by owner in Postgres mode."""
    if not uses_postgres():
        return "", []
    col = f"{alias}.{column}" if alias else column
    return f" AND {col} = ?", [owner_cv_id_for_path(db_path)]


def _is_integrity_error(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    try:
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        if isinstance(exc, SAIntegrityError):
            return True
    except ImportError:
        pass
    return False


def _is_operational_error(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        return True
    try:
        from sqlalchemy.exc import ProgrammingError, OperationalError

        if isinstance(exc, (ProgrammingError, OperationalError)):
            return True
    except ImportError:
        pass
    return False


class _ResultCursor:
    """Minimal cursor API shared by SQLite and Postgres adapters."""

    def __init__(
        self,
        rows: list[Any] | None = None,
        *,
        lastrowid: int | None = None,
        rowcount: int = -1,
    ) -> None:
        self._rows = rows or []
        self._index = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self) -> Any | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[Any]:
        remaining = self._rows[self._index :]
        self._index = len(self._rows)
        return remaining


class _PgConnection:
    """SQLAlchemy connection wrapper with sqlite3-like execute(?, ...) API."""

    def __init__(self, sa_conn: Any) -> None:
        self._conn = sa_conn

    @staticmethod
    def _bind_sql(sql: str, params: Sequence[Any] | None) -> tuple[str, dict[str, Any]]:
        params = list(params or ())
        bind: dict[str, Any] = {}
        parts = sql.split("?")
        if len(parts) == 1:
            return sql, bind
        if len(parts) - 1 != len(params):
            raise ValueError(
                f"SQL placeholder count ({len(parts) - 1}) != params ({len(params)})"
            )
        out = parts[0]
        for i, part in enumerate(parts[1:]):
            key = f"p{i}"
            bind[key] = params[i]
            out += f":{key}" + part
        return out, bind

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> _ResultCursor:
        from sqlalchemy import text

        converted, bind = self._bind_sql(sql, params)
        sql_upper = sql.strip().upper()
        returning = False
        if (
            sql_upper.startswith("INSERT")
            and "RETURNING" not in sql_upper
            and re.search(r"INSERT\s+INTO\s+(jobs|applications|cv_scans|cv_job_matches)\b", sql, re.I)
        ):
            converted = converted.rstrip().rstrip(";") + " RETURNING id"
            returning = True

        result = self._conn.execute(text(converted), bind)
        lastrowid: int | None = None
        rows: list[Any] = []
        if result.returns_rows:
            mappings = list(result.mappings())
            if returning:
                if mappings:
                    lastrowid = mappings[0].get("id")
                rows = []
            else:
                rows = [dict(m) for m in mappings]
        return _ResultCursor(rows, lastrowid=lastrowid, rowcount=result.rowcount or 0)

    def executescript(self, script: str) -> None:
        from sqlalchemy import text

        # Strip line comments; split on semicolons.
        cleaned_lines: list[str] = []
        for line in script.splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                continue
            cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines)
        for stmt in cleaned.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(text(stmt))

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[Any]:
    """Open a DB connection (Postgres pool or SQLite file).

    ``db_path`` remains the public scope key: in Postgres mode it selects
    ``owner_cv_id`` for jobs; in SQLite mode it is the on-disk database file.
    """
    if uses_postgres():
        sa_conn = get_engine().connect()
        adapter = _PgConnection(sa_conn)
        try:
            yield adapter
            sa_conn.commit()
        except Exception:
            sa_conn.rollback()
            raise
        finally:
            sa_conn.close()
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_USER_ID = "default"
WORKSPACE_CV_ID = "workspace"

_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    hashed_password TEXT,
    display_name TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS cvs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    file_name TEXT NOT NULL,
    display_name TEXT,
    stored_path TEXT,
    file_ext TEXT,
    file_size INTEGER,
    file_hash TEXT,
    parsed_profile TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT,
    last_scan_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id)
);
"""

_JOBS_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    job_url TEXT NOT NULL UNIQUE,
    source TEXT,
    description TEXT,
    full_description TEXT,
    match_score INTEGER,
    match_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    applied_at TIMESTAMP,
    notes TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs (id)
);

CREATE TABLE IF NOT EXISTS cv_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cv_id TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT DEFAULT 'running',
    summary TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS cv_job_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cv_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    scan_id INTEGER,
    match_score INTEGER,
    match_reason TEXT,
    match_method TEXT,
    match_category TEXT,
    matched_skills TEXT,
    missing_skills TEXT,
    ai_decision TEXT,
    ai_strengths TEXT,
    ai_missing_skills TEXT,
    ai_explanation TEXT,
    ai_recommended_action TEXT,
    fallback_score INTEGER,
    candidate_strategy_hash TEXT,
    application_status TEXT DEFAULT 'not_sent',
    application_notes TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE (cv_id, job_id),
    FOREIGN KEY (job_id) REFERENCES jobs (id)
);

CREATE INDEX IF NOT EXISTS idx_cv_job_matches_cv ON cv_job_matches (cv_id);
CREATE INDEX IF NOT EXISTS idx_cv_job_matches_job ON cv_job_matches (job_id);
CREATE INDEX IF NOT EXISTS idx_cv_job_matches_scan ON cv_job_matches (scan_id);
CREATE INDEX IF NOT EXISTS idx_cv_scans_cv ON cv_scans (cv_id);

CREATE TABLE IF NOT EXISTS job_applications (
    id TEXT PRIMARY KEY,
    cv_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    application_url TEXT,
    started_at TEXT,
    completed_at TEXT,
    submitted_at TEXT,
    failure_reason TEXT,
    failure_category TEXT,
    requires_user_action_reason TEXT,
    external_confirmation_text TEXT,
    external_confirmation_url TEXT,
    attempt_number INTEGER DEFAULT 1,
    provider_name TEXT,
    current_step_url TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs (id)
);

CREATE TABLE IF NOT EXISTS job_application_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT,
    FOREIGN KEY (application_id) REFERENCES job_applications (id)
);

CREATE INDEX IF NOT EXISTS idx_job_applications_cv_job ON job_applications (cv_id, job_id);
CREATE INDEX IF NOT EXISTS idx_job_application_steps_app ON job_application_steps (application_id);
"""

# Full Postgres schema (all columns; shared tables + owner_cv_id isolation).
_PG_FULL_SCHEMA = """
CREATE TABLE IF NOT EXISTS cvs (
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

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    owner_cv_id TEXT NOT NULL DEFAULT 'legacy',
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    job_url TEXT NOT NULL,
    source TEXT,
    description TEXT,
    full_description TEXT,
    match_score INTEGER,
    match_reason TEXT,
    match_method TEXT,
    ai_decision TEXT,
    ai_strengths TEXT,
    ai_missing_skills TEXT,
    ai_recommended_action TEXT,
    ai_explanation TEXT,
    fallback_score INTEGER,
    job_hash TEXT,
    collected_at TEXT,
    enriched_at TEXT,
    matched_at TEXT,
    last_seen_at TEXT,
    job_content_hash TEXT,
    is_enriched INTEGER DEFAULT 0,
    is_matched INTEGER DEFAULT 0,
    match_category TEXT,
    matched_keywords TEXT,
    missing_keywords TEXT,
    rejection_reason TEXT,
    candidate_strategy_hash TEXT,
    source_query TEXT,
    source_category TEXT,
    source_strategy_hash TEXT,
    enrich_attempted_at TEXT,
    enrich_status TEXT,
    enrich_error TEXT,
    enrich_attempts INTEGER DEFAULT 0,
    last_enrich_hash TEXT,
    source_queries TEXT,
    source_categories TEXT,
    first_seen_at TEXT,
    seen_count INTEGER DEFAULT 1,
    job_profile TEXT,
    job_profile_hash TEXT,
    is_analyzed INTEGER DEFAULT 0,
    posted_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_cv_id, job_url)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_owner_job_hash
    ON jobs (owner_cv_id, job_hash)
    WHERE job_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs (owner_cv_id);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    status TEXT DEFAULT 'pending',
    applied_at TIMESTAMP,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS cv_scans (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    cv_id TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT DEFAULT 'running',
    summary TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS cv_job_matches (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    cv_id TEXT NOT NULL,
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    scan_id INTEGER,
    match_score INTEGER,
    match_reason TEXT,
    match_method TEXT,
    match_category TEXT,
    matched_skills TEXT,
    missing_skills TEXT,
    ai_decision TEXT,
    ai_strengths TEXT,
    ai_missing_skills TEXT,
    ai_explanation TEXT,
    ai_recommended_action TEXT,
    fallback_score INTEGER,
    candidate_strategy_hash TEXT,
    application_status TEXT DEFAULT 'not_sent',
    application_notes TEXT,
    created_at TEXT,
    updated_at TEXT,
    ats_score_label TEXT,
    ats_missing_mandatory TEXT,
    ats_relevant_experience TEXT,
    ats_reasons TEXT,
    ats_improvements TEXT,
    ats_component_scores TEXT,
    UNIQUE (cv_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_cv_job_matches_cv ON cv_job_matches (cv_id);
CREATE INDEX IF NOT EXISTS idx_cv_job_matches_job ON cv_job_matches (job_id);
CREATE INDEX IF NOT EXISTS idx_cv_job_matches_scan ON cv_job_matches (scan_id);
CREATE INDEX IF NOT EXISTS idx_cv_scans_cv ON cv_scans (cv_id);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    hashed_password TEXT,
    display_name TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS job_applications (
    id TEXT PRIMARY KEY,
    cv_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    application_url TEXT,
    started_at TEXT,
    completed_at TEXT,
    submitted_at TEXT,
    failure_reason TEXT,
    failure_category TEXT,
    requires_user_action_reason TEXT,
    external_confirmation_text TEXT,
    external_confirmation_url TEXT,
    attempt_number INTEGER DEFAULT 1,
    provider_name TEXT,
    current_step_url TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS job_application_steps (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    application_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT,
    FOREIGN KEY (application_id) REFERENCES job_applications (id)
);

CREATE INDEX IF NOT EXISTS idx_job_applications_cv_job ON job_applications (cv_id, job_id);
CREATE INDEX IF NOT EXISTS idx_job_application_steps_app ON job_application_steps (application_id);
"""

_LEGACY_CV_SCHEMA = _REGISTRY_SCHEMA

# Back-compat alias used by older call sites / docs.
_JOBS_SCHEMA = _JOBS_SCHEMA_SQLITE


def init_registry_db(db_path: Path = REGISTRY_DB_PATH) -> None:
    """Create the global CV registry (metadata only)."""
    if uses_postgres():
        with get_connection(db_path) as conn:
            conn.executescript(_PG_FULL_SCHEMA)
            conn.commit()
        return
    with get_connection(db_path) as conn:
        conn.executescript(_REGISTRY_SCHEMA)
        _apply_registry_migrations(conn)
        conn.commit()


def _apply_registry_migrations(conn: sqlite3.Connection) -> None:
    """Evolve the registry schema for multi-user CV support."""
    now = _utc_now()
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    for column, col_type in [
        ("email", "TEXT"),
        ("hashed_password", "TEXT"),
    ]:
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {col_type}")
    # Unique email index (NULL emails allowed for the legacy default user).
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email) "
        "WHERE email IS NOT NULL AND email != ''"
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO users (id, email, hashed_password, display_name, created_at, updated_at)
        VALUES (?, NULL, NULL, ?, ?, ?)
        """,
        (DEFAULT_USER_ID, "Default User", now, now),
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cvs)").fetchall()}
    for column, col_type in [
        ("user_id", "TEXT NOT NULL DEFAULT 'default'"),
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
    ]:
        if column not in columns:
            conn.execute(f"ALTER TABLE cvs ADD COLUMN {column} {col_type}")
    conn.execute(
        "UPDATE cvs SET user_id = ? WHERE user_id IS NULL OR user_id = ''",
        (DEFAULT_USER_ID,),
    )
    conn.execute("UPDATE cvs SET is_active = 1 WHERE is_active IS NULL")
    # Index must be created AFTER columns exist — old registries predate user_id.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cvs_user_active ON cvs (user_id, is_active)"
    )


def init_cv_data_db(cv_id: str) -> Path:
    """Ensure jobs/scans/matches storage for one CV (file or Postgres scope)."""
    path = cv_db_path(cv_id)
    if not uses_postgres():
        path.parent.mkdir(parents=True, exist_ok=True)
    init_db(path)
    return path


def _apply_jobs_migrations(conn: Any) -> None:
    for column, col_type in [
            ("source", "TEXT"),
            ("full_description", "TEXT"),
            ("match_reason", "TEXT"),
            ("match_method", "TEXT"),
            ("ai_decision", "TEXT"),
            ("ai_strengths", "TEXT"),
            ("ai_missing_skills", "TEXT"),
            ("ai_recommended_action", "TEXT"),
            ("ai_explanation", "TEXT"),
            ("fallback_score", "INTEGER"),
            ("job_hash", "TEXT"),
            ("collected_at", "TEXT"),
            ("enriched_at", "TEXT"),
            ("matched_at", "TEXT"),
            ("last_seen_at", "TEXT"),
            ("job_content_hash", "TEXT"),
            ("is_enriched", "INTEGER DEFAULT 0"),
            ("is_matched", "INTEGER DEFAULT 0"),
            ("match_category", "TEXT"),
            ("matched_keywords", "TEXT"),
            ("missing_keywords", "TEXT"),
            ("rejection_reason", "TEXT"),
            ("candidate_strategy_hash", "TEXT"),
            ("source_query", "TEXT"),
            ("source_category", "TEXT"),
            ("source_strategy_hash", "TEXT"),
            ("enrich_attempted_at", "TEXT"),
            ("enrich_status", "TEXT"),
            ("enrich_error", "TEXT"),
            ("enrich_attempts", "INTEGER DEFAULT 0"),
            ("last_enrich_hash", "TEXT"),
            ("source_queries", "TEXT"),
            ("source_categories", "TEXT"),
            ("first_seen_at", "TEXT"),
            ("seen_count", "INTEGER DEFAULT 1"),
            ("job_profile", "TEXT"),
            ("job_profile_hash", "TEXT"),
            ("is_analyzed", "INTEGER DEFAULT 0"),
            ("posted_date", "TEXT"),
            ("owner_cv_id", "TEXT"),
        ]:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {col_type}")
            conn.commit()
        except Exception as exc:
            if not _is_operational_error(exc):
                raise
            try:
                conn.rollback()
            except Exception:
                pass

    try:
        if uses_postgres():
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_owner_job_hash
                ON jobs (owner_cv_id, job_hash)
                WHERE job_hash IS NOT NULL
                """
            )
        else:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_job_hash ON jobs(job_hash)"
            )
        conn.commit()
    except Exception as exc:
        if not _is_operational_error(exc):
            raise
        try:
            conn.rollback()
        except Exception:
            pass

    # job_url is UNIQUE in the base schema; reinforce for older DBs that lost it.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_job_url ON jobs(job_url)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute(
            """
            UPDATE jobs
            SET posted_date = substr(
                COALESCE(first_seen_at, collected_at, created_at), 1, 10
            )
            WHERE posted_date IS NULL
              AND COALESCE(first_seen_at, collected_at, created_at) IS NOT NULL
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute(
            """
            UPDATE jobs
            SET first_seen_at = COALESCE(first_seen_at, collected_at, created_at, last_seen_at)
            WHERE first_seen_at IS NULL
            """
        )
        conn.commit()
    except Exception as exc:
        if not _is_operational_error(exc):
            raise
        try:
            conn.rollback()
        except Exception:
            pass

    try:
        rows = conn.execute(
            """
            SELECT id, title, company, location, description
            FROM jobs
            WHERE last_enrich_hash IS NULL
              AND (is_enriched = 1 OR enrich_attempted_at IS NOT NULL)
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE jobs SET last_enrich_hash = ? WHERE id = ?",
                (
                    listing_content_hash(
                        row["title"] or "",
                        row["company"] or "",
                        row["location"] or "",
                        row["description"] or "",
                    ),
                    row["id"],
                ),
            )
        if rows:
            conn.commit()
    except Exception as exc:
        if not _is_operational_error(exc):
            raise
        try:
            conn.rollback()
        except Exception:
            pass


def _apply_cv_match_migrations(conn: Any) -> None:
    """Add ATS explainability columns to cv_job_matches."""
    for column, col_type in [
        ("ats_score_label", "TEXT"),
        ("ats_missing_mandatory", "TEXT"),
        ("ats_relevant_experience", "TEXT"),
        ("ats_reasons", "TEXT"),
        ("ats_improvements", "TEXT"),
        ("ats_component_scores", "TEXT"),
        ("is_potential_junior_match", "INTEGER DEFAULT 0"),
        ("tailored_cv_path", "TEXT"),
        ("tailored_cv_updated_at", "TEXT"),
        ("initial_score", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE cv_job_matches ADD COLUMN {column} {col_type}")
            conn.commit()
        except Exception as exc:
            if not _is_operational_error(exc):
                raise
            try:
                conn.rollback()
            except Exception:
                pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cv_tailor_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cv_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            score_before INTEGER NOT NULL,
            score_after INTEGER NOT NULL,
            tailored_cv_path TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs (id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cv_tailor_versions_cv_job
        ON cv_tailor_versions (cv_id, job_id, id DESC)
        """
    )
    conn.commit()

    # Backfill initial_score for legacy rows (frozen baseline from first scan score).
    try:
        conn.execute(
            """
            UPDATE cv_job_matches
            SET initial_score = match_score
            WHERE initial_score IS NULL AND match_score IS NOT NULL
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def init_db(db_path: Path = DB_PATH) -> None:
    """Create job tables (and legacy cvs table when using the global jobs.db)."""
    if uses_postgres():
        with get_connection(db_path) as conn:
            conn.executescript(_PG_FULL_SCHEMA)
            conn.commit()
            _apply_jobs_migrations(conn)
            _apply_cv_match_migrations(conn)
        return

    with get_connection(db_path) as conn:
        conn.executescript(_JOBS_SCHEMA_SQLITE)
        if db_path.resolve() == LEGACY_DB_PATH.resolve():
            conn.executescript(_LEGACY_CV_SCHEMA)
        conn.commit()
        _apply_jobs_migrations(conn)
        _apply_cv_match_migrations(conn)


def _cv_data_counts(cv_id: str, registry_db: Path = REGISTRY_DB_PATH) -> tuple[int, int]:
    if uses_postgres():
        with get_connection(registry_db) as conn:
            scan_count = conn.execute(
                "SELECT COUNT(*) AS n FROM cv_scans WHERE cv_id = ?", (cv_id,)
            ).fetchone()["n"]
            match_count = conn.execute(
                "SELECT COUNT(*) AS n FROM cv_job_matches WHERE cv_id = ?", (cv_id,)
            ).fetchone()["n"]
        return int(match_count), int(scan_count)

    data_db = _resolve_cv_data_db(cv_id, registry_db)
    if not data_db.exists():
        return 0, 0
    with get_connection(data_db) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        scan_count = 0
        match_count = 0
        if "cv_scans" in tables:
            scan_count = conn.execute(
                "SELECT COUNT(*) AS n FROM cv_scans WHERE cv_id = ?", (cv_id,)
            ).fetchone()["n"]
        if "cv_job_matches" in tables:
            match_count = conn.execute(
                "SELECT COUNT(*) AS n FROM cv_job_matches WHERE cv_id = ?", (cv_id,)
            ).fetchone()["n"]
    return int(match_count), int(scan_count)


def _resolve_cv_data_db(cv_id: str, registry_db: Path = REGISTRY_DB_PATH) -> Path:
    """Return the database file that stores a CV's jobs/scans/matches."""
    with get_connection(registry_db) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "cv_job_matches" in tables:
            has_rows = conn.execute(
                "SELECT 1 FROM cv_job_matches WHERE cv_id = ? LIMIT 1",
                (cv_id,),
            ).fetchone()
            if has_rows is not None:
                return registry_db

    per_cv = cv_db_path(cv_id)
    if per_cv.exists():
        return per_cv
    return registry_db


def migrate_legacy_shared_database() -> bool:
    """Move multi-CV data from the old shared jobs.db into per-CV databases.

    No-op when using PostgreSQL (single shared schema already).
    """
    if uses_postgres():
        return False

    legacy = LEGACY_DB_PATH
    if not legacy.exists():
        return False

    init_registry_db()
    with get_connection(legacy) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "cvs" not in tables:
            return False
        legacy_cvs = conn.execute("SELECT * FROM cvs").fetchall()
        if not legacy_cvs:
            return False

    migrated_any = False
    for row in legacy_cvs:
        cv_id = row["id"]
        target = cv_db_path(cv_id)
        if target.exists():
            continue

        init_db(target)
        job_id_map: dict[int, int] = {}

        with get_connection(legacy) as src, get_connection(target) as dst:
            job_ids = [
                r["job_id"]
                for r in src.execute(
                    "SELECT DISTINCT job_id FROM cv_job_matches WHERE cv_id = ?",
                    (cv_id,),
                ).fetchall()
            ]
            for old_job_id in job_ids:
                job = src.execute("SELECT * FROM jobs WHERE id = ?", (old_job_id,)).fetchone()
                if job is None:
                    continue
                job_dict = dict(job)
                old_pk = job_dict.pop("id")
                columns = list(job_dict.keys())
                placeholders = ", ".join("?" for _ in columns)
                col_names = ", ".join(columns)
                cursor = dst.execute(
                    f"INSERT INTO jobs ({col_names}) VALUES ({placeholders})",
                    [job_dict[c] for c in columns],
                )
                job_id_map[old_job_id] = int(cursor.lastrowid)

            for scan in src.execute(
                "SELECT * FROM cv_scans WHERE cv_id = ? ORDER BY id", (cv_id,)
            ).fetchall():
                scan_dict = dict(scan)
                scan_dict.pop("id", None)
                cols = list(scan_dict.keys())
                dst.execute(
                    f"INSERT INTO cv_scans ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [scan_dict[c] for c in cols],
                )

            for match in src.execute(
                "SELECT * FROM cv_job_matches WHERE cv_id = ?", (cv_id,)
            ).fetchall():
                match_dict = dict(match)
                match_dict.pop("id", None)
                old_job_id = match_dict["job_id"]
                if old_job_id not in job_id_map:
                    continue
                match_dict["job_id"] = job_id_map[old_job_id]
                cols = list(match_dict.keys())
                dst.execute(
                    f"INSERT INTO cv_job_matches ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [match_dict[c] for c in cols],
                )

            for app in src.execute(
                """
                SELECT a.* FROM applications a
                JOIN cv_job_matches m ON m.job_id = a.job_id
                WHERE m.cv_id = ?
                """,
                (cv_id,),
            ).fetchall():
                app_dict = dict(app)
                app_dict.pop("id", None)
                old_job_id = app_dict["job_id"]
                if old_job_id not in job_id_map:
                    continue
                app_dict["job_id"] = job_id_map[old_job_id]
                cols = list(app_dict.keys())
                dst.execute(
                    f"INSERT INTO applications ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [app_dict[c] for c in cols],
                )
            dst.commit()

        migrated_any = True

    with get_connection(REGISTRY_DB_PATH) as registry, get_connection(legacy) as legacy_conn:
        for row in legacy_cvs:
            exists = registry.execute(
                "SELECT 1 FROM cvs WHERE id = ? LIMIT 1", (row["id"],)
            ).fetchone()
            if exists is not None:
                continue
            cols = [
                "id", "file_name", "display_name", "stored_path", "file_ext",
                "file_size", "file_hash", "parsed_profile", "created_at",
                "updated_at", "last_scan_at",
            ]
            registry.execute(
                f"INSERT INTO cvs ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [row[c] for c in cols],
            )
        registry.commit()

        if migrated_any:
            legacy_conn.execute("DELETE FROM cv_job_matches")
            legacy_conn.execute("DELETE FROM cv_scans")
            legacy_conn.execute("DELETE FROM cvs")
            legacy_conn.commit()

    return migrated_any


def ensure_multi_cv_storage() -> None:
    """Initialize registry and migrate old shared-DB layout if needed."""
    init_registry_db()
    if not uses_postgres():
        migrate_legacy_shared_database()
    _backfill_cv_profiles()


def _backfill_cv_profiles() -> None:
    """Create or refresh per-CV profile.json from cv_profile.json."""
    from cv_domain import refine_profile
    from profile_utils import save_profile_for_cv

    for cv in list_cvs():
        cv_id = cv["id"]
        cv_profile_path = cv_db_path(cv_id).parent / "cv_profile.json"
        if not cv_profile_path.exists():
            continue
        try:
            cv_profile = json.loads(cv_profile_path.read_text(encoding="utf-8"))
            cv_profile = refine_profile(cv_profile)
            cv_profile_path.write_text(
                json.dumps(cv_profile, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            save_profile_for_cv(cv_id, cv_profile)
        except (json.JSONDecodeError, OSError):
            pass


def _parse_source_list(value: Any) -> list[str]:
    """Parse a JSON list field or legacy single string."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return [text]


def _merge_source_list(existing_value: Any, new_item: str | None) -> str:
    items = _parse_source_list(existing_value)
    if new_item:
        item = str(new_item).strip()
        if item and item not in items:
            items.append(item)
    return json.dumps(items, ensure_ascii=False)


def listing_content_hash(
    title: str = "",
    company: str = "",
    location: str = "",
    description: str = "",
) -> str:
    """Hash of search-result listing fields only (excludes fetched full_description)."""
    return compute_job_content_hash(title, company, location, description, "")


def _stored_listing_hash(row: Any) -> str:
    """Listing hash for an existing row, compatible with legacy job_content_hash values."""
    if row["last_enrich_hash"]:
        return row["last_enrich_hash"]
    return listing_content_hash(
        row["title"] or "",
        row["company"] or "",
        row["location"] or "",
        row["description"] or "",
    )


def find_existing_job(
    conn: Any,
    *,
    job_url: str,
    title: str = "",
    company: str = "",
    location: str = "",
    owner_cv_id: str | None = None,
) -> Any | None:
    """Find an existing job row by identity hash, canonical URL, or Drushim job id."""
    canonical_url = normalize_job_url(job_url)
    identity = compute_job_hash(canonical_url or job_url, title, company, location)
    scope_sql = ""
    scope_params: list[Any] = []
    if owner_cv_id is not None and uses_postgres():
        scope_sql = " AND owner_cv_id = ?"
        scope_params = [owner_cv_id]

    row = conn.execute(
        f"SELECT * FROM jobs WHERE job_hash = ?{scope_sql}",
        (identity, *scope_params),
    ).fetchone()
    if row is not None:
        return row

    if canonical_url:
        row = conn.execute(
            f"SELECT * FROM jobs WHERE job_url = ?{scope_sql}",
            (canonical_url, *scope_params),
        ).fetchone()
        if row is not None:
            return row

    reference_url = canonical_url or job_url
    is_linkedin = "linkedin.com" in reference_url.lower()
    is_gotfriends = "gotfriends.co.il" in reference_url.lower()

    if is_gotfriends:
        gotfriends_id = extract_gotfriends_job_id(reference_url)
        if gotfriends_id:
            row = conn.execute(
                f"SELECT * FROM jobs WHERE job_hash = ?{scope_sql} LIMIT 1",
                (f"gotfriends:job:{gotfriends_id}", *scope_params),
            ).fetchone()
            if row is not None:
                return row
    elif not is_linkedin:
        drushim_id = extract_drushim_job_id(reference_url)
        if drushim_id:
            row = conn.execute(
                f"SELECT * FROM jobs WHERE job_url LIKE ?{scope_sql} LIMIT 1",
                (f"%/job/{drushim_id}/%", *scope_params),
            ).fetchone()
            if row is not None:
                return row
    else:
        linkedin_id = extract_linkedin_job_id(reference_url)
        if linkedin_id:
            row = conn.execute(
                f"SELECT * FROM jobs WHERE job_hash = ?{scope_sql} LIMIT 1",
                (f"linkedin:job:{linkedin_id}", *scope_params),
            ).fetchone()
            if row is not None:
                return row

    return None


def get_known_job_identity_keys(db_path: Path = DB_PATH) -> set[str]:
    """Return identity keys for every job already stored in the database."""
    keys: set[str] = set()
    scope_sql, scope_params = _jobs_scope_sql(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT job_hash, job_url, title, company, location FROM jobs WHERE 1=1{scope_sql}",
            scope_params,
        ).fetchall()
        for row in rows:
            if row["job_hash"]:
                keys.add(row["job_hash"])
            else:
                keys.add(
                    compute_job_hash(
                        row["job_url"],
                        row["title"] or "",
                        row["company"] or "",
                        row["location"] or "",
                    )
                )
    return keys


def get_latest_known_job_identity(
    db_path: Path = DB_PATH,
    *,
    source_category: str | None = None,
    source: str | None = None,
) -> dict[str, Any] | None:
    """Return the most recently saved job identity for delta-refresh early-break.

    Prefers ``(source_category, source)`` when provided, then category-only, then
    the newest job overall. Identity is the stable ``job_hash`` / canonical URL
    used by collectors before enrichment or scoring.
    """
    if not Path(db_path).exists():
        return None

    order_sql = (
        "ORDER BY COALESCE(first_seen_at, collected_at, created_at) DESC, id DESC"
    )

    def _row_to_identity(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        url = normalize_job_url(row["job_url"] or "")
        job_hash = row["job_hash"] or compute_job_hash(
            url,
            row["title"] or "",
            row["company"] or "",
            row["location"] or "",
        )
        if not url and not job_hash:
            return None
        return {
            "job_id": row["id"],
            "job_url": url,
            "job_hash": job_hash,
            "identity_key": job_hash,
            "title": row["title"] or "",
            "company": row["company"] or "",
            "location": row["location"] or "",
            "source": row["source"] or "",
            "source_category": row["source_category"] or "",
            "first_seen_at": row["first_seen_at"],
            "collected_at": row["collected_at"],
        }

    select_sql = (
        "SELECT id, job_url, job_hash, title, company, location, source, "
        "source_category, first_seen_at, collected_at FROM jobs"
    )

    with get_connection(db_path) as conn:
        if "jobs" not in _table_names(conn):
            return None

        attempts: list[tuple[str, tuple[Any, ...]]] = []
        category = (source_category or "").strip()
        board = (source or "").strip()
        if category and board:
            attempts.append(
                (
                    f"{select_sql} WHERE source_category = ? AND source = ? {order_sql} LIMIT 1",
                    (category, board),
                )
            )
            # source_categories JSON may list the category without source_category set.
            attempts.append(
                (
                    f"{select_sql} WHERE source = ? AND ("
                    f"source_category = ? OR instr(COALESCE(source_categories, ''), ?) > 0"
                    f") {order_sql} LIMIT 1",
                    (board, category, category),
                )
            )
        if category:
            attempts.append(
                (
                    f"{select_sql} WHERE source_category = ? {order_sql} LIMIT 1",
                    (category,),
                )
            )
            attempts.append(
                (
                    f"{select_sql} WHERE instr(COALESCE(source_categories, ''), ?) > 0 "
                    f"{order_sql} LIMIT 1",
                    (category,),
                )
            )
        if board:
            attempts.append(
                (
                    f"{select_sql} WHERE source = ? {order_sql} LIMIT 1",
                    (board,),
                )
            )
        attempts.append((f"{select_sql} {order_sql} LIMIT 1", ()))

        for sql, params in attempts:
            try:
                row = conn.execute(sql, params).fetchone()
            except Exception:  # noqa: BLE001 — older DBs may lack optional columns
                continue
            identity = _row_to_identity(row)
            if identity is not None:
                return identity
    return None


def get_known_job_urls(db_path: Path = DB_PATH) -> set[str]:
    """Return canonical ``job_url`` values already stored in SQLite.

    Used by collectors to skip known listings before any description fetch /
    enrichment work.
    """
    urls: set[str] = set()
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT job_url FROM jobs").fetchall()
        for row in rows:
            canonical = normalize_job_url(row["job_url"] or "")
            if canonical:
                urls.add(canonical)
    return urls


def job_url_exists(job_url: str, db_path: Path = DB_PATH) -> bool:
    """True when ``job_url`` (after normalization) is already in the database."""
    canonical = normalize_job_url(job_url)
    if not canonical:
        return False
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE job_url = ? LIMIT 1",
            (canonical,),
        ).fetchone()
        return row is not None


def insert_job(
    title: str,
    job_url: str,
    company: str | None = None,
    location: str | None = None,
    source: str | None = None,
    description: str | None = None,
    match_score: int | None = None,
    source_query: str | None = None,
    source_category: str | None = None,
    source_strategy_hash: str | None = None,
    posted_date: str | None = None,
    db_path: Path = DB_PATH,
) -> int | None:
    """Insert a job record. Returns the new row id, or None if the job already exists.

    Uses ``INSERT OR IGNORE`` so UNIQUE ``job_url`` / ``job_hash`` collisions are
    idempotent across re-runs of the collect agent.
    """
    canonical_url = normalize_job_url(job_url) or job_url.strip()
    job_hash = compute_job_hash(canonical_url, title, company or "", location or "")
    now = _utc_now()
    content_hash = listing_content_hash(title, company or "", location or "", description or "")
    posted = normalize_posted_date(posted_date, default_to_today=True) or today_iso()
    source_queries_json = json.dumps(
        [source_query] if source_query else [], ensure_ascii=False
    )
    source_categories_json = json.dumps(
        [source_category] if source_category else [], ensure_ascii=False
    )
    owner = owner_cv_id_for_path(db_path) if uses_postgres() else None

    with get_connection(db_path) as conn:
        existing = find_existing_job(
            conn,
            job_url=canonical_url,
            title=title,
            company=company or "",
            location=location or "",
            owner_cv_id=owner,
        )
        if existing is not None:
            return None

        try:
            if uses_postgres():
                cursor = conn.execute(
                    """
                    INSERT INTO jobs (
                        owner_cv_id, title, company, location, job_url, source, description,
                        match_score, job_hash, collected_at, first_seen_at, last_seen_at,
                        job_content_hash, source_query, source_category, source_strategy_hash,
                        source_queries, source_categories, seen_count,
                        is_enriched, is_matched, enrich_attempts, posted_date
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0, ?)
                    """,
                    (
                        owner,
                        title,
                        company,
                        location,
                        canonical_url,
                        source,
                        description,
                        match_score,
                        job_hash,
                        now,
                        now,
                        now,
                        content_hash,
                        source_query,
                        source_category,
                        source_strategy_hash,
                        source_queries_json,
                        source_categories_json,
                        posted,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO jobs (
                        title, company, location, job_url, source, description, match_score,
                        job_hash, collected_at, first_seen_at, last_seen_at, job_content_hash,
                        source_query, source_category, source_strategy_hash,
                        source_queries, source_categories, seen_count,
                        is_enriched, is_matched, enrich_attempts, posted_date
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0, ?)
                    """,
                    (
                        title,
                        company,
                        location,
                        canonical_url,
                        source,
                        description,
                        match_score,
                        job_hash,
                        now,
                        now,
                        now,
                        content_hash,
                        source_query,
                        source_category,
                        source_strategy_hash,
                        source_queries_json,
                        source_categories_json,
                        posted,
                    ),
                )
            conn.commit()
            if cursor.rowcount == 0 or not cursor.lastrowid:
                return None
            return cursor.lastrowid
        except Exception as exc:
            if _is_integrity_error(exc):
                return None
            raise


def touch_existing_job(
    job_url: str,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    description: str = "",
    source_query: str | None = None,
    source_category: str | None = None,
    source_strategy_hash: str | None = None,
    posted_date: str | None = None,
    db_path: Path = DB_PATH,
) -> tuple[int | None, bool]:
    """Update an existing job: merge sources, bump seen_count, touch last_seen_at.

    Returns (job_id, content_changed). Never resets enrichment/matching — re-running
    collect for an already-known job must skip Enrich/Match entirely.
    """
    canonical_url = normalize_job_url(job_url) or job_url.strip()
    incoming_posted = normalize_posted_date(posted_date, default_to_today=False)
    owner = owner_cv_id_for_path(db_path) if uses_postgres() else None

    with get_connection(db_path) as conn:
        row = find_existing_job(
            conn,
            job_url=canonical_url,
            title=title,
            company=company,
            location=location,
            owner_cv_id=owner,
        )
        if row is None:
            return None, False

        identity = compute_job_hash(
            canonical_url,
            title or row["title"] or "",
            company or row["company"] or "",
            location or row["location"] or "",
        )
        new_listing_hash = listing_content_hash(
            title or row["title"] or "",
            company or row["company"] or "",
            location or row["location"] or "",
            description or row["description"] or "",
        )
        content_changed = _stored_listing_hash(row) != new_listing_hash
        now = _utc_now()
        merged_queries = _merge_source_list(row["source_queries"] or row["source_query"], source_query)
        merged_categories = _merge_source_list(
            row["source_categories"] or row["source_category"], source_category
        )
        seen_count = int(row["seen_count"] or 1) + 1
        first_seen = row["first_seen_at"] or row["collected_at"] or now
        # Prefer an earlier (older) board publication date when we learn a better one.
        existing_posted = row["posted_date"] if "posted_date" in row.keys() else None
        if incoming_posted and existing_posted:
            posted = min(str(incoming_posted), str(existing_posted))
        else:
            posted = incoming_posted or existing_posted

        if content_changed:
            conn.execute(
                """
                UPDATE jobs SET
                    title = ?, company = ?, location = ?, description = ?,
                    job_url = ?, job_hash = ?, job_content_hash = ?,
                    last_seen_at = ?, first_seen_at = COALESCE(first_seen_at, ?),
                    source_query = ?, source_category = ?, source_strategy_hash = ?,
                    source_queries = ?, source_categories = ?, seen_count = ?,
                    posted_date = COALESCE(?, posted_date)
                WHERE id = ?
                """,
                (
                    title or row["title"],
                    company or row["company"],
                    location or row["location"],
                    description or row["description"],
                    canonical_url or row["job_url"],
                    identity,
                    new_listing_hash,
                    now,
                    first_seen,
                    source_query or row["source_query"],
                    source_category or row["source_category"],
                    source_strategy_hash or row["source_strategy_hash"],
                    merged_queries,
                    merged_categories,
                    seen_count,
                    posted,
                    row["id"],
                ),
            )
        else:
            conn.execute(
                """
                UPDATE jobs SET
                    job_url = ?, job_hash = ?, job_content_hash = ?,
                    last_seen_at = ?,
                    first_seen_at = COALESCE(first_seen_at, ?),
                    source_query = COALESCE(?, source_query),
                    source_category = COALESCE(?, source_category),
                    source_strategy_hash = COALESCE(?, source_strategy_hash),
                    source_queries = ?, source_categories = ?,
                    seen_count = ?,
                    posted_date = COALESCE(?, posted_date)
                WHERE id = ?
                """,
                (
                    canonical_url or row["job_url"],
                    identity,
                    new_listing_hash,
                    now,
                    first_seen,
                    source_query,
                    source_category,
                    source_strategy_hash,
                    merged_queries,
                    merged_categories,
                    seen_count,
                    posted,
                    row["id"],
                ),
            )
        conn.commit()
        return row["id"], content_changed


def upsert_collected_job(
    title: str,
    job_url: str,
    company: str | None = None,
    location: str | None = None,
    source: str | None = None,
    description: str | None = None,
    source_query: str | None = None,
    source_category: str | None = None,
    source_strategy_hash: str | None = None,
    posted_date: str | None = None,
    db_path: Path = DB_PATH,
) -> tuple[int | None, bool]:
    """Insert a new job or touch last_seen_at. Returns (job_id, is_new)."""
    job_id = insert_job(
        title=title,
        job_url=job_url,
        company=company,
        location=location,
        source=source,
        description=description,
        source_query=source_query,
        source_category=source_category,
        source_strategy_hash=source_strategy_hash,
        posted_date=posted_date,
        db_path=db_path,
    )
    if job_id is not None:
        return job_id, True

    existing_id, _ = touch_existing_job(
        job_url,
        title=title,
        company=company or "",
        location=location or "",
        description=description or "",
        source_query=source_query,
        source_category=source_category,
        source_strategy_hash=source_strategy_hash,
        posted_date=posted_date,
        db_path=db_path,
    )
    return existing_id, False


def get_all_jobs(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Return all jobs as a list of dictionaries."""
    scope_sql, scope_params = _jobs_scope_sql(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE 1=1{scope_sql} ORDER BY created_at DESC",
            scope_params,
        ).fetchall()
        return [dict(row) for row in rows]


# Application statuses persisted in the `applications` table.
APPLICATION_SENT = "sent"
APPLICATION_DECLINED = "declined"
APPLICATION_SKIPPED = "skipped"
APPLICATION_FAILED = "failed"
APPLICATION_PENDING = "pending"
APPLICATION_DRY_RUN = "dry_run"

# Jobs with these statuses are hidden from default suggestions (already handled).
APPLICATION_HANDLED_STATUSES = (
    APPLICATION_SENT,
    APPLICATION_DECLINED,
    APPLICATION_SKIPPED,
)


def get_jobs(
    min_score: int | None = None,
    *,
    exclude_handled: bool = False,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return jobs with optional application metadata joined in.

    When ``exclude_handled`` is True, omit jobs already sent/declined/skipped so
    each pipeline run only surfaces new actionable opportunities.
    """
    conditions: list[str] = ["1=1"]
    params: list[Any] = []

    scope_sql, scope_params = _jobs_scope_sql(db_path, alias="j")
    if scope_sql:
        conditions[0] = "1=1" + scope_sql
        params.extend(scope_params)

    if min_score is not None:
        conditions.append("j.match_score IS NOT NULL AND j.match_score >= ?")
        params.append(min_score)

    if exclude_handled:
        placeholders = ",".join("?" for _ in APPLICATION_HANDLED_STATUSES)
        conditions.append(
            f"(a.status IS NULL OR a.status NOT IN ({placeholders}))"
        )
        params.extend(APPLICATION_HANDLED_STATUSES)

    where = f"WHERE {' AND '.join(conditions)}"
    query = f"""
        SELECT
            j.*,
            a.status AS application_status,
            a.applied_at AS application_applied_at,
            a.notes AS application_notes
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        {where}
        ORDER BY j.match_score IS NULL, j.match_score DESC, j.created_at DESC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def update_match_score(
    job_id: int,
    score: int,
    reason: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """Save a match score (and optional reason) for a job."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET match_score = ?, match_reason = ? WHERE id = ?",
            (score, reason, job_id),
        )
        conn.commit()


def update_job_profile(
    job_id: int,
    profile_json: str,
    profile_hash: str,
    db_path: Path | None = None,
) -> None:
    """Persist structured job analysis on the job row."""
    path = db_path if db_path is not None else DB_PATH
    with get_connection(path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET job_profile = ?, job_profile_hash = ?, is_analyzed = 1
            WHERE id = ?
            """,
            (profile_json, profile_hash, job_id),
        )
        conn.commit()


def job_needs_analysis(job: dict[str, Any], db_path: Path | None = None) -> bool:
    """True when the job needs (re-)analysis into a structured profile."""
    if not job.get("full_description") and not job.get("description"):
        return False
    if not job.get("is_analyzed"):
        return True
    from job_analyzer import job_profile_hash

    current_hash = job_profile_hash(job)
    stored_hash = job.get("job_profile_hash") or ""
    return stored_hash != current_hash


def update_match_result(
    job_id: int,
    fields: dict[str, Any],
    db_path: Path = DB_PATH,
) -> None:
    """Save full match result including AI and local classification fields."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs SET
                match_score = ?,
                match_reason = ?,
                match_method = ?,
                ai_decision = ?,
                ai_strengths = ?,
                ai_missing_skills = ?,
                ai_recommended_action = ?,
                ai_explanation = ?,
                fallback_score = ?,
                match_category = ?,
                matched_keywords = ?,
                missing_keywords = ?,
                rejection_reason = ?,
                candidate_strategy_hash = ?,
                matched_at = ?,
                is_matched = 1
            WHERE id = ?
            """,
            (
                fields.get("match_score"),
                fields.get("match_reason"),
                fields.get("match_method"),
                fields.get("ai_decision"),
                fields.get("ai_strengths"),
                fields.get("ai_missing_skills"),
                fields.get("ai_recommended_action"),
                fields.get("ai_explanation"),
                fields.get("fallback_score"),
                fields.get("match_category"),
                fields.get("matched_keywords"),
                fields.get("missing_keywords"),
                fields.get("rejection_reason"),
                fields.get("candidate_strategy_hash"),
                fields.get("matched_at") or _utc_now(),
                job_id,
            ),
        )
        conn.commit()


ENRICH_SUCCESS = "success"
ENRICH_NO_DESCRIPTION = "no_description"
ENRICH_FAILED = "failed"
ENRICH_TIMEOUT = "timeout"
ENRICH_BLOCKED = "blocked"

ENRICH_DEFAULT_MAX_ATTEMPTS = 3
ENRICH_DEFAULT_RETRY_AFTER_DAYS = 3

# Statuses that may be retried after the retry window (not blocked).
_ENRICH_RETRYABLE = {ENRICH_FAILED, ENRICH_TIMEOUT}


def _enrich_source_hash(job: dict[str, Any]) -> str:
    """Hash of the collected job content (excludes the full_description we fetch)."""
    return listing_content_hash(
        job.get("title") or "",
        job.get("company") or "",
        job.get("location") or "",
        job.get("description") or "",
    )


def record_enrichment_attempt(
    job_id: int,
    status: str,
    *,
    full_description: str | None = None,
    error: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """Record the outcome of an enrichment attempt so it is never silently re-run.

    On success the full description and content hash are stored; otherwise the
    failure status/error and attempt bookkeeping are persisted so future runs can
    decide whether a retry is warranted.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT title, company, location, description, enrich_attempts
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return

        now = _utc_now()
        attempts = int(row["enrich_attempts"] or 0) + 1
        source_hash = listing_content_hash(
            row["title"] or "",
            row["company"] or "",
            row["location"] or "",
            row["description"] or "",
        )

        if status == ENRICH_SUCCESS and full_description:
            conn.execute(
                """
                UPDATE jobs SET
                    full_description = ?,
                    job_content_hash = ?,
                    enriched_at = ?, is_enriched = 1, is_matched = 0,
                    enrich_attempted_at = ?, enrich_status = ?, enrich_error = NULL,
                    enrich_attempts = ?, last_enrich_hash = ?
                WHERE id = ?
                """,
                (
                    full_description,
                    source_hash,
                    now,
                    now,
                    ENRICH_SUCCESS,
                    attempts,
                    source_hash,
                    job_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE jobs SET
                    enrich_attempted_at = ?, enrich_status = ?, enrich_error = ?,
                    enrich_attempts = ?, last_enrich_hash = ?
                WHERE id = ?
                """,
                (now, status, error, attempts, source_hash, job_id),
            )
        conn.commit()


def update_full_description(
    job_id: int,
    full_description: str,
    db_path: Path = DB_PATH,
) -> None:
    """Save the full job description and mark the job successfully enriched."""
    record_enrichment_attempt(
        job_id, ENRICH_SUCCESS, full_description=full_description, db_path=db_path
    )


def mark_enrichment_attempted(
    job_id: int,
    status: str = ENRICH_NO_DESCRIPTION,
    *,
    error: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """Record a non-successful enrichment attempt (default: no_description)."""
    record_enrichment_attempt(job_id, status, error=error, db_path=db_path)


def mark_all_jobs_for_rematch(db_path: Path = DB_PATH) -> int:
    """Clear match flags so jobs are re-scored on the next match run."""
    scope_sql, scope_params = _jobs_scope_sql(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE jobs SET is_matched = 0 WHERE 1=1{scope_sql}",
            scope_params,
        )
        conn.commit()
        return cursor.rowcount


def _enrich_last_attempt_age_days(job: dict[str, Any]) -> float | None:
    """Days since the last enrichment attempt, or None if never attempted."""
    ts = job.get("enrich_attempted_at")
    if not ts:
        return None
    try:
        last = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 86400


def enrich_skip_reason(
    job: dict[str, Any],
    *,
    redo: bool = False,
    retry_failed: bool = False,
    max_attempts: int = ENRICH_DEFAULT_MAX_ATTEMPTS,
    retry_after_days: int = ENRICH_DEFAULT_RETRY_AFTER_DAYS,
) -> tuple[bool, str]:
    """Decide whether a job needs enrichment.

    Returns ``(needs_enrichment, reason)`` where ``reason`` is a short status string
    used for logging (e.g. ``new``, ``retry:failed``, ``skip:no_description``).
    """
    if redo:
        return True, "redo"

    status = (job.get("enrich_status") or "").strip().lower()
    has_desc = bool(job.get("full_description"))

    if status == ENRICH_SUCCESS and has_desc:
        return False, "skip:success"

    # Legacy rows enriched before the status column existed.
    if not status:
        if job.get("is_enriched") and has_desc:
            return False, "skip:success"
        if job.get("is_enriched") or job.get("enrich_attempted_at"):
            # Previously attempted with no description but no recorded status.
            status = ENRICH_NO_DESCRIPTION

    attempted = bool(status) or bool(job.get("enrich_attempted_at"))
    if not attempted:
        return True, "new"

    content_changed = bool(job.get("last_enrich_hash")) and (
        _enrich_source_hash(job) != job.get("last_enrich_hash")
    )
    if content_changed:
        return True, "retry:content-changed"

    attempts = int(job.get("enrich_attempts") or 0)

    if retry_failed and (status in _ENRICH_RETRYABLE or status == ENRICH_NO_DESCRIPTION):
        if attempts < max_attempts:
            return True, "retry:retry-failed"
        return False, f"skip:max-attempts({attempts})"

    if status == ENRICH_NO_DESCRIPTION:
        return False, "skip:no_description"

    if status == ENRICH_BLOCKED:
        if retry_failed and attempts < max_attempts:
            return True, "retry:retry-failed"
        age = _enrich_last_attempt_age_days(job)
        if age is not None and retry_after_days > 0 and age >= retry_after_days and attempts < max_attempts:
            return True, f"retry:stale({int(age)}d)"
        return False, "skip:blocked"

    if status in _ENRICH_RETRYABLE:
        if attempts >= max_attempts:
            return False, f"skip:max-attempts({attempts})"
        age = _enrich_last_attempt_age_days(job)
        if age is not None and retry_after_days > 0 and age >= retry_after_days:
            return True, f"retry:stale({int(age)}d)"
        return False, f"skip:{status}"

    # Unknown attempted state — try once if we still have no description.
    if not has_desc:
        return True, "retry:unknown-state"
    return False, "skip:success"


def job_needs_enrichment(
    job: dict[str, Any],
    *,
    redo: bool = False,
    retry_failed: bool = False,
    max_attempts: int = ENRICH_DEFAULT_MAX_ATTEMPTS,
    retry_after_days: int = ENRICH_DEFAULT_RETRY_AFTER_DAYS,
) -> bool:
    needs, _ = enrich_skip_reason(
        job,
        redo=redo,
        retry_failed=retry_failed,
        max_attempts=max_attempts,
        retry_after_days=retry_after_days,
    )
    return needs


def job_needs_matching(
    job: dict[str, Any],
    *,
    current_strategy_hash: str,
    rematch: bool = False,
) -> bool:
    if rematch:
        return True
    if not job.get("is_matched"):
        return True
    stored_hash = job.get("candidate_strategy_hash") or ""
    if stored_hash and stored_hash != current_strategy_hash:
        return True
    return False


def get_handled_job_ids(db_path: Path = DB_PATH) -> set[int]:
    """Job ids that should not be suggested again (sent / declined / skipped)."""
    return get_applied_job_ids(statuses=APPLICATION_HANDLED_STATUSES, db_path=db_path)


def mark_job_declined(
    job_id: int,
    notes: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """Record that the user chose not to apply to this job."""
    record_application(
        job_id,
        APPLICATION_DECLINED,
        notes or "Marked as not applying",
        db_path=db_path,
    )


def mark_jobs_declined(
    job_ids: list[int],
    notes: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Mark multiple jobs as declined. Returns count updated."""
    for job_id in job_ids:
        mark_job_declined(job_id, notes=notes, db_path=db_path)
    return len(job_ids)


def record_application(
    job_id: int,
    status: str,
    notes: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """Insert or update the application record for a job.

    status is one of: pending, sent, failed, skipped, declined, dry_run.
    Each job has at most one application row (the latest attempt wins).
    """
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM applications WHERE job_id = ?",
            (job_id,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO applications (job_id, status, applied_at, notes)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                """,
                (job_id, status, notes),
            )
        else:
            conn.execute(
                """
                UPDATE applications
                SET status = ?, applied_at = CURRENT_TIMESTAMP, notes = ?
                WHERE job_id = ?
                """,
                (status, notes, job_id),
            )
        conn.commit()


def get_applied_job_ids(
    statuses: tuple[str, ...] = ("sent",),
    db_path: Path = DB_PATH,
) -> set[int]:
    """Return the set of job ids that already have an application in one of the statuses."""
    placeholders = ",".join("?" for _ in statuses)
    scope_sql, scope_params = _jobs_scope_sql(db_path, alias="j")
    with get_connection(db_path) as conn:
        if scope_sql:
            rows = conn.execute(
                f"""
                SELECT a.job_id
                FROM applications a
                JOIN jobs j ON j.id = a.job_id
                WHERE a.status IN ({placeholders}){scope_sql}
                """,
                (*statuses, *scope_params),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT job_id FROM applications WHERE status IN ({placeholders})",
                statuses,
            ).fetchall()
        return {row["job_id"] for row in rows}


# ---------------------------------------------------------------------------
# Multi-CV: CV records, scans, and per-CV job matches
# ---------------------------------------------------------------------------

# Per-CV application statuses (stored on cv_job_matches.application_status).
CV_APP_NOT_SENT = "not_sent"
CV_APP_SENT = "sent"
CV_APP_INTERESTED = "interested"
CV_APP_NOT_RELEVANT = "not_relevant"
CV_APP_APPLIED_MANUALLY = "applied_manually"

CV_APP_STATUSES = (
    CV_APP_NOT_SENT,
    CV_APP_SENT,
    CV_APP_INTERESTED,
    CV_APP_NOT_RELEVANT,
    CV_APP_APPLIED_MANUALLY,
)


def create_user(
    user_id: str,
    *,
    email: str,
    hashed_password: str,
    display_name: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Insert an authenticated user into the registry and return the row."""
    path = db_path or REGISTRY_DB_PATH
    now = _utc_now()
    init_registry_db(path)
    with get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO users (id, email, hashed_password, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                email.strip().lower(),
                hashed_password,
                display_name or email.split("@")[0],
                now,
                now,
            ),
        )
        conn.commit()
    user = get_user_by_id(user_id, db_path=path)
    assert user is not None
    return user


def get_user_by_id(user_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or REGISTRY_DB_PATH
    init_registry_db(path)
    with get_connection(path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row is not None else None


def get_user_by_email(email: str, db_path: Path | None = None) -> dict[str, Any] | None:
    if not email:
        return None
    path = db_path or REGISTRY_DB_PATH
    init_registry_db(path)
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE lower(email) = ? LIMIT 1",
            (email.strip().lower(),),
        ).fetchone()
        return dict(row) if row is not None else None


def create_cv(
    cv_id: str,
    *,
    file_name: str,
    stored_path: str,
    display_name: str | None = None,
    file_ext: str | None = None,
    file_size: int | None = None,
    file_hash: str | None = None,
    parsed_profile: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    is_active: bool = True,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Insert a new CV record and return it."""
    path = db_path or REGISTRY_DB_PATH
    now = _utc_now()
    init_registry_db(path)
    init_cv_data_db(cv_id)
    with get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO cvs (
                id, user_id, file_name, display_name, stored_path, file_ext, file_size,
                file_hash, parsed_profile, is_active, created_at, updated_at, last_scan_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                cv_id,
                user_id,
                file_name,
                display_name or file_name,
                stored_path,
                file_ext,
                file_size,
                file_hash,
                parsed_profile,
                1 if is_active else 0,
                now,
                now,
            ),
        )
        conn.commit()
    cv = get_cv(cv_id, db_path=path)
    assert cv is not None
    return cv


def get_cv(cv_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or REGISTRY_DB_PATH
    with get_connection(path) as conn:
        row = conn.execute("SELECT * FROM cvs WHERE id = ?", (cv_id,)).fetchone()
        return dict(row) if row is not None else None


def find_cv_by_hash(
    file_hash: str,
    *,
    user_id: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return an existing CV with the same file content hash, if any.

    When ``user_id`` is set, only that owner's CVs are considered (data isolation).
    """
    if not file_hash:
        return None
    path = db_path or REGISTRY_DB_PATH
    with get_connection(path) as conn:
        if user_id:
            row = conn.execute(
                "SELECT * FROM cvs WHERE file_hash = ? AND user_id = ? LIMIT 1",
                (file_hash, user_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM cvs WHERE file_hash = ? LIMIT 1", (file_hash,)
            ).fetchone()
        return dict(row) if row is not None else None


def list_cvs(
    *,
    user_id: str | None = None,
    active_only: bool = False,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return CVs (newest first) with lightweight match/scan counts."""
    path = db_path or REGISTRY_DB_PATH
    init_registry_db(path)
    clauses: list[str] = []
    params: list[Any] = []
    if user_id:
        clauses.append("c.user_id = ?")
        params.append(user_id)
    if active_only:
        clauses.append("c.is_active = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection(path) as conn:
        rows = conn.execute(
            f"""
            SELECT c.*
            FROM cvs c
            {where}
            ORDER BY c.created_at DESC
            """,
            params,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            match_count, scan_count = _cv_data_counts(item["id"])
            item["match_count"] = match_count
            item["scan_count"] = scan_count
            result.append(item)
        return result


def list_active_cvs_for_user(
    user_id: str = DEFAULT_USER_ID,
    db_path: Path = REGISTRY_DB_PATH,
) -> list[dict[str, Any]]:
    """Return all active CV records for a user (newest first)."""
    return list_cvs(user_id=user_id, active_only=True, db_path=db_path)


def update_cv(cv_id: str, fields: dict[str, Any], db_path: Path = REGISTRY_DB_PATH) -> None:
    """Update editable CV fields (display_name, parsed_profile, last_scan_at)."""
    allowed = {"display_name", "parsed_profile", "last_scan_at", "file_name",
               "stored_path", "file_ext", "file_size", "file_hash", "is_active", "user_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = _utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    params = list(updates.values()) + [cv_id]
    with get_connection(db_path) as conn:
        conn.execute(f"UPDATE cvs SET {assignments} WHERE id = ?", params)
        conn.commit()


def set_cv_last_scan(cv_id: str, when: str | None = None, db_path: Path = REGISTRY_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE cvs SET last_scan_at = ?, updated_at = ? WHERE id = ?",
            (when or _utc_now(), _utc_now(), cv_id),
        )
        conn.commit()


def delete_cv(cv_id: str, db_path: Path = REGISTRY_DB_PATH) -> dict[str, Any]:
    """Delete a CV registry row, its scans/matches, and orphaned jobs.

    Safe when the per-CV / workspace jobs DB is missing or has no jobs tables yet
    (e.g. uploaded but never scanned) — the registry row is still removed so the
    same file can be uploaded again. In Postgres mode, jobs with ``owner_cv_id``
    equal to this CV are removed entirely.
    """
    deleted_matches = 0
    deleted_scans = 0
    deleted_jobs = 0
    orphaned_job_ids: list[int] = []

    if uses_postgres():
        with get_connection(db_path) as conn:
            match_row = conn.execute(
                "SELECT COUNT(*) AS n FROM cv_job_matches WHERE cv_id = ?", (cv_id,)
            ).fetchone()
            scan_row = conn.execute(
                "SELECT COUNT(*) AS n FROM cv_scans WHERE cv_id = ?", (cv_id,)
            ).fetchone()
            deleted_matches = int(match_row["n"])
            deleted_scans = int(scan_row["n"])

            conn.execute("DELETE FROM cv_job_matches WHERE cv_id = ?", (cv_id,))
            conn.execute("DELETE FROM cv_scans WHERE cv_id = ?", (cv_id,))

            job_rows = conn.execute(
                "SELECT id FROM jobs WHERE owner_cv_id = ?", (cv_id,)
            ).fetchall()
            job_ids = [r["id"] for r in job_rows]
            deleted_jobs = len(job_ids)
            orphaned_job_ids = list(job_ids)
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                try:
                    conn.execute(
                        f"DELETE FROM job_application_steps WHERE application_id IN "
                        f"(SELECT id FROM job_applications WHERE job_id IN ({placeholders}))",
                        job_ids,
                    )
                    conn.execute(
                        f"DELETE FROM job_applications WHERE job_id IN ({placeholders})",
                        job_ids,
                    )
                except Exception:
                    pass
                conn.execute(
                    f"DELETE FROM applications WHERE job_id IN ({placeholders})",
                    job_ids,
                )
                conn.execute("DELETE FROM jobs WHERE owner_cv_id = ?", (cv_id,))

            conn.execute("DELETE FROM cvs WHERE id = ?", (cv_id,))
            conn.commit()

        return {
            "cv_id": cv_id,
            "deleted_matches": deleted_matches,
            "deleted_scans": deleted_scans,
            "deleted_jobs": deleted_jobs,
            "orphaned_job_ids": orphaned_job_ids,
        }

    data_db = _resolve_cv_data_db(cv_id, db_path)
    if data_db.exists():
        with get_connection(data_db) as conn:
            tables = _table_names(conn)
            has_matches = "cv_job_matches" in tables
            has_scans = "cv_scans" in tables

            if has_matches:
                deleted_matches = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM cv_job_matches WHERE cv_id = ?",
                        (cv_id,),
                    ).fetchone()["n"]
                )
                job_ids = [
                    int(row["job_id"])
                    for row in conn.execute(
                        "SELECT DISTINCT job_id FROM cv_job_matches WHERE cv_id = ?",
                        (cv_id,),
                    ).fetchall()
                ]
                conn.execute("DELETE FROM cv_job_matches WHERE cv_id = ?", (cv_id,))
            else:
                job_ids = []

            if has_scans:
                deleted_scans = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM cv_scans WHERE cv_id = ?",
                        (cv_id,),
                    ).fetchone()["n"]
                )
                conn.execute("DELETE FROM cv_scans WHERE cv_id = ?", (cv_id,))

            if has_matches and "jobs" in tables:
                for job_id in job_ids:
                    still_referenced = conn.execute(
                        "SELECT 1 FROM cv_job_matches WHERE job_id = ? LIMIT 1",
                        (job_id,),
                    ).fetchone()
                    if still_referenced is not None:
                        continue
                    if "job_applications" in tables:
                        if "job_application_steps" in tables:
                            conn.execute(
                                "DELETE FROM job_application_steps WHERE application_id IN "
                                "(SELECT id FROM job_applications WHERE job_id = ?)",
                                (job_id,),
                            )
                        conn.execute(
                            "DELETE FROM job_applications WHERE job_id = ?", (job_id,)
                        )
                    if "applications" in tables:
                        conn.execute(
                            "DELETE FROM applications WHERE job_id = ?", (job_id,)
                        )
                    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                    orphaned_job_ids.append(job_id)
                    deleted_jobs += 1

            conn.commit()

    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM cvs WHERE id = ?", (cv_id,))
        conn.commit()

    return {
        "cv_id": cv_id,
        "deleted_matches": deleted_matches,
        "deleted_scans": deleted_scans,
        "deleted_jobs": deleted_jobs,
        "orphaned_job_ids": orphaned_job_ids,
    }


# --- Scans -----------------------------------------------------------------

SCAN_RUNNING = "running"
SCAN_SUCCESS = "success"
SCAN_FAILED = "failed"
SCAN_STOPPED = "stopped"


def reset_cv_job_pool(cv_id: str, db_path: Path | None = None) -> None:
    """Clear collected jobs and match rows before a fresh agent scan."""
    path = db_path or cv_db_path(cv_id)
    if not uses_postgres() and not path.exists():
        return
    with get_connection(path) as conn:
        if uses_postgres():
            job_rows = conn.execute(
                "SELECT id FROM jobs WHERE owner_cv_id = ?", (cv_id,)
            ).fetchall()
            job_ids = [r["id"] for r in job_rows]
            conn.execute("DELETE FROM cv_job_matches WHERE cv_id = ?", (cv_id,))
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                try:
                    conn.execute(
                        f"DELETE FROM job_application_steps WHERE application_id IN "
                        f"(SELECT id FROM job_applications WHERE job_id IN ({placeholders}))",
                        job_ids,
                    )
                    conn.execute(
                        f"DELETE FROM job_applications WHERE job_id IN ({placeholders})",
                        job_ids,
                    )
                except Exception:
                    pass
                conn.execute(
                    f"DELETE FROM applications WHERE job_id IN ({placeholders})",
                    job_ids,
                )
            conn.execute("DELETE FROM jobs WHERE owner_cv_id = ?", (cv_id,))
        else:
            tables = _table_names(conn)
            if "job_application_steps" in tables:
                conn.execute("DELETE FROM job_application_steps")
            if "job_applications" in tables:
                conn.execute("DELETE FROM job_applications")
            if "applications" in tables:
                conn.execute("DELETE FROM applications")
            conn.execute("DELETE FROM cv_job_matches WHERE cv_id = ?", (cv_id,))
            conn.execute("DELETE FROM jobs")
        conn.commit()


def create_scan(cv_id: str, db_path: Path = DB_PATH) -> int:
    """Start a new scan for a CV and return its id."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO cv_scans (cv_id, started_at, status) VALUES (?, ?, ?)",
            (cv_id, _utc_now(), SCAN_RUNNING),
        )
        conn.commit()
        return int(cursor.lastrowid)


def finish_scan(
    scan_id: int,
    status: str,
    *,
    summary: str | None = None,
    error_message: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE cv_scans
            SET finished_at = ?, status = ?, summary = ?, error_message = ?
            WHERE id = ?
            """,
            (_utc_now(), status, summary, error_message, scan_id),
        )
        conn.commit()


def get_scan(scan_id: int, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM cv_scans WHERE id = ?", (scan_id,)).fetchone()
        return dict(row) if row is not None else None


def _table_names(conn: Any) -> set[str]:
    if uses_postgres():
        rows = conn.execute(
            "SELECT tablename AS name FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
        ).fetchall()
        return {row["name"] for row in rows}
    return {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def ensure_jobs_schema(db_path: Path) -> None:
    """Ensure a jobs/scans/matches database has the expected schema.

    Safe to call on empty files created accidentally by ``get_connection``.
    """
    init_db(db_path)


def get_latest_scan(cv_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    if not uses_postgres() and not Path(db_path).exists():
        return None
    with get_connection(db_path) as conn:
        if "cv_scans" not in _table_names(conn):
            return None
        row = conn.execute(
            "SELECT * FROM cv_scans WHERE cv_id = ? ORDER BY id DESC LIMIT 1",
            (cv_id,),
        ).fetchone()
        return dict(row) if row is not None else None


def list_scans(cv_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    if not uses_postgres() and not Path(db_path).exists():
        return []
    with get_connection(db_path) as conn:
        if "cv_scans" not in _table_names(conn):
            return []
        rows = conn.execute(
            "SELECT * FROM cv_scans WHERE cv_id = ? ORDER BY id DESC", (cv_id,)
        ).fetchall()
        return [dict(row) for row in rows]


# --- Per-CV job matches -----------------------------------------------------

_CV_MATCH_FIELDS = (
    "match_score",
    "match_reason",
    "match_method",
    "match_category",
    "matched_skills",
    "missing_skills",
    "ai_decision",
    "ai_strengths",
    "ai_missing_skills",
    "ai_explanation",
    "ai_recommended_action",
    "fallback_score",
    "candidate_strategy_hash",
    "ats_score_label",
    "ats_missing_mandatory",
    "ats_relevant_experience",
    "ats_reasons",
    "ats_improvements",
    "ats_component_scores",
    "is_potential_junior_match",
)


def get_cv_job_match(
    cv_id: str, job_id: int, db_path: Path = DB_PATH
) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM cv_job_matches WHERE cv_id = ? AND job_id = ?",
            (cv_id, job_id),
        ).fetchone()
        return dict(row) if row is not None else None


def upsert_cv_job_match(
    cv_id: str,
    job_id: int,
    fields: dict[str, Any],
    *,
    scan_id: int | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Insert or update the match row for (cv_id, job_id).

    The application_status is preserved across re-scans; only the match/AI
    fields and scan_id are refreshed. Returns the match row id.
    """
    now = _utc_now()
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM cv_job_matches WHERE cv_id = ? AND job_id = ?",
            (cv_id, job_id),
        ).fetchone()

        values = {key: fields.get(key) for key in _CV_MATCH_FIELDS}

        if existing is None:
            columns = [
                "cv_id",
                "job_id",
                "scan_id",
                *_CV_MATCH_FIELDS,
                "initial_score",
                "application_status",
                "created_at",
                "updated_at",
            ]
            placeholders = ", ".join("?" for _ in columns)
            initial_score = values.get("match_score")
            params = [
                cv_id,
                job_id,
                scan_id,
                *[values[key] for key in _CV_MATCH_FIELDS],
                initial_score,
                CV_APP_NOT_SENT,
                now,
                now,
            ]
            cursor = conn.execute(
                f"INSERT INTO cv_job_matches ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                params,
            )
            conn.commit()
            return int(cursor.lastrowid)

        assignments = ", ".join(f"{key} = ?" for key in _CV_MATCH_FIELDS)
        params = [values[key] for key in _CV_MATCH_FIELDS]
        params.append(scan_id)
        params.append(now)
        params.append(existing["id"])
        conn.execute(
            f"UPDATE cv_job_matches SET {assignments}, scan_id = ?, updated_at = ? "
            f"WHERE id = ?",
            params,
        )
        conn.commit()
        return int(existing["id"])


def cv_job_needs_matching(
    cv_id: str,
    job_id: int,
    *,
    current_strategy_hash: str,
    rematch: bool = False,
    db_path: Path = DB_PATH,
) -> bool:
    """True when this CV has no up-to-date match for the job yet."""
    if rematch:
        return True
    existing = get_cv_job_match(cv_id, job_id, db_path=db_path)
    if existing is None:
        return True
    stored_hash = existing.get("candidate_strategy_hash") or ""
    if stored_hash and stored_hash != current_strategy_hash:
        return True
    if existing.get("match_score") is None:
        return True
    return False


def refresh_cv_job_match_scan(
    cv_id: str,
    job_id: int,
    scan_id: int | None,
    *,
    db_path: Path = DB_PATH,
) -> bool:
    """Attach an existing match row to the current scan without rescoring.

    The UI lists matches for the latest scan_id. Without this, jobs skipped as
    "already matched" disappear from the latest-scan view.
    """
    if scan_id is None:
        return False
    now = _utc_now()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE cv_job_matches
            SET scan_id = ?, updated_at = ?
            WHERE cv_id = ? AND job_id = ?
            """,
            (scan_id, now, cv_id, job_id),
        )
        conn.commit()
        return cursor.rowcount > 0


# Date sort uses YYYY-MM-DD posted_date first so ORDER BY is chronological,
# not lexicographic across mixed timestamp string formats.
_MATCH_SORT_COLUMNS = {
    "score": "m.match_score",
    "date": (
        "COALESCE("
        "j.posted_date, "
        "substr(COALESCE(j.first_seen_at, j.collected_at, j.created_at), 1, 10)"
        ")"
    ),
    "site": "LOWER(COALESCE(j.source, ''))",
}


def get_cv_matches(
    cv_id: str,
    *,
    latest_only: bool = False,
    min_score: int | None = None,
    sort_by: str | None = None,
    order: str | None = None,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return a CV's job matches joined with the global job record.

    Default sort: best match score first (potential junior matches last).
    Optional ``sort_by``: ``score``, ``date`` (board posted_date), or ``site``.
    Optional ``order``: ``asc`` or ``desc`` (default depends on sort_by).
    When ``latest_only`` is True, only the matches produced by the CV's most
    recent scan are returned.
    """
    if not Path(db_path).exists():
        return []
    with get_connection(db_path) as conn:
        tables = _table_names(conn)
        if "cv_job_matches" not in tables or "jobs" not in tables:
            return []

    conditions = ["m.cv_id = ?"]
    params: list[Any] = [cv_id]

    if latest_only:
        latest = get_latest_scan(cv_id, db_path=db_path)
        if latest is not None:
            conditions.append("m.scan_id = ?")
            params.append(latest["id"])

    if min_score is not None:
        conditions.append("m.match_score IS NOT NULL AND m.match_score >= ?")
        params.append(min_score)

    sort_key = (sort_by or "score").strip().lower()
    if sort_key not in _MATCH_SORT_COLUMNS:
        raise ValueError("sort_by must be one of: date, score, site")
    order_key = (order or ("desc" if sort_key in {"score", "date"} else "asc")).strip().lower()
    if order_key not in {"asc", "desc"}:
        raise ValueError("order must be asc or desc")
    sort_expr = _MATCH_SORT_COLUMNS[sort_key]
    # Keep null scores at the end for score sorts regardless of direction.
    nulls_last = "m.match_score IS NULL," if sort_key == "score" else ""
    # Preserve the potential-junior bucket after the primary sort column.
    order_sql = f"""
        ORDER BY
            CASE WHEN m.is_potential_junior_match = 1 AND COALESCE(m.match_score, 0) < 50
                 THEN 1 ELSE 0 END,
            {nulls_last}
            {sort_expr} {order_key.upper()},
            m.updated_at DESC
    """

    where = " AND ".join(conditions)
    query = f"""
        SELECT
            m.id AS match_id,
            m.cv_id,
            m.job_id,
            m.scan_id,
            m.match_score,
            m.match_reason,
            m.match_method,
            m.match_category,
            m.matched_skills,
            m.missing_skills,
            m.ai_decision,
            m.ai_strengths,
            m.ai_missing_skills,
            m.ai_explanation,
            m.ai_recommended_action,
            m.ats_score_label,
            m.ats_missing_mandatory,
            m.ats_relevant_experience,
            m.ats_reasons,
            m.ats_improvements,
            m.ats_component_scores,
            m.is_potential_junior_match,
            m.tailored_cv_path,
            m.tailored_cv_updated_at,
            m.application_status,
            m.application_notes,
            m.updated_at AS match_updated_at,
            j.title,
            j.company,
            j.location,
            j.job_url,
            j.source,
            j.description,
            j.full_description,
            j.posted_date,
            j.created_at AS job_created_at,
            j.collected_at,
            j.first_seen_at
        FROM cv_job_matches m
        JOIN jobs j ON j.id = m.job_id
        WHERE {where}
        {order_sql}
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def update_cv_match_status(
    cv_id: str,
    match_id: int,
    status: str,
    *,
    notes: str | None = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """Set the application status for one (cv, job) match. Returns updated row."""
    if status not in CV_APP_STATUSES:
        raise ValueError(f"invalid application status: {status}")
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE cv_job_matches
            SET application_status = ?, application_notes = ?, updated_at = ?
            WHERE id = ? AND cv_id = ?
            """,
            (status, notes, _utc_now(), match_id, cv_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM cv_job_matches WHERE id = ?", (match_id,)
        ).fetchone()
        return dict(row) if row is not None else None


def mark_cv_match_tailored(
    cv_id: str,
    job_id: int,
    *,
    tailored_cv_path: str,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """Record that a tailored CV was generated for this match."""
    now = _utc_now()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE cv_job_matches
            SET tailored_cv_path = ?, tailored_cv_updated_at = ?, updated_at = ?
            WHERE cv_id = ? AND job_id = ?
            """,
            (tailored_cv_path, now, now, cv_id, job_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM cv_job_matches WHERE cv_id = ? AND job_id = ?",
            (cv_id, job_id),
        ).fetchone()
        return dict(row) if row is not None else None


def get_match_baseline_score(
    cv_id: str,
    job_id: int,
    *,
    db_path: Path = DB_PATH,
) -> int | None:
    """Return the frozen scan baseline (initial_score) for a CV/job match."""
    match = get_cv_job_match(cv_id, job_id, db_path=db_path)
    if match is None:
        return None
    baseline = match.get("initial_score")
    if baseline is None:
        baseline = match.get("match_score")
    if baseline is None:
        return None
    try:
        return int(baseline)
    except (TypeError, ValueError):
        return None


def record_cv_tailor_version(
    cv_id: str,
    job_id: int,
    *,
    score_before: int,
    score_after: int,
    tailored_cv_path: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Persist one tailored-CV version with explicit score progression."""
    now = _utc_now()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO cv_tailor_versions (
                cv_id, job_id, score_before, score_after, tailored_cv_path, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (cv_id, job_id, score_before, score_after, tailored_cv_path, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_latest_cv_tailor_version(
    cv_id: str,
    job_id: int,
    *,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """Return the newest tailored-CV version row for (cv_id, job_id)."""
    if not Path(db_path).exists():
        return None
    with get_connection(db_path) as conn:
        tables = _table_names(conn)
        if "cv_tailor_versions" not in tables:
            return None
        row = conn.execute(
            """
            SELECT *
            FROM cv_tailor_versions
            WHERE cv_id = ? AND job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (cv_id, job_id),
        ).fetchone()
        return dict(row) if row is not None else None


def list_cv_tailor_versions(
    cv_id: str,
    job_id: int,
    *,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return tailored-CV version history for (cv_id, job_id), newest first."""
    if not Path(db_path).exists():
        return []
    with get_connection(db_path) as conn:
        tables = _table_names(conn)
        if "cv_tailor_versions" not in tables:
            return []
        rows = conn.execute(
            """
            SELECT *
            FROM cv_tailor_versions
            WHERE cv_id = ? AND job_id = ?
            ORDER BY id DESC
            """,
            (cv_id, job_id),
        ).fetchall()
        return [dict(row) for row in rows]


def update_cv_match_status_by_job(
    cv_id: str,
    job_id: int,
    status: str,
    *,
    notes: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """Set application status on the cv_job_matches row for (cv_id, job_id)."""
    if status not in CV_APP_STATUSES:
        raise ValueError(f"invalid application status: {status}")
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE cv_job_matches
            SET application_status = ?, application_notes = ?, updated_at = ?
            WHERE cv_id = ? AND job_id = ?
            """,
            (status, notes, _utc_now(), cv_id, job_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Automated job applications
# ---------------------------------------------------------------------------

JOB_APP_PENDING = "pending"
JOB_APP_IN_PROGRESS = "in_progress"
JOB_APP_SUBMITTED = "submitted"
JOB_APP_FAILED = "failed"
JOB_APP_REQUIRES_USER_ACTION = "requires_user_action"

JOB_APP_STATUSES = (
    JOB_APP_PENDING,
    JOB_APP_IN_PROGRESS,
    JOB_APP_SUBMITTED,
    JOB_APP_FAILED,
    JOB_APP_REQUIRES_USER_ACTION,
)

JOB_APP_TERMINAL_STATUSES = (
    JOB_APP_SUBMITTED,
    JOB_APP_FAILED,
    JOB_APP_REQUIRES_USER_ACTION,
)

STEP_SUCCESS = "success"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
STEP_REQUIRES_USER_ACTION = "requires_user_action"


def get_job_by_id(job_id: int, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row is not None else None


def create_job_application(
    application_id: str,
    cv_id: str,
    job_id: int,
    *,
    application_url: str | None = None,
    attempt_number: int = 1,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    now = _utc_now()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO job_applications (
                id, cv_id, job_id, status, application_url, attempt_number,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                application_id,
                cv_id,
                job_id,
                JOB_APP_PENDING,
                application_url,
                attempt_number,
                now,
                now,
            ),
        )
        conn.commit()
    app = get_job_application(application_id, db_path=db_path)
    assert app is not None
    return app


def get_job_application(
    application_id: str, db_path: Path = DB_PATH
) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM job_applications WHERE id = ?", (application_id,)
        ).fetchone()
        return dict(row) if row is not None else None


def get_latest_job_application(
    cv_id: str,
    job_id: int,
    *,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM job_applications
            WHERE cv_id = ? AND job_id = ?
            ORDER BY created_at DESC, attempt_number DESC
            LIMIT 1
            """,
            (cv_id, job_id),
        ).fetchone()
        return dict(row) if row is not None else None


def count_successful_job_applications(
    cv_id: str,
    job_id: int,
    *,
    db_path: Path = DB_PATH,
) -> int:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM job_applications
            WHERE cv_id = ? AND job_id = ? AND status = ?
            """,
            (cv_id, job_id, JOB_APP_SUBMITTED),
        ).fetchone()
        return int(row["n"])


def list_job_applications_for_cv_job(
    cv_id: str,
    job_id: int,
    *,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM job_applications
            WHERE cv_id = ? AND job_id = ?
            ORDER BY created_at DESC
            """,
            (cv_id, job_id),
        ).fetchall()
        return [dict(row) for row in rows]


def update_job_application(
    application_id: str,
    fields: dict[str, Any],
    *,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    allowed = {
        "status",
        "application_url",
        "started_at",
        "completed_at",
        "submitted_at",
        "failure_reason",
        "failure_category",
        "requires_user_action_reason",
        "external_confirmation_text",
        "external_confirmation_url",
        "attempt_number",
        "provider_name",
        "current_step_url",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_job_application(application_id, db_path=db_path)
    updates["updated_at"] = _utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    params = list(updates.values()) + [application_id]
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE job_applications SET {assignments} WHERE id = ?",
            params,
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
    return get_job_application(application_id, db_path=db_path)


def add_job_application_step(
    application_id: str,
    step_name: str,
    status: str,
    *,
    message: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO job_application_steps (
                application_id, step_name, status, message, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (application_id, step_name, status, message, _utc_now()),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_job_application_steps(
    application_id: str, db_path: Path = DB_PATH
) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM job_application_steps
            WHERE application_id = ?
            ORDER BY id ASC
            """,
            (application_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_latest_job_applications_for_cv(
    cv_id: str,
    job_ids: list[int] | None = None,
    *,
    db_path: Path = DB_PATH,
) -> dict[int, dict[str, Any]]:
    """Return the latest application attempt per job for a CV."""
    with get_connection(db_path) as conn:
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            rows = conn.execute(
                f"""
                SELECT * FROM job_applications
                WHERE cv_id = ? AND job_id IN ({placeholders})
                ORDER BY job_id, created_at DESC, attempt_number DESC
                """,
                [cv_id, *job_ids],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM job_applications
                WHERE cv_id = ?
                ORDER BY job_id, created_at DESC, attempt_number DESC
                """,
                (cv_id,),
            ).fetchall()

    latest: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        job_id = int(item["job_id"])
        if job_id not in latest:
            latest[job_id] = item
    return latest


if __name__ == "__main__":
    init_registry_db()
    init_db()
    if uses_postgres():
        print("PostgreSQL schema initialized via DATABASE_URL")
    else:
        print(f"Database initialized at {DB_PATH}")
