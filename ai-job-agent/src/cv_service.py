"""Multi-CV management service.

Owns everything that is *per-CV*: storing the uploaded resume file, creating the
DB record, running the agent pipeline for a single CV (recorded as a scan), and
deleting a CV together with all of its data.

Every per-CV artifact lives under ``data/cvs/<cv_id>/`` and every scan/match/
status is keyed by ``cv_id`` in the database, so running the agent for one CV
can never read or overwrite another CV's data.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db
from config import (
    CVS_DIR,
    LEGACY_CV_PROFILE_PATH,
    PROJECT_ROOT,
    RESUMES_DIR,
    cv_data_dir,
    cv_db_path,
)
from profile_utils import save_profile_for_cv

SRC = PROJECT_ROOT / "src"
PYTHON = sys.executable

ALLOWED_CV_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"}
MAX_CV_SIZE = 15 * 1024 * 1024

# Steps run for a single CV. Each runs as a subprocess with AGENT_CV_ID (and
# AGENT_SCAN_ID) set, so config.py resolves per-CV paths automatically.
SCAN_STEPS = [
    ("parse_cv", "ניתוח קורות החיים", "parse_cv.py", ["--yes"]),
    ("analyze_roles", "בניית אסטרטגיית חיפוש", "analyze_roles.py", []),
    ("collect", "איסוף משרות", "collect_jobs.py", []),
    ("enrich", "שליפת תיאורי משרה", "enrich_jobs.py", []),
    ("match", "חישוב ציוני התאמה", "match_jobs.py", []),
]

# Steps that abort the scan when they fail (same policy as run_all.py).
CRITICAL_STEPS = {"parse_cv", "analyze_roles", "match"}


class DuplicateCvError(Exception):
    """Raised when the same file is uploaded again without an explicit override."""

    def __init__(self, existing: dict[str, Any]):
        self.existing = existing
        super().__init__(f"CV already uploaded (id={existing.get('id')})")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store_cv_file(cv_id: str, ext: str, data: bytes) -> Path:
    """Write the uploaded resume to data/cvs/<cv_id>/resume.<ext>."""
    directory = cv_data_dir(cv_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"resume{ext}"
    target.write_bytes(data)
    return target


def upload_cv(
    filename: str,
    data: bytes,
    *,
    display_name: str | None = None,
    as_new_version: bool = False,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Store an uploaded resume and create its CV record.

    Raises ``DuplicateCvError`` if an identical file was already uploaded, unless
    ``as_new_version`` is True (the user explicitly wants a separate version).
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_CV_EXTENSIONS:
        raise ValueError(f"unsupported file type: {ext}")
    if not data:
        raise ValueError("empty file")
    if len(data) > MAX_CV_SIZE:
        raise ValueError("file too large")

    file_hash = compute_file_hash(data)
    if not as_new_version:
        existing = db.find_cv_by_hash(file_hash, db_path=db_path)
        if existing is not None:
            raise DuplicateCvError(existing)

    cv_id = uuid.uuid4().hex
    stored = _store_cv_file(cv_id, ext, data)
    try:
        stored_path = str(stored.relative_to(PROJECT_ROOT))
    except ValueError:
        stored_path = str(stored)
    return db.create_cv(
        cv_id,
        file_name=filename,
        display_name=display_name,
        stored_path=stored_path,
        file_ext=ext,
        file_size=len(data),
        file_hash=file_hash,
        db_path=db_path,
    )


def adopt_legacy_cv(db_path: Path = db.REGISTRY_DB_PATH) -> dict[str, Any] | None:
    """Import an existing single-CV setup (resumes/cv.* + cv_profile.json).

    Lets the old single-CV flow keep working by treating the existing resume as
    the first CV record. Returns the created CV, or None if there is nothing to
    adopt or a CV already exists.
    """
    if db.list_cvs(db_path=db_path):
        return None

    legacy_file: Path | None = None
    if RESUMES_DIR.exists():
        for path in sorted(RESUMES_DIR.iterdir()):
            if path.is_file() and path.stem.lower() == "cv":
                legacy_file = path
                break
    if legacy_file is None:
        return None

    data = legacy_file.read_bytes()
    cv = upload_cv(
        legacy_file.name,
        data,
        display_name=legacy_file.name,
        as_new_version=True,
        db_path=db_path,
    )

    # Carry over the already-parsed profile so the imported CV keeps its data.
    if LEGACY_CV_PROFILE_PATH.exists():
        try:
            profile_text = LEGACY_CV_PROFILE_PATH.read_text(encoding="utf-8")
            profile = json.loads(profile_text)
            target = cv_data_dir(cv["id"]) / "cv_profile.json"
            target.write_text(profile_text, encoding="utf-8")
            db.update_cv(
                cv["id"],
                {"parsed_profile": json.dumps(profile, ensure_ascii=False)},
                db_path=db_path,
            )
        except (json.JSONDecodeError, OSError):
            pass

    return db.get_cv(cv["id"], db_path=db_path)


def delete_cv(cv_id: str, db_path: Path = db.REGISTRY_DB_PATH) -> dict[str, Any]:
    """Delete a CV: its file/directory, parsed profile, scans, and matches.

    Global job records are only removed when no other CV references them.
    """
    summary = db.delete_cv(cv_id, db_path=db_path)
    directory = cv_data_dir(cv_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    return summary


def sync_parsed_profile(cv_id: str, db_path: Path = db.REGISTRY_DB_PATH) -> None:
    """Copy the per-CV cv_profile.json into the DB record (if it exists)."""
    from cv_domain import refine_profile

    profile_path = cv_data_dir(cv_id) / "cv_profile.json"
    if not profile_path.exists():
        return
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    profile = refine_profile(profile)
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    save_profile_for_cv(cv_id, profile)
    db.update_cv(
        cv_id,
        {"parsed_profile": json.dumps(profile, ensure_ascii=False)},
        db_path=db_path,
    )
    db.update_cv(
        cv_id,
        {"parsed_profile": json.dumps(profile, ensure_ascii=False)},
        db_path=db_path,
    )


def run_scan(
    cv_id: str,
    *,
    skip_collect: bool = False,
    skip_enrich: bool = False,
    log: Callable[[str], None] | None = None,
    set_step_status: Callable[[str, str], None] | None = None,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Run the full agent pipeline for a single CV, recorded as a cv_scan.

    Returns the finished scan record. The heavy steps run as subprocesses with
    AGENT_CV_ID / AGENT_SCAN_ID set so all generated data is stored under this
    CV id and never mixes with another CV's results.
    """
    import os

    cv_db = cv_db_path(cv_id)
    db.init_db(cv_db)

    cv = db.get_cv(cv_id, db_path=db_path)
    if cv is None:
        raise ValueError(f"unknown cv_id: {cv_id}")

    def _log(line: str) -> None:
        if log is not None:
            log(line)

    def _step(key: str, status: str) -> None:
        if set_step_status is not None:
            set_step_status(key, status)

    scan_id = db.create_scan(cv_id, db_path=cv_db)
    db.reset_cv_job_pool(cv_id)
    env = {**os.environ, "AGENT_CV_ID": cv_id, "AGENT_SCAN_ID": str(scan_id)}

    error: str | None = None
    for key, name, script, extra in SCAN_STEPS:
        skipped = (key == "collect" and skip_collect) or (
            key == "enrich" and skip_enrich
        )
        if skipped:
            _step(key, "skipped")
            _log(f"-- מדלג על: {name}")
            continue

        _step(key, "running")
        _log(f">> {name}")

        proc = subprocess.Popen(
            [PYTHON, str(SRC / script), *extra],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _log(line)
        code = proc.wait()

        if code != 0:
            _step(key, "failed")
            _log(f"!! {name} נכשל (קוד {code})")
            if key in CRITICAL_STEPS:
                error = f"השלב '{name}' נכשל"
                break
        else:
            _step(key, "success")
            if key == "parse_cv":
                sync_parsed_profile(cv_id, db_path=db_path)

    latest_scan = db.get_latest_scan(cv_id, db_path=cv_db)
    match_count = len(
        db.get_cv_matches(cv_id, latest_only=True, db_path=cv_db)
    )
    summary = json.dumps({"matches": match_count}, ensure_ascii=False)

    if error:
        db.finish_scan(
            scan_id, db.SCAN_FAILED, summary=summary, error_message=error, db_path=cv_db
        )
    else:
        db.finish_scan(scan_id, db.SCAN_SUCCESS, summary=summary, db_path=cv_db)
        db.set_cv_last_scan(cv_id, db_path=db_path)

    _ = latest_scan
    return db.get_scan(scan_id, db_path=cv_db) or {}
