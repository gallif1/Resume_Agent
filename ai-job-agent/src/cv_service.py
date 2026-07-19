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
from collection_report import parse_agent_line
from config import (
    CVS_DIR,
    LEGACY_CV_PROFILE_PATH,
    PROJECT_ROOT,
    RESUMES_DIR,
    cv_data_dir,
    cv_db_path,
    user_cv_profile_path,
    user_data_dir,
    user_db_path,
    user_master_profile_path,
)
from cv_aggregator_service import aggregate_and_save
from profile_utils import save_profile_for_cv, save_profile_for_user
from scan_control import (
    ScanCancelled,
    is_cancelled,
    register_process,
    unregister_process,
)

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

# Interactive two-step flow: analyze CV first, then search with user-selected domains.
ANALYZE_STEPS = [
    ("parse_cv", "ניתוח קורות החיים", "parse_cv.py", ["--yes"]),
    ("analyze_roles", "בניית אסטרטגיית חיפוש", "analyze_roles.py", []),
]

SEARCH_STEPS = [
    ("collect", "איסוף משרות", "collect_jobs.py", []),
    ("enrich", "שליפת תיאורי משרה", "enrich_jobs.py", []),
    ("match", "חישוב ציוני התאמה", "match_jobs.py", []),
]

# Unified multi-CV pipeline: parse all uploads, aggregate, then match jobs.
USER_SCAN_STEPS = [
    ("parse_cvs", "ניתוח כל קבצי קורות החיים", None, []),
    ("aggregate", "איחוד לפרופיל מועמד מאוחד", None, []),
    ("analyze_roles", "בניית אסטרטגיית חיפוש", "analyze_roles.py", []),
    ("collect", "איסוף משרות", "collect_jobs.py", []),
    ("enrich", "שליפת תיאורי משרה", "enrich_jobs.py", []),
    ("match", "חישוב ציוני התאמה", "match_jobs.py", []),
]

# Steps that abort the scan when they fail (same policy as run_all.py).
CRITICAL_STEPS = {"parse_cv", "parse_cvs", "aggregate", "analyze_roles", "match"}
USER_CRITICAL_STEPS = CRITICAL_STEPS
SEARCH_CRITICAL_STEPS = {"match"}


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
    user_id: str = db.DEFAULT_USER_ID,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Store an uploaded resume and create its CV record.

    Raises ``DuplicateCvError`` if an identical file was already uploaded, unless
    ``as_new_version`` is True (the user explicitly wants a separate version).
    Deduplication is scoped to ``user_id`` so users do not share CV ownership.
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
        existing = db.find_cv_by_hash(file_hash, user_id=user_id, db_path=db_path)
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
        user_id=user_id,
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


def extract_recommended_domains(strategy: dict[str, Any] | None) -> list[str]:
    """Build a de-duplicated list of suggested job domains/roles from a strategy.

    Surfaces every career track encoded in the matching strategy — best-fit roles,
    category titles, and collection search titles (EN + HE) — with no fixed
    count limit so Step 2 chips cover all relevant tracks from the CV.
    """
    if not strategy:
        return []

    domains: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        domains.append(text)

    for entry in strategy.get("best_fit_roles") or []:
        if isinstance(entry, dict):
            _add(entry.get("role"))
        else:
            _add(entry)

    for entry in strategy.get("job_categories") or []:
        if not isinstance(entry, dict):
            continue
        titles = entry.get("titles") or []
        if titles:
            for title in titles:
                _add(title)
        else:
            _add(entry.get("category"))

    for entry in strategy.get("collection_queries") or []:
        if not isinstance(entry, dict):
            continue
        _add(entry.get("primary_role"))
        for key in (
            "search_queries",
            "queries_en",
            "queries",
            "alternative_titles",
            "hebrew_search_queries",
            "queries_he",
            "queries_mixed",
        ):
            values = entry.get(key) or []
            if not isinstance(values, list):
                continue
            for title in values:
                _add(title)

    return domains


def analyze_cv(
    cv_id: str,
    *,
    log: Callable[[str], None] | None = None,
    set_step_status: Callable[[str, str], None] | None = None,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Parse a CV and analyze roles; return recommended domains without scraping."""
    import os

    from role_analyzer import load_matching_strategy

    cv = db.get_cv(cv_id, db_path=db_path)
    if cv is None:
        raise ValueError(f"unknown cv_id: {cv_id}")

    def _log(line: str) -> None:
        if log is not None:
            log(line)

    def _step(key: str, status: str) -> None:
        if set_step_status is not None:
            set_step_status(key, status)

    env = {**os.environ, "AGENT_CV_ID": cv_id}
    env.pop("AGENT_USER_ID", None)
    env.pop("AGENT_SCAN_ID", None)

    for key, name, script, extra in ANALYZE_STEPS:
        _step(key, "running")
        _log(f">> {name}")
        code = _run_logged_subprocess(
            [PYTHON, str(SRC / script), *extra],
            env=env,
            log=_log,
        )
        if code != 0:
            _step(key, "failed")
            _log(f"!! {name} נכשל (קוד {code})")
            raise RuntimeError(f"השלב '{name}' נכשל")
        _step(key, "success")
        if key == "parse_cv":
            sync_parsed_profile(cv_id, db_path=db_path)

    # Subprocesses wrote under data/cvs/<cv_id>/ with AGENT_CV_ID set.
    # The API process itself has no AGENT_CV_ID, so the module-level default
    # AI_MATCHING_STRATEGY_PATH still points at the legacy global file — load
    # the per-CV strategy explicitly (same pattern as run_search).
    strategy_path = cv_data_dir(cv_id) / "ai_matching_strategy.json"
    strategy = load_matching_strategy(strategy_path) or {}
    domains = extract_recommended_domains(strategy)
    # Always merge rule/AI roles from the parsed profile so secondary tracks
    # survive even if the strategy truncated them.
    profile_path = cv_data_dir(cv_id) / "cv_profile.json"
    profile: dict[str, Any] = {}
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            profile = {}

    seen = {d.casefold() for d in domains}
    for role in profile.get("best_fit_roles") or []:
        text = str(role or "").strip()
        if not text or text.casefold() in seen:
            continue
        domains.append(text)
        seen.add(text.casefold())
    for title in (profile.get("experience") or {}).get("job_titles") or []:
        text = str(title or "").strip()
        if not text or text.casefold() in seen:
            continue
        domains.append(text)
        seen.add(text.casefold())

    return {
        "cv_id": cv_id,
        "domains": domains,
        "candidate_summary": strategy.get("candidate_summary") or "",
        "career_notes": strategy.get("career_notes") or "",
        "best_fit_roles": strategy.get("best_fit_roles") or [],
    }


def run_search(
    cv_id: str,
    *,
    domains: list[str],
    skip_enrich: bool = False,
    job_sites: list[str] | None = None,
    log: Callable[[str], None] | None = None,
    set_step_status: Callable[[str, str], None] | None = None,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Collect/enrich/match for selected domains without wiping prior job history.

    New jobs are inserted incrementally (``INSERT OR IGNORE`` / identity dedupe).
    Already-known jobs are skipped by enrich/match and remain in the listing.
    """
    import os

    from job_boards import normalize_job_board_ids

    cleaned_domains = [str(d).strip() for d in domains if str(d).strip()]
    if not cleaned_domains:
        raise ValueError("יש לבחור לפחות תחום אחד לחיפוש")

    cv_db = cv_db_path(cv_id)
    db.init_db(cv_db)

    cv = db.get_cv(cv_id, db_path=db_path)
    if cv is None:
        raise ValueError(f"unknown cv_id: {cv_id}")

    strategy_path = cv_data_dir(cv_id) / "ai_matching_strategy.json"
    if not strategy_path.exists():
        raise ValueError("יש לנתח את קורות החיים לפני תחילת החיפוש")

    def _log(line: str) -> None:
        if log is not None:
            log(line)

    def _step(key: str, status: str) -> None:
        if set_step_status is not None:
            set_step_status(key, status)

    # Intentionally do NOT call reset_cv_job_pool — preserve prior scan history.
    scan_id = db.create_scan(cv_id, db_path=cv_db)
    env = {**os.environ, "AGENT_CV_ID": cv_id, "AGENT_SCAN_ID": str(scan_id)}
    env.pop("AGENT_USER_ID", None)

    try:
        selected_sites = normalize_job_board_ids(job_sites)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    error: str | None = None
    warnings: list[str] = []
    collection_summary: dict[str, Any] | None = None
    domains_arg = ",".join(cleaned_domains)

    for key, name, script, extra in SEARCH_STEPS:
        skipped = key == "enrich" and skip_enrich
        if skipped:
            _step(key, "skipped")
            _log(f"-- מדלג על: {name}")
            continue

        _step(key, "running")
        _log(f">> {name}")

        extra_args = list(extra)
        if key == "collect":
            extra_args = [*extra_args, "--domains", domains_arg]
            if selected_sites:
                extra_args = [*extra_args, "--sites", ",".join(selected_sites)]

        collect_warnings: list[str] = []
        collect_summary_holder: dict[str, Any] = {}

        def _on_collect_line(line: str, *, _key: str = key) -> None:
            if _key != "collect":
                return
            parsed = parse_agent_line(line)
            if parsed is None:
                return
            if parsed.get("type") == "warning":
                message = parsed.get("message")
                if message and message not in collect_warnings:
                    collect_warnings.append(message)
            elif parsed.get("type") == "summary":
                collect_summary_holder["summary"] = parsed.get("summary")

        try:
            code = _run_logged_subprocess(
                [PYTHON, str(SRC / script), *extra_args],
                env=env,
                log=_log,
                on_line=_on_collect_line if key == "collect" else None,
            )
        except ScanCancelled:
            _step(key, "failed")
            _log("!! הסריקה בוטלה")
            error = "הסריקה בוטלה על ידי המשתמש"
            break

        for message in collect_warnings:
            if message not in warnings:
                warnings.append(message)
        if collect_summary_holder.get("summary"):
            collection_summary = collect_summary_holder["summary"]

        if code != 0:
            _step(key, "failed")
            _log(f"!! {name} נכשל (קוד {code})")
            if key in SEARCH_CRITICAL_STEPS:
                error = f"השלב '{name}' נכשל"
                break
        else:
            _step(key, "success")

    # All historical matches for this CV (past + current scans).
    match_count = len(db.get_cv_matches(cv_id, latest_only=False, db_path=cv_db))
    summary_payload: dict[str, Any] = {
        "matches": match_count,
        "domains": cleaned_domains,
    }
    if collection_summary:
        summary_payload["collection"] = collection_summary
    if warnings:
        summary_payload["warnings"] = warnings
    summary = json.dumps(summary_payload, ensure_ascii=False)

    if error:
        db.finish_scan(
            scan_id, db.SCAN_FAILED, summary=summary, error_message=error, db_path=cv_db
        )
    else:
        db.finish_scan(scan_id, db.SCAN_SUCCESS, summary=summary, db_path=cv_db)
        db.set_cv_last_scan(cv_id, db_path=db_path)

    scan_record = db.get_scan(scan_id, db_path=cv_db) or {}
    if warnings:
        scan_record["warnings"] = warnings
    if collection_summary:
        scan_record["collection"] = collection_summary
    scan_record["domains"] = cleaned_domains
    scan_record["match_count"] = match_count
    return scan_record


def run_scan(
    cv_id: str,
    *,
    skip_collect: bool = False,
    skip_enrich: bool = False,
    job_sites: list[str] | None = None,
    domains: list[str] | None = None,
    reset_jobs: bool = False,
    log: Callable[[str], None] | None = None,
    set_step_status: Callable[[str, str], None] | None = None,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Run the full agent pipeline for a single CV, recorded as a cv_scan.

    Returns the finished scan record. The heavy steps run as subprocesses with
    AGENT_CV_ID / AGENT_SCAN_ID set so all generated data is stored under this
    CV id and never mixes with another CV's results.

    By default prior jobs/matches are retained (incremental). Pass
    ``reset_jobs=True`` only when an explicit wipe is requested.
    """
    import os

    from job_boards import normalize_job_board_ids

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
    if reset_jobs:
        db.reset_cv_job_pool(cv_id)
    env = {**os.environ, "AGENT_CV_ID": cv_id, "AGENT_SCAN_ID": str(scan_id)}

    try:
        selected_sites = normalize_job_board_ids(job_sites)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    cleaned_domains = [str(d).strip() for d in (domains or []) if str(d).strip()]
    error: str | None = None
    warnings: list[str] = []
    collection_summary: dict[str, Any] | None = None
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

        extra_args = list(extra)
        if key == "collect":
            if cleaned_domains:
                extra_args = [*extra_args, "--domains", ",".join(cleaned_domains)]
            if selected_sites:
                extra_args = [*extra_args, "--sites", ",".join(selected_sites)]

        proc = subprocess.Popen(
            [PYTHON, str(SRC / script), *extra_args],
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
                if key == "collect":
                    parsed = parse_agent_line(line)
                    if parsed is None:
                        continue
                    if parsed.get("type") == "warning":
                        message = parsed.get("message")
                        if message and message not in warnings:
                            warnings.append(message)
                    elif parsed.get("type") == "summary":
                        collection_summary = parsed.get("summary")
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

    match_count = len(
        db.get_cv_matches(cv_id, latest_only=False, db_path=cv_db)
    )
    summary_payload: dict[str, Any] = {"matches": match_count}
    if cleaned_domains:
        summary_payload["domains"] = cleaned_domains
    if collection_summary:
        summary_payload["collection"] = collection_summary
    if warnings:
        summary_payload["warnings"] = warnings
    summary = json.dumps(summary_payload, ensure_ascii=False)

    if error:
        db.finish_scan(
            scan_id, db.SCAN_FAILED, summary=summary, error_message=error, db_path=cv_db
        )
    else:
        db.finish_scan(scan_id, db.SCAN_SUCCESS, summary=summary, db_path=cv_db)
        db.set_cv_last_scan(cv_id, db_path=db_path)

    scan_record = db.get_scan(scan_id, db_path=cv_db) or {}
    if warnings:
        scan_record["warnings"] = warnings
    if collection_summary:
        scan_record["collection"] = collection_summary
    return scan_record


def _cv_text_from_profile(profile: dict[str, Any]) -> str:
    raw = str(profile.get("raw_text") or "").strip()
    if raw:
        return raw
    sections = profile.get("sections")
    if isinstance(sections, dict):
        parts = [str(v).strip() for v in sections.values() if v]
        combined = "\n\n".join(p for p in parts if p)
        if combined:
            return combined
    return ""


def _load_parsed_cv_text(cv_id: str) -> str:
    profile_path = cv_data_dir(cv_id) / "cv_profile.json"
    if not profile_path.exists():
        return ""
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    return _cv_text_from_profile(profile)


def _run_logged_subprocess(
    args: list[str],
    *,
    env: dict[str, str],
    log: Callable[[str], None] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> int:
    """Run a subprocess with live logging; abort early if the scan is cancelled."""
    if is_cancelled():
        raise ScanCancelled("scan cancelled")

    proc = subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    register_process(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if is_cancelled():
                _terminate_proc(proc)
                raise ScanCancelled("scan cancelled")
            line = line.rstrip()
            if line:
                if log is not None:
                    log(line)
                if on_line is not None:
                    on_line(line)
        return proc.wait()
    finally:
        unregister_process(proc)


def _terminate_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except OSError:
            pass


def _parse_all_user_cvs(
    user_id: str,
    *,
    log: Callable[[str], None] | None = None,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> list[str]:
    """Parse every active CV for a user and return their text contents."""
    import os

    texts: list[str] = []
    active_cvs = db.list_active_cvs_for_user(user_id, db_path=db_path)
    if not active_cvs:
        raise ValueError("no active CV files uploaded")

    for cv in active_cvs:
        if is_cancelled():
            raise ScanCancelled("scan cancelled")
        cv_id = cv["id"]
        if log:
            log(f"-- מנתח: {cv.get('display_name') or cv.get('file_name')}")
        env = {**os.environ, "AGENT_CV_ID": cv_id}
        # Avoid leaking workspace scope into per-CV parse.
        env.pop("AGENT_USER_ID", None)
        code = _run_logged_subprocess(
            [PYTHON, str(SRC / "parse_cv.py"), "--yes"],
            env=env,
            log=log,
        )
        if code != 0:
            raise RuntimeError(f"parse failed for CV {cv_id}")
        sync_parsed_profile(cv_id, db_path=db_path)
        text = _load_parsed_cv_text(cv_id)
        if text:
            texts.append(text)
    if not texts:
        raise ValueError("could not extract text from uploaded CV files")
    return texts


def _aggregate_user_profile(
    user_id: str,
    cv_texts: list[str],
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    workspace = user_data_dir(user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    master = aggregate_and_save(
        cv_texts,
        master_path=user_master_profile_path(user_id),
        cv_profile_path=user_cv_profile_path(user_id),
    )
    from cv_domain import refine_profile
    from universal_profile import (
        apply_universal_profile_to_cv,
        build_universal_profile_fallback,
        extract_universal_profile,
    )

    cv_profile = refine_profile(
        json.loads(user_cv_profile_path(user_id).read_text(encoding="utf-8"))
    )
    raw_text = _cv_text_from_profile(cv_profile)
    try:
        universal = extract_universal_profile(raw_text, cv_profile, use_ai=True)
    except Exception:  # noqa: BLE001
        universal = build_universal_profile_fallback(cv_profile)
    cv_profile = refine_profile(apply_universal_profile_to_cv(cv_profile, universal))
    user_cv_profile_path(user_id).write_text(
        json.dumps(cv_profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    save_profile_for_user(user_id, cv_profile)
    if log:
        log(
            f"אוחדו {master.source_cv_count} קבצי קורות חיים "
            f"({master.aggregated_with}) — {len(master.work_experience)} תפקידים, "
            f"{sum(len(v) for v in master.master_skills.values())} מיומנויות"
        )


def run_user_scan(
    user_id: str = db.DEFAULT_USER_ID,
    *,
    skip_collect: bool = False,
    skip_enrich: bool = False,
    job_sites: list[str] | None = None,
    log: Callable[[str], None] | None = None,
    set_step_status: Callable[[str, str], None] | None = None,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Run the unified agent pipeline across all of a user's uploaded CVs."""
    import os

    from job_boards import normalize_job_board_ids

    workspace_cv_id = db.WORKSPACE_CV_ID
    user_db = user_db_path(user_id)
    db.init_db(user_db)
    user_data_dir(user_id).mkdir(parents=True, exist_ok=True)

    active_cvs = db.list_active_cvs_for_user(user_id, db_path=db_path)
    if not active_cvs:
        raise ValueError("no active CV files uploaded")

    def _log(line: str) -> None:
        if log is not None:
            log(line)

    def _step(key: str, status: str) -> None:
        if set_step_status is not None:
            set_step_status(key, status)

    scan_id = db.create_scan(workspace_cv_id, db_path=user_db)
    # Keep prior workspace jobs/matches; collection dedupes incrementally.
    env = {
        **os.environ,
        "AGENT_USER_ID": user_id,
        "AGENT_SCAN_ID": str(scan_id),
    }
    # Clear per-CV scope so subprocesses use the user workspace paths.
    env.pop("AGENT_CV_ID", None)

    try:
        selected_sites = normalize_job_board_ids(job_sites)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    error: str | None = None
    warnings: list[str] = []
    collection_summary: dict[str, Any] | None = None
    cv_texts: list[str] | None = None

    for key, name, script, extra in USER_SCAN_STEPS:
        if is_cancelled():
            error = "הסריקה בוטלה על ידי המשתמש"
            _log("!! הסריקה בוטלה")
            break

        skipped = (key == "collect" and skip_collect) or (
            key == "enrich" and skip_enrich
        )
        if skipped:
            _step(key, "skipped")
            _log(f"-- מדלג על: {name}")
            continue

        _step(key, "running")
        _log(f">> {name}")

        if key == "parse_cvs":
            try:
                cv_texts = _parse_all_user_cvs(user_id, log=_log, db_path=db_path)
            except ScanCancelled:
                _step(key, "failed")
                _log("!! הסריקה בוטלה")
                error = "הסריקה בוטלה על ידי המשתמש"
                break
            except (ValueError, RuntimeError) as exc:
                _step(key, "failed")
                _log(f"!! {name} נכשל: {exc}")
                error = f"השלב '{name}' נכשל: {exc}"
                break
            _step(key, "success")
            continue

        if key == "aggregate":
            try:
                if is_cancelled():
                    raise ScanCancelled("scan cancelled")
                texts = cv_texts or _collect_active_cv_texts(user_id, db_path=db_path)
                _aggregate_user_profile(user_id, texts, log=_log)
            except ScanCancelled:
                _step(key, "failed")
                _log("!! הסריקה בוטלה")
                error = "הסריקה בוטלה על ידי המשתמש"
                break
            except Exception as exc:  # noqa: BLE001
                _step(key, "failed")
                _log(f"!! {name} נכשל: {exc}")
                error = f"השלב '{name}' נכשל: {exc}"
                break
            _step(key, "success")
            continue

        extra_args = list(extra)
        if key == "collect" and selected_sites:
            extra_args = [*extra_args, "--sites", ",".join(selected_sites)]

        collect_warnings: list[str] = []
        collect_summary_holder: dict[str, Any] = {}

        def _on_collect_line(line: str, *, _key: str = key) -> None:
            if _key != "collect":
                return
            parsed = parse_agent_line(line)
            if parsed is None:
                return
            if parsed.get("type") == "warning":
                message = parsed.get("message")
                if message and message not in collect_warnings:
                    collect_warnings.append(message)
            elif parsed.get("type") == "summary":
                collect_summary_holder["summary"] = parsed.get("summary")

        try:
            code = _run_logged_subprocess(
                [PYTHON, str(SRC / script), *extra_args],
                env=env,
                log=_log,
                on_line=_on_collect_line if key == "collect" else None,
            )
        except ScanCancelled:
            _step(key, "failed")
            _log("!! הסריקה בוטלה")
            error = "הסריקה בוטלה על ידי המשתמש"
            break

        for message in collect_warnings:
            if message not in warnings:
                warnings.append(message)
        if collect_summary_holder.get("summary"):
            collection_summary = collect_summary_holder["summary"]

        if code != 0:
            _step(key, "failed")
            _log(f"!! {name} נכשל (קוד {code})")
            if key in USER_CRITICAL_STEPS:
                error = f"השלב '{name}' נכשל"
                break
        else:
            _step(key, "success")

    match_count = len(
        db.get_cv_matches(workspace_cv_id, latest_only=False, db_path=user_db)
    )
    summary_payload: dict[str, Any] = {
        "matches": match_count,
        "cv_count": len(active_cvs),
        "user_id": user_id,
    }
    if collection_summary:
        summary_payload["collection"] = collection_summary
    if warnings:
        summary_payload["warnings"] = warnings
    summary = json.dumps(summary_payload, ensure_ascii=False)

    now = _utc_now()
    if error:
        db.finish_scan(
            scan_id, db.SCAN_FAILED, summary=summary, error_message=error, db_path=user_db
        )
    else:
        db.finish_scan(scan_id, db.SCAN_SUCCESS, summary=summary, db_path=user_db)
        for cv in active_cvs:
            db.set_cv_last_scan(cv["id"], when=now, db_path=db_path)

    scan_record = db.get_scan(scan_id, db_path=user_db) or {}
    if warnings:
        scan_record["warnings"] = warnings
    if collection_summary:
        scan_record["collection"] = collection_summary
    scan_record["user_id"] = user_id
    scan_record["cv_count"] = len(active_cvs)
    return scan_record


def _collect_active_cv_texts(
    user_id: str,
    *,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> list[str]:
    texts: list[str] = []
    for cv in db.list_active_cvs_for_user(user_id, db_path=db_path):
        text = _load_parsed_cv_text(cv["id"])
        if text:
            texts.append(text)
    if not texts:
        raise ValueError("could not extract text from uploaded CV files")
    return texts


_WORKSPACE_ARTIFACTS = (
    "master_profile.json",
    "cv_profile.json",
    "profile.json",
    "ai_roles.json",
    "ai_matching_strategy.json",
    "pipeline_state.json",
    "scan_state.json",
)


def reset_user_results(
    user_id: str = db.DEFAULT_USER_ID,
    *,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Clear workspace match/scan results; keep uploaded CV files."""
    from scan_control import clear_scan_state

    user_db = user_db_path(user_id)
    cleared_jobs = False
    if user_db.exists():
        db.reset_cv_job_pool(db.WORKSPACE_CV_ID, db_path=user_db)
        with db.get_connection(user_db) as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "cv_scans" in tables:
                conn.execute("DELETE FROM cv_scans")
            conn.commit()
        cleared_jobs = True

    for cv in db.list_cvs(user_id=user_id, db_path=db_path):
        db.update_cv(cv["id"], {"last_scan_at": None}, db_path=db_path)

    clear_scan_state(user_id)
    return {
        "reset": "results",
        "user_id": user_id,
        "cleared_workspace_db": cleared_jobs,
    }


def reset_user_files(
    user_id: str = db.DEFAULT_USER_ID,
    *,
    db_path: Path = db.REGISTRY_DB_PATH,
) -> dict[str, Any]:
    """Delete all uploaded CVs for a user and clear workspace results/profiles."""
    cvs = db.list_cvs(user_id=user_id, db_path=db_path)
    deleted_ids: list[str] = []
    for cv in cvs:
        delete_cv(cv["id"], db_path=db_path)
        deleted_ids.append(cv["id"])

    results = reset_user_results(user_id, db_path=db_path)

    workspace = user_data_dir(user_id)
    removed_artifacts: list[str] = []
    if workspace.exists():
        for name in _WORKSPACE_ARTIFACTS:
            path = workspace / name
            if path.exists():
                try:
                    path.unlink()
                    removed_artifacts.append(name)
                except OSError:
                    pass
        cache_dir = workspace / "ai_cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
            removed_artifacts.append("ai_cache/")

    return {
        "reset": "files",
        "user_id": user_id,
        "deleted_cv_ids": deleted_ids,
        "deleted_count": len(deleted_ids),
        "removed_artifacts": removed_artifacts,
        "results": results,
    }