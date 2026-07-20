"""HTTP API for the AI Job Agent — lets the web client run the pipeline.

Run with:
    python src/api_server.py            # starts on http://localhost:8000 (or API_PORT from .env)
    python src/api_server.py --port 8001

Auth:
    POST   /api/auth/register               create account (email + password) → JWT
    POST   /api/auth/login                  login → JWT
    GET    /api/auth/me                     current user (Bearer token)

Multi-CV endpoints (each CV has isolated data; require Bearer JWT):
    GET    /cvs                             list uploaded CVs + metadata
    POST   /cvs/upload                      upload a CV (dedup by content hash)
    GET    /cvs/{cv_id}                     one CV + its latest scan
    DELETE /cvs/{cv_id}                     delete a CV and all its data
    POST   /cvs/reset                       delete all CVs + clear workspace
    POST   /cvs/{cv_id}/analyze             parse CV + suggest job domains/roles
    POST   /cvs/{cv_id}/search              scrape jobs for selected domains (incremental)
    POST   /cvs/{cv_id}/run-agent           run full per-CV pipeline (incremental)
    GET    /cvs/{cv_id}/scan-status         live scan progress + log tail
    GET    /cvs/{cv_id}/matches             CV's job matches (all scans by default)
           (query: latest, min_score, sort_by=date|score|site, order=asc|desc)
    POST   /jobs/match                        run agent across all uploaded CVs (aggregated)
    GET    /jobs/match-status                 live workspace scan progress
    GET    /jobs/matches                      workspace job matches
           (query: latest, min_score, sort_by=date|score|site, order=asc|desc)
    POST   /jobs/matches/reset                clear workspace match results
    PATCH  /jobs/matches/{id}/status          set application status for workspace match
    POST   /jobs/{job_id}/tailor-cv           tailor CV from aggregated profile
    PATCH  /cvs/{cv_id}/matches/{id}/status set the application status for a match
    POST   /cvs/{cv_id}/jobs/{job_id}/tailor-cv  generate ATS-tailored CV markdown for a job
           (?regenerate=true deep-scans original CVs + ATS gaps; score-guarded)
    GET    /cvs/{cv_id}/jobs/{job_id}/tailored-cv/download-pdf  download tailored CV as PDF
    POST   /cvs/{cv_id}/jobs/{job_id}/apply          start automated job application
    GET    /cvs/{cv_id}/job-applications/{id}        application attempt details + log
    GET    /cvs/{cv_id}/jobs/{job_id}/application-status  latest application status
    POST   /cvs/{cv_id}/job-applications/{id}/retry  retry a failed application
    PUT    /cvs/{cv_id}/site-sessions/linkedin       import LinkedIn browser cookies
    GET    /cvs/{cv_id}/site-credentials             per-CV login settings (no passwords)
    PUT    /cvs/{cv_id}/site-credentials             save LinkedIn/Drushim login for auto-apply

Legacy (single global CV) endpoints, kept for backward compatibility:
    GET  /api/health            server + pipeline/scan availability
    GET  /api/jobs              jobs with match scores (query: min_score, all)
    POST /api/pipeline/run      start the legacy pipeline in the background
    GET  /api/pipeline/status   current pipeline progress + log tail
    GET  /api/cv                info about the legacy global CV
    POST /api/cv                upload/replace the legacy global CV (resumes/cv.*)
"""

import argparse
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
import cv_service
import db
from application_service import ApplicationError, get_application_for_cv, get_job_application_status, public_application, start_application
from application_worker import enqueue_application, is_application_active
from collection_report import parse_agent_line
from config import API_HOST, API_PORT, CV_PROFILE_PATH, DATA_DIR, PROJECT_ROOT, RESUMES_DIR, cv_db_path, user_db_path
from job_boards import list_job_boards, normalize_job_board_ids
from pdf_generator_service import PdfGeneratorError, generate_tailored_cv_pdf
from scan_control import (
    begin_scan,
    clear_scan_state,
    is_cancelled,
    load_scan_state,
    mark_interrupted_if_stale,
    request_cancel,
    save_scan_state,
)
from site_auth import import_linkedin_storage_state, linkedin_storage_state_path
from site_credentials import public_site_credentials, update_site_credentials
from tailor_cv_service import (
    TailorCvError,
    extract_cv_markdown_for_copy,
    load_saved_tailored_cv,
    tailor_cv_for_job,
)

SRC = PROJECT_ROOT / "src"
PYTHON = sys.executable

ALLOWED_CV_EXTENSIONS = cv_service.ALLOWED_CV_EXTENSIONS
MAX_CV_SIZE = cv_service.MAX_CV_SIZE

app = FastAPI(title="AI Job Agent API")

# Mark any scan left "running" by a previous process as interrupted.
try:
    mark_interrupted_if_stale(db.DEFAULT_USER_ID)
except Exception:  # noqa: BLE001
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local tool — the web client runs on a different port
    allow_methods=["*"],
    allow_headers=["*"],
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_owned_cv(cv_id: str, user: dict) -> dict:
    """Return the CV row if it belongs to ``user``; otherwise 404."""
    # Resolve REGISTRY_DB_PATH at call time so pytest monkeypatches apply.
    cv = db.get_cv(cv_id, db_path=db.REGISTRY_DB_PATH)
    if cv is None or cv.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")
    return cv


def _parse_match_sort(sort_by: str | None, order: str | None) -> tuple[str | None, str | None]:
    """Validate sort query params (None keeps DB defaults)."""
    if sort_by is None and order is None:
        return None, None
    key = (sort_by or "score").strip().lower()
    if key not in {"date", "score", "site"}:
        raise HTTPException(
            status_code=400,
            detail="sort_by חייב להיות אחד מ: date, score, site",
        )
    direction = (order or ("desc" if key in {"score", "date"} else "asc")).strip().lower()
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="order חייב להיות asc או desc")
    return key, direction


# ---------------------------------------------------------------------------
# Auth models + endpoints
# ---------------------------------------------------------------------------


class AuthRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)


class AuthLoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register")
def auth_register(req: AuthRegisterRequest):
    db.ensure_multi_cv_storage()
    try:
        user = auth.register_user(req.email, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = auth.create_access_token(user["id"], user["email"])
    return {"access_token": token, "token_type": "bearer", "user": auth.public_user(user)}


@app.post("/api/auth/login")
def auth_login(req: AuthLoginRequest):
    db.ensure_multi_cv_storage()
    user = auth.authenticate_user(req.email, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="אימייל או סיסמה שגויים")
    token = auth.create_access_token(user["id"], user["email"] or "")
    return {"access_token": token, "token_type": "bearer", "user": auth.public_user(user)}


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(auth.get_current_user)):
    return {"user": auth.public_user(user)}


# ---------------------------------------------------------------------------
# Pipeline runner (background thread; one run at a time)
# ---------------------------------------------------------------------------

PIPELINE_STEPS = [
    ("parse_cv", "ניתוח קורות החיים", "parse_cv.py", ["--yes"]),
    ("analyze_roles", "בניית אסטרטגיית תפקידים (AI)", "analyze_roles.py", []),
    ("collect", "איסוף משרות מדרושים ולינקדאין", "collect_jobs.py", []),
    ("enrich", "שליפת תיאורי משרה מלאים", "enrich_jobs.py", []),
    ("match", "חישוב ציוני התאמה", "match_jobs.py", []),
]

# Steps that abort the pipeline when they fail (same behavior as run_all.py).
CRITICAL_STEPS = {"parse_cv", "analyze_roles", "match"}

_state_lock = threading.Lock()
_pipeline_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "steps": [],
    "log": [],
}


def _append_log(line: str) -> None:
    with _state_lock:
        log = _pipeline_state["log"]
        log.append(line)
        if len(log) > 300:
            del log[: len(log) - 300]


def _set_step_status(key: str, status: str) -> None:
    with _state_lock:
        for step in _pipeline_state["steps"]:
            if step["key"] == key:
                step["status"] = status


def _run_pipeline(skip_collect: bool, skip_enrich: bool) -> None:
    try:
        for key, name, script, extra in PIPELINE_STEPS:
            with _state_lock:
                skipped = (key == "collect" and skip_collect) or (
                    key == "enrich" and skip_enrich
                )
            if skipped:
                _set_step_status(key, "skipped")
                _append_log(f"-- מדלג על: {name}")
                continue

            _set_step_status(key, "running")
            _append_log(f">> {name}")

            proc = subprocess.Popen(
                [PYTHON, str(SRC / script), *extra],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    _append_log(line)
            code = proc.wait()

            if code != 0:
                _set_step_status(key, "failed")
                _append_log(f"!! {name} נכשל (קוד {code})")
                if key in CRITICAL_STEPS:
                    with _state_lock:
                        _pipeline_state["error"] = f"השלב '{name}' נכשל"
                    return
            else:
                _set_step_status(key, "success")
    except Exception as exc:  # noqa: BLE001 — surface any crash to the client
        with _state_lock:
            _pipeline_state["error"] = str(exc)
        _append_log(f"!! שגיאה: {exc}")
    finally:
        with _state_lock:
            _pipeline_state["running"] = False
            _pipeline_state["finished_at"] = _utc_now()


class RunRequest(BaseModel):
    skip_collect: bool = False
    skip_enrich: bool = False


class RunAgentRequest(BaseModel):
    skip_collect: bool = False
    skip_enrich: bool = False
    job_sites: list[str] | None = None
    domains: list[str] | None = None


class SearchJobsRequest(BaseModel):
    domains: list[str] = Field(default_factory=list)
    skip_enrich: bool = False
    job_sites: list[str] | None = None

    # Accept either {"domains": [...]} or {"selected_domains": [...]}
    selected_domains: list[str] | None = None

    def resolved_domains(self) -> list[str]:
        raw = self.domains or self.selected_domains or []
        return [str(d).strip() for d in raw if str(d).strip()]


@app.post("/api/pipeline/run")
def run_pipeline(req: RunRequest):
    with _state_lock:
        if _pipeline_state["running"]:
            raise HTTPException(status_code=409, detail="הפייפליין כבר רץ")
        _pipeline_state.update(
            {
                "running": True,
                "started_at": _utc_now(),
                "finished_at": None,
                "error": None,
                "log": [],
                "steps": [
                    {"key": key, "name": name, "status": "pending"}
                    for key, name, _, _ in PIPELINE_STEPS
                ],
            }
        )
    thread = threading.Thread(
        target=_run_pipeline,
        args=(req.skip_collect, req.skip_enrich),
        daemon=True,
    )
    thread.start()
    return {"started": True}


@app.get("/api/pipeline/status")
def pipeline_status():
    with _state_lock:
        return {
            "running": _pipeline_state["running"],
            "started_at": _pipeline_state["started_at"],
            "finished_at": _pipeline_state["finished_at"],
            "error": _pipeline_state["error"],
            "steps": [dict(s) for s in _pipeline_state["steps"]],
            "log": list(_pipeline_state["log"][-40:]),
        }


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@app.get("/api/jobs")
def get_jobs(min_score: int = 55, all: bool = False):
    db.init_db()
    jobs = db.get_jobs(min_score=None if all else min_score, exclude_handled=False)
    fields = [
        "id", "title", "company", "location", "job_url", "source",
        "match_score", "match_reason", "match_category",
        "ai_decision", "ai_strengths", "ai_missing_skills", "ai_explanation",
        "matched_at", "first_seen_at", "application_status",
    ]
    return {"jobs": [{f: job.get(f) for f in fields} for job in jobs]}


# ---------------------------------------------------------------------------
# CV upload / info
# ---------------------------------------------------------------------------


@app.get("/api/cv")
def get_cv_info():
    current = None
    if RESUMES_DIR.exists():
        for path in sorted(RESUMES_DIR.iterdir()):
            if path.is_file() and path.stem.lower() == "cv":
                stat = path.stat()
                current = {
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
                break

    analyzed = None
    if CV_PROFILE_PATH.exists():
        try:
            profile = json.loads(CV_PROFILE_PATH.read_text(encoding="utf-8"))
            analyzed = {
                "name": profile.get("contact", {}).get("name"),
                "parsed_at": profile.get("parsed_at") or profile.get("generated_at"),
                "skills_count": sum(
                    len(v) for v in (profile.get("skills") or {}).values()
                ) if isinstance(profile.get("skills"), dict) else None,
            }
        except (json.JSONDecodeError, OSError):
            pass

    return {"cv": current, "analysis": analyzed}


@app.post("/api/cv")
async def upload_cv(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_CV_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"סוג קובץ לא נתמך: {ext}")

    data = await file.read()
    if len(data) > MAX_CV_SIZE:
        raise HTTPException(status_code=400, detail="הקובץ גדול מדי (מקסימום 15MB)")
    if not data:
        raise HTTPException(status_code=400, detail="הקובץ ריק")

    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    # Remove previous cv.* files so the agent has a single unambiguous resume.
    for path in RESUMES_DIR.iterdir():
        if path.is_file() and path.stem.lower() == "cv":
            path.unlink()

    target = RESUMES_DIR / f"cv{ext}"
    target.write_bytes(data)
    return {"saved": True, "name": target.name, "size": len(data)}


# ---------------------------------------------------------------------------
# Multi-CV management
# ---------------------------------------------------------------------------

# Per-CV scan runner (one scan at a time; separate from the legacy pipeline).
_scan_lock = threading.Lock()
_scan_state: dict = {
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
    "steps": [],
    "log": [],
    "current_detail": None,
}


def _parse_scan_summary(summary: str | None) -> dict:
    if not summary:
        return {}
    try:
        data = json.loads(summary)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _persist_scan_state() -> None:
    """Mirror in-memory scan state to disk so refresh can resume the UI."""
    with _scan_lock:
        user_id = _scan_state.get("user_id") or db.DEFAULT_USER_ID
        snapshot = {
            "running": _scan_state["running"],
            "mode": _scan_state.get("mode"),
            "cv_id": _scan_state.get("cv_id"),
            "user_id": user_id,
            "scan_id": _scan_state.get("scan_id"),
            "started_at": _scan_state.get("started_at"),
            "finished_at": _scan_state.get("finished_at"),
            "error": _scan_state.get("error"),
            "warnings": list(_scan_state.get("warnings") or []),
            "collection": _scan_state.get("collection"),
            "steps": [dict(s) for s in (_scan_state.get("steps") or [])],
            "log": list(_scan_state.get("log") or [])[-80:],
            "current_detail": _scan_state.get("current_detail"),
            "cancelled": is_cancelled(),
        }
    try:
        save_scan_state(user_id, snapshot)
    except OSError:
        pass


def _scan_log(line: str) -> None:
    with _scan_lock:
        log = _scan_state["log"]
        log.append(line)
        if len(log) > 300:
            del log[: len(log) - 300]
        stripped = line.strip()
        if stripped and not stripped.startswith(">>") and not stripped.startswith("--"):
            _scan_state["current_detail"] = stripped
        parsed = parse_agent_line(stripped)
        if parsed is not None:
            if parsed.get("type") == "warning":
                message = parsed.get("message")
                if message and message not in _scan_state["warnings"]:
                    _scan_state["warnings"].append(message)
            elif parsed.get("type") == "summary":
                _scan_state["collection"] = parsed.get("summary")
    _persist_scan_state()


def _scan_set_step(key: str, status: str) -> None:
    with _scan_lock:
        for step in _scan_state["steps"]:
            if step["key"] == key:
                step["status"] = status
                if status == "running":
                    _scan_state["current_detail"] = step["name"]
                break
    _persist_scan_state()


def _running_step_key_from_steps(steps: list[dict]) -> str | None:
    for step in steps:
        if step["status"] == "running":
            return step["key"]
    return None


def _scan_running_step_key() -> str | None:
    with _scan_lock:
        return _running_step_key_from_steps(_scan_state["steps"])


def _run_scan_thread(
    cv_id: str,
    skip_collect: bool,
    skip_enrich: bool,
    job_sites: list[str] | None,
    domains: list[str] | None = None,
) -> None:
    error: str | None = None
    try:
        scan = cv_service.run_scan(
            cv_id,
            skip_collect=skip_collect,
            skip_enrich=skip_enrich,
            job_sites=job_sites,
            domains=domains,
            log=_scan_log,
            set_step_status=_scan_set_step,
        )
        if scan.get("status") == db.SCAN_FAILED:
            error = scan.get("error_message") or "הסריקה נכשלה"
        elif scan.get("status") == db.SCAN_STOPPED:
            error = None
        with _scan_lock:
            scan_warnings = scan.get("warnings") or []
            for message in scan_warnings:
                if message and message not in _scan_state["warnings"]:
                    _scan_state["warnings"].append(message)
            if scan.get("collection"):
                _scan_state["collection"] = scan.get("collection")
    except Exception as exc:  # noqa: BLE001 — surface any crash to the client
        error = str(exc)
        _scan_log(f"!! שגיאה: {exc}")
    finally:
        with _scan_lock:
            _scan_state["running"] = False
            _scan_state["finished_at"] = _utc_now()
            if error is None and is_cancelled():
                _scan_state["current_detail"] = "הסריקה נעצרה"
            _scan_state["error"] = error
        _persist_scan_state()


def _run_search_thread(
    cv_id: str,
    domains: list[str],
    skip_enrich: bool,
    job_sites: list[str] | None,
) -> None:
    error: str | None = None
    try:
        scan = cv_service.run_search(
            cv_id,
            domains=domains,
            skip_enrich=skip_enrich,
            job_sites=job_sites,
            log=_scan_log,
            set_step_status=_scan_set_step,
        )
        if scan.get("status") == db.SCAN_FAILED:
            error = scan.get("error_message") or "הסריקה נכשלה"
        elif scan.get("status") == db.SCAN_STOPPED:
            error = None
        with _scan_lock:
            scan_warnings = scan.get("warnings") or []
            for message in scan_warnings:
                if message and message not in _scan_state["warnings"]:
                    _scan_state["warnings"].append(message)
            if scan.get("collection"):
                _scan_state["collection"] = scan.get("collection")
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        _scan_log(f"!! שגיאה: {exc}")
    finally:
        with _scan_lock:
            _scan_state["running"] = False
            _scan_state["finished_at"] = _utc_now()
            if error is None and is_cancelled():
                _scan_state["current_detail"] = "הסריקה נעצרה"
            _scan_state["error"] = error
        _persist_scan_state()


def _cv_match_count(cv_id: str) -> int:
    cv_db = cv_db_path(cv_id)
    if not cv_db.exists():
        return 0
    try:
        db.ensure_jobs_schema(cv_db)
        return len(db.get_cv_matches(cv_id, latest_only=False, db_path=cv_db))
    except Exception:  # noqa: BLE001
        return 0


def _run_user_scan_thread(
    user_id: str,
    skip_collect: bool,
    skip_enrich: bool,
    job_sites: list[str] | None,
) -> None:
    error: str | None = None
    try:
        scan = cv_service.run_user_scan(
            user_id,
            skip_collect=skip_collect,
            skip_enrich=skip_enrich,
            job_sites=job_sites,
            log=_scan_log,
            set_step_status=_scan_set_step,
        )
        if scan.get("status") == db.SCAN_FAILED:
            error = scan.get("error_message") or "הסריקה נכשלה"
        elif scan.get("status") == db.SCAN_STOPPED:
            error = None
        with _scan_lock:
            scan_warnings = scan.get("warnings") or []
            for message in scan_warnings:
                if message and message not in _scan_state["warnings"]:
                    _scan_state["warnings"].append(message)
            if scan.get("collection"):
                _scan_state["collection"] = scan.get("collection")
    except Exception as exc:  # noqa: BLE001 — surface any crash to the client
        error = str(exc)
        _scan_log(f"!! שגיאה: {exc}")
    finally:
        with _scan_lock:
            _scan_state["running"] = False
            _scan_state["finished_at"] = _utc_now()
            if error is None and is_cancelled():
                _scan_state["current_detail"] = "הסריקה נעצרה"
            _scan_state["error"] = error
        _persist_scan_state()


def _workspace_match_count(user_id: str = db.DEFAULT_USER_ID) -> int:
    workspace_db = user_db_path(user_id)
    if not workspace_db.exists():
        return 0
    try:
        # Repair empty/partial DBs created by an earlier get_connection without init_db.
        db.ensure_jobs_schema(workspace_db)
        return len(
            db.get_cv_matches(
                db.WORKSPACE_CV_ID,
                latest_only=False,
                db_path=workspace_db,
            )
        )
    except Exception:  # noqa: BLE001 — listing CVs must not fail because of match counts
        return 0


def _start_user_scan(req: RunAgentRequest, user_id: str = db.DEFAULT_USER_ID) -> dict:
    active = db.list_active_cvs_for_user(user_id)
    if not active:
        raise HTTPException(status_code=400, detail="יש להעלות לפחות קובץ קורות חיים אחד")

    try:
        normalize_job_board_ids(req.job_sites)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _scan_lock:
        if _scan_state["running"]:
            raise HTTPException(status_code=409, detail="סריקה אחרת כבר רצה")
        begin_scan()
        _scan_state.update(
            {
                "running": True,
                "mode": "user",
                "cv_id": None,
                "user_id": user_id,
                "scan_id": None,
                "started_at": _utc_now(),
                "finished_at": None,
                "error": None,
                "warnings": [],
                "collection": None,
                "log": [],
                "current_detail": "מתחיל סריקה…",
                "steps": [
                    {"key": key, "name": name, "status": "pending"}
                    for key, name, _, _ in cv_service.USER_SCAN_STEPS
                ],
            }
        )
    _persist_scan_state()

    thread = threading.Thread(
        target=_run_user_scan_thread,
        args=(user_id, req.skip_collect, req.skip_enrich, req.job_sites),
        daemon=True,
    )
    thread.start()
    return {
        "started": True,
        "user_id": user_id,
        "cv_count": len(active),
    }


def _stop_user_scan(user_id: str = db.DEFAULT_USER_ID) -> dict:
    with _scan_lock:
        running = bool(_scan_state["running"]) and _scan_state.get("user_id") == user_id
        if not running:
            # Also allow stop if persisted state still says running (rare race).
            persisted = load_scan_state(user_id) or {}
            if not persisted.get("running"):
                raise HTTPException(status_code=409, detail="אין סריקה פעילה לעצור")
        _scan_state["current_detail"] = "עוצר סריקה…"
        if "!! מבטל סריקה" not in (_scan_state.get("log") or []):
            _scan_state.setdefault("log", []).append("!! מבטל סריקה לפי בקשת המשתמש")
    request_cancel()
    _persist_scan_state()
    return {"stopping": True, "user_id": user_id}


def _user_scan_status(user_id: str = db.DEFAULT_USER_ID) -> dict:
    workspace_db = user_db_path(user_id)
    with _scan_lock:
        memory_matches = (
            _scan_state.get("mode") == "user" and _scan_state.get("user_id") == user_id
        )
        if memory_matches and (
            _scan_state["running"]
            or _scan_state.get("finished_at")
            or _scan_state.get("error")
            or _scan_state.get("steps")
        ):
            steps = [dict(s) for s in _scan_state["steps"]]
            live = {
                "running": _scan_state["running"],
                "started_at": _scan_state["started_at"],
                "finished_at": _scan_state["finished_at"],
                "error": _scan_state["error"],
                "warnings": list(_scan_state["warnings"]),
                "collection": _scan_state.get("collection"),
                "current_step": _running_step_key_from_steps(steps),
                "detail": _scan_state.get("current_detail"),
                "steps": steps,
                "log": list(_scan_state["log"][-20:]),
            }
        else:
            live = None

    if live is None:
        persisted = load_scan_state(user_id)
        if persisted:
            steps = [dict(s) for s in (persisted.get("steps") or [])]
            live = {
                "running": bool(persisted.get("running")),
                "started_at": persisted.get("started_at"),
                "finished_at": persisted.get("finished_at"),
                "error": persisted.get("error"),
                "warnings": list(persisted.get("warnings") or []),
                "collection": persisted.get("collection"),
                "current_step": _running_step_key_from_steps(steps),
                "detail": persisted.get("current_detail") or persisted.get("detail"),
                "steps": steps,
                "log": list(persisted.get("log") or [])[-20:],
            }
        else:
            live = {
                "running": False,
                "started_at": None,
                "finished_at": None,
                "error": None,
                "warnings": [],
                "collection": None,
                "current_step": None,
                "detail": None,
                "steps": [],
                "log": [],
            }

    # In-memory is source of truth while this process owns the scan.
    with _scan_lock:
        if _scan_state.get("running") and _scan_state.get("user_id") == user_id:
            steps = [dict(s) for s in _scan_state["steps"]]
            live = {
                "running": True,
                "started_at": _scan_state["started_at"],
                "finished_at": None,
                "error": None,
                "warnings": list(_scan_state["warnings"]),
                "collection": _scan_state.get("collection"),
                "current_step": _running_step_key_from_steps(steps),
                "detail": _scan_state.get("current_detail"),
                "steps": steps,
                "log": list(_scan_state["log"][-20:]),
            }

    latest_scan = db.get_latest_scan(db.WORKSPACE_CV_ID, db_path=workspace_db)
    if latest_scan and not live.get("warnings"):
        summary_data = _parse_scan_summary(latest_scan.get("summary"))
        live["warnings"] = summary_data.get("warnings") or []
        if not live.get("collection"):
            live["collection"] = summary_data.get("collection")
    if latest_scan and not live.get("error") and not live.get("running"):
        live["error"] = latest_scan.get("error_message")
    live["latest_scan"] = latest_scan
    live["match_count"] = _workspace_match_count(user_id)
    live["cv_count"] = len(db.list_active_cvs_for_user(user_id))
    live["can_stop"] = bool(live.get("running"))
    return live


def _cv_public(cv: dict) -> dict:
    """Shape a CV row for the API (without the large parsed_profile blob)."""
    profile = None
    raw = cv.get("parsed_profile")
    if raw:
        try:
            data = json.loads(raw)
            profile = {
                "name": (data.get("contact") or {}).get("name"),
                "seniority": (data.get("experience") or {}).get("seniority_level"),
                "best_fit_roles": (data.get("best_fit_roles") or [])[:5],
                "skills_count": sum(
                    len(v) for v in (data.get("skills") or {}).values()
                ) if isinstance(data.get("skills"), dict) else None,
            }
        except (json.JSONDecodeError, TypeError):
            profile = None
    return {
        "id": cv["id"],
        "file_name": cv.get("file_name"),
        "display_name": cv.get("display_name"),
        "file_ext": cv.get("file_ext"),
        "file_size": cv.get("file_size"),
        "created_at": cv.get("created_at"),
        "updated_at": cv.get("updated_at"),
        "last_scan_at": cv.get("last_scan_at"),
        "match_count": cv.get("match_count"),
        "scan_count": cv.get("scan_count"),
        "profile": profile,
    }


def _parse_json_list(value) -> list:
    if not value:
        return []
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _job_description_text(match: dict) -> str:
    """Prefer the enriched full description, fall back to the listing snippet.

    Always injects a Hebrew publication-date header at the top of the text box.
    """
    from date_utils import inject_posted_date_header, normalize_posted_date

    full = (match.get("full_description") or "").strip()
    body = full if full else (match.get("description") or "").strip()
    posted = normalize_posted_date(
        match.get("posted_date")
        or match.get("first_seen_at")
        or match.get("collected_at")
        or match.get("job_created_at"),
        default_to_today=True,
    )
    return inject_posted_date_header(body, posted)


def _match_public(match: dict) -> dict:
    from date_utils import normalize_posted_date

    posted_date = normalize_posted_date(
        match.get("posted_date")
        or match.get("first_seen_at")
        or match.get("collected_at")
        or match.get("job_created_at"),
        default_to_today=True,
    )
    return {
        "match_id": match.get("match_id"),
        "job_id": match.get("job_id"),
        "scan_id": match.get("scan_id"),
        "title": match.get("title"),
        "company": match.get("company"),
        "location": match.get("location"),
        "job_url": match.get("job_url"),
        "source": match.get("source"),
        "description": _job_description_text(match),
        "posted_date": posted_date,
        # Prefer board publication date for chronological UI sorting.
        "job_created_at": posted_date
        or match.get("job_created_at")
        or match.get("first_seen_at")
        or match.get("collected_at"),
        "match_score": match.get("match_score"),
        "match_reason": match.get("match_reason"),
        "explanation": match.get("ai_explanation") or match.get("match_reason"),
        "matched_skills": _parse_json_list(match.get("matched_skills"))
        or _parse_json_list(match.get("ai_strengths")),
        "missing_skills": _parse_json_list(match.get("missing_skills"))
        or _parse_json_list(match.get("ai_missing_skills")),
        "score_label": match.get("ats_score_label"),
        "missing_mandatory": _parse_json_list(match.get("ats_missing_mandatory")),
        "relevant_experience": _parse_json_list(match.get("ats_relevant_experience")),
        "score_reasons": _parse_json_list(match.get("ats_reasons")),
        "cv_improvements": _parse_json_list(match.get("ats_improvements")),
        "is_potential_junior_match": bool(match.get("is_potential_junior_match")),
        "has_tailored_cv": bool(match.get("tailored_cv_path")),
        "tailored_cv_updated_at": match.get("tailored_cv_updated_at"),
        "application_status": match.get("application_status") or db.CV_APP_NOT_SENT,
        "application_notes": match.get("application_notes"),
        "job_application": match.get("job_application"),
        "updated_at": match.get("match_updated_at"),
    }


def _clear_live_scan_state(user_id: str = db.DEFAULT_USER_ID) -> None:
    """Reset in-memory + persisted scan UI so the client can start fresh."""
    with _scan_lock:
        if _scan_state["running"] and (
            _scan_state.get("user_id") == user_id or _scan_state.get("mode") == "user"
        ):
            raise HTTPException(status_code=409, detail="לא ניתן לאפס בזמן סריקה פעילה")
        _scan_state.update(
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
    clear_scan_state(user_id)


@app.post("/jobs/matches/reset")
def reset_job_matches(user: dict = Depends(auth.get_current_user)):
    """Clear workspace match/scan results; keep uploaded CV files."""
    db.ensure_multi_cv_storage()
    user_id = user["id"]
    _clear_live_scan_state(user_id)
    summary = cv_service.reset_user_results(user_id)
    return {"ok": True, **summary}


@app.post("/cvs/reset")
def reset_all_cvs(user: dict = Depends(auth.get_current_user)):
    """Delete all uploaded CV files and clear workspace results/profiles."""
    db.ensure_multi_cv_storage()
    user_id = user["id"]
    _clear_live_scan_state(user_id)
    summary = cv_service.reset_user_files(user_id)
    return {"ok": True, **summary}


@app.get("/cvs")
def list_cvs(user: dict = Depends(auth.get_current_user)):
    try:
        db.ensure_multi_cv_storage()
        user_id = user["id"]
        return {
            "cvs": [_cv_public(cv) for cv in db.list_cvs(user_id=user_id)],
            "workspace_match_count": _workspace_match_count(user_id),
            "active_cv_count": len(db.list_active_cvs_for_user(user_id)),
        }
    except Exception as exc:  # noqa: BLE001 — never return an opaque 500 to the UI
        raise HTTPException(
            status_code=500,
            detail=f"שגיאה בטעינת קורות החיים: {exc}",
        ) from exc


@app.post("/jobs/match")
def run_job_matcher(req: RunAgentRequest, user: dict = Depends(auth.get_current_user)):
    """Run the job-matching agent across all uploaded CVs for the current user."""
    db.ensure_multi_cv_storage()
    return _start_user_scan(req, user_id=user["id"])


@app.post("/jobs/match/stop")
def stop_job_matcher(user: dict = Depends(auth.get_current_user)):
    """Stop the currently running workspace job-matching scan."""
    db.ensure_multi_cv_storage()
    return _stop_user_scan(user_id=user["id"])


@app.get("/jobs/match-status")
def job_match_status(user: dict = Depends(auth.get_current_user)):
    db.ensure_multi_cv_storage()
    return _user_scan_status(user_id=user["id"])


@app.get("/jobs/matches")
def get_job_matches(
    latest: bool = False,
    min_score: int | None = None,
    sort_by: str | None = None,
    order: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    sort_key, sort_order = _parse_match_sort(sort_by, order)
    workspace_db = user_db_path(user["id"])
    try:
        matches = db.get_cv_matches(
            db.WORKSPACE_CV_ID,
            latest_only=latest,
            min_score=min_score,
            sort_by=sort_key,
            order=sort_order,
            db_path=workspace_db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"matches": [_match_public(_reshape_match_row(m)) for m in matches]}


@app.post("/cvs/upload")
async def upload_cv_multi(
    file: UploadFile = File(...),
    as_new_version: bool = Form(False),
    display_name: str | None = Form(None),
    user: dict = Depends(auth.get_current_user),
):
    try:
        db.ensure_multi_cv_storage()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"שגיאה באתחול מסד הנתונים: {exc}",
        ) from exc

    with _scan_lock:
        if _scan_state["running"]:
            raise HTTPException(status_code=409, detail="לא ניתן להעלות קבצים בזמן סריקה")

    try:
        data = await file.read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"לא ניתן לקרוא את הקובץ: {exc}") from exc

    try:
        cv = cv_service.upload_cv(
            file.filename or "cv",
            data,
            display_name=display_name,
            as_new_version=as_new_version,
            user_id=user["id"],
        )
    except cv_service.DuplicateCvError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "קובץ זהה כבר הועלה",
                "existing": _cv_public(exc.existing),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"שגיאת שמירת קובץ בשרת (דיסק/הרשאות): {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"העלאת הקובץ נכשלה: {exc}",
        ) from exc
    return {"cv": _cv_public(cv)}


@app.get("/cvs/{cv_id}")
def get_cv(cv_id: str, user: dict = Depends(auth.get_current_user)):
    db.ensure_multi_cv_storage()
    cv = _require_owned_cv(cv_id, user)
    public = _cv_public(cv)
    public["latest_scan"] = db.get_latest_scan(cv_id, db_path=cv_db_path(cv_id))
    return {"cv": public}


@app.delete("/cvs/{cv_id}")
def delete_cv(cv_id: str, user: dict = Depends(auth.get_current_user)):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    with _scan_lock:
        if _scan_state["running"] and (
            _scan_state.get("cv_id") == cv_id
            or (
                _scan_state.get("mode") == "user"
                and _scan_state.get("user_id") == user["id"]
            )
        ):
            raise HTTPException(status_code=409, detail="לא ניתן למחוק בזמן סריקה")
    try:
        summary = cv_service.delete_cv(cv_id)
    except Exception as exc:  # noqa: BLE001 — surface delete failures clearly
        raise HTTPException(
            status_code=500,
            detail=f"מחיקת הקובץ נכשלה: {exc}",
        ) from exc
    return {"deleted": True, **summary}


@app.get("/api/job-sites")
def get_job_sites():
    return {"sites": list_job_boards()}


@app.post("/cvs/{cv_id}/analyze")
def analyze_cv_domains(cv_id: str, user: dict = Depends(auth.get_current_user)):
    """Parse the CV and return recommended job domains/roles for user selection."""
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)

    with _scan_lock:
        if _scan_state["running"]:
            raise HTTPException(status_code=409, detail="סריקה אחרת כבר רצה")

    begin_scan()
    try:
        result = cv_service.analyze_cv(cv_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"ניתוח קורות החיים נכשל: {exc}",
        ) from exc

    return {
        "cv_id": cv_id,
        "domains": result.get("domains") or [],
        "candidate_summary": result.get("candidate_summary") or "",
        "career_notes": result.get("career_notes") or "",
        "best_fit_roles": result.get("best_fit_roles") or [],
    }


@app.post("/cvs/{cv_id}/search")
def search_jobs_for_cv(
    cv_id: str,
    req: SearchJobsRequest,
    user: dict = Depends(auth.get_current_user),
):
    """Start an incremental job search for the user-selected domains."""
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)

    domains = req.resolved_domains()
    if not domains:
        raise HTTPException(status_code=400, detail="יש לבחור לפחות תחום אחד לחיפוש")

    try:
        normalize_job_board_ids(req.job_sites)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _scan_lock:
        if _scan_state["running"]:
            raise HTTPException(status_code=409, detail="סריקה אחרת כבר רצה")
        begin_scan()
        _scan_state.update(
            {
                "running": True,
                "mode": "cv_search",
                "cv_id": cv_id,
                "user_id": user["id"],
                "scan_id": None,
                "started_at": _utc_now(),
                "finished_at": None,
                "error": None,
                "warnings": [],
                "collection": None,
                "log": [],
                "current_detail": "מתחיל חיפוש משרות…",
                "steps": [
                    {"key": key, "name": name, "status": "pending"}
                    for key, name, _, _ in cv_service.SEARCH_STEPS
                ],
            }
        )
    _persist_scan_state()

    thread = threading.Thread(
        target=_run_search_thread,
        args=(cv_id, domains, req.skip_enrich, req.job_sites),
        daemon=True,
    )
    thread.start()
    return {
        "started": True,
        "cv_id": cv_id,
        "domains": domains,
    }


@app.post("/cvs/{cv_id}/run-agent")
def run_agent_for_cv(
    cv_id: str,
    req: RunAgentRequest,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)

    try:
        normalize_job_board_ids(req.job_sites)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _scan_lock:
        if _scan_state["running"]:
            raise HTTPException(status_code=409, detail="סריקה אחרת כבר רצה")
        begin_scan()
        _scan_state.update(
            {
                "running": True,
                "mode": "cv",
                "cv_id": cv_id,
                "user_id": user["id"],
                "scan_id": None,
                "started_at": _utc_now(),
                "finished_at": None,
                "error": None,
                "warnings": [],
                "collection": None,
                "log": [],
                "current_detail": "מתחיל סריקה…",
                "steps": [
                    {"key": key, "name": name, "status": "pending"}
                    for key, name, _, _ in cv_service.SCAN_STEPS
                ],
            }
        )
    _persist_scan_state()

    thread = threading.Thread(
        target=_run_scan_thread,
        args=(cv_id, req.skip_collect, req.skip_enrich, req.job_sites, req.domains),
        daemon=True,
    )
    thread.start()
    return {"started": True, "cv_id": cv_id}


@app.get("/cvs/{cv_id}/scan-status")
def cv_scan_status(cv_id: str, user: dict = Depends(auth.get_current_user)):
    _require_owned_cv(cv_id, user)
    cv_db = cv_db_path(cv_id)
    with _scan_lock:
        # Only report the live runner state when it belongs to this CV.
        if _scan_state["cv_id"] == cv_id:
            steps = [dict(s) for s in _scan_state["steps"]]
            live = {
                "running": _scan_state["running"],
                "started_at": _scan_state["started_at"],
                "finished_at": _scan_state["finished_at"],
                "error": _scan_state["error"],
                "warnings": list(_scan_state["warnings"]),
                "collection": _scan_state.get("collection"),
                "current_step": _running_step_key_from_steps(steps),
                "detail": _scan_state.get("current_detail"),
                "steps": steps,
                "log": list(_scan_state["log"][-20:]),
                "match_count": _cv_match_count(cv_id),
            }
        else:
            live = {
                "running": False,
                "started_at": None,
                "finished_at": None,
                "error": None,
                "warnings": [],
                "collection": None,
                "current_step": None,
                "detail": None,
                "steps": [],
                "log": [],
                "match_count": _cv_match_count(cv_id),
            }
    latest_scan = db.get_latest_scan(cv_id, db_path=cv_db)
    if latest_scan and not live.get("warnings"):
        summary_data = _parse_scan_summary(latest_scan.get("summary"))
        live["warnings"] = summary_data.get("warnings") or []
        if not live.get("collection"):
            live["collection"] = summary_data.get("collection")
    live["latest_scan"] = latest_scan
    return live


@app.get("/cvs/{cv_id}/matches")
def get_cv_matches(
    cv_id: str,
    latest: bool = False,
    min_score: int | None = None,
    sort_by: str | None = None,
    order: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    cv_db = cv_db_path(cv_id)
    db.init_db(cv_db)
    sort_key, sort_order = _parse_match_sort(sort_by, order)
    try:
        matches = db.get_cv_matches(
            cv_id,
            latest_only=latest,
            min_score=min_score,
            sort_by=sort_key,
            order=sort_order,
            db_path=cv_db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_ids = [int(m["job_id"]) for m in matches]
    latest_apps = db.get_latest_job_applications_for_cv(cv_id, job_ids, db_path=cv_db)
    enriched = []
    for m in matches:
        row = dict(m)
        app = latest_apps.get(int(row["job_id"]))
        if app:
            row["job_application"] = public_application(app)
        enriched.append(_match_public(row))
    return {"matches": enriched}


class MatchStatusRequest(BaseModel):
    status: str
    notes: str | None = None


class TailorCvRequest(BaseModel):
    force: bool = False


@app.post("/cvs/{cv_id}/jobs/{job_id}/tailor-cv")
def tailor_cv_endpoint(
    cv_id: str,
    job_id: int,
    regenerate: bool = False,
    req: TailorCvRequest | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Generate an ATS-optimized Markdown CV tailored to one job (no hallucinated experience).

    Pass ``?regenerate=true`` to deep-scan original source CVs against ATS gaps on
    the current best draft (score guard keeps only strictly better results).
    """
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)

    cv_db = cv_db_path(cv_id)
    db.init_db(cv_db)
    job = db.get_job_by_id(job_id, db_path=cv_db)
    if job is None:
        raise HTTPException(status_code=404, detail="משרה לא נמצאה")

    force = (req.force if req else False) or regenerate
    try:
        result = tailor_cv_for_job(
            cv_id, job, force=force, regenerate=regenerate
        )
    except TailorCvError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    relative_path = f"data/cvs/{cv_id}/tailored_cvs/{job_id}.md"
    # Only bump tailored-CV metadata when content actually changed.
    if not result.get("no_improvement"):
        db.mark_cv_match_tailored(
            cv_id,
            job_id,
            tailored_cv_path=relative_path,
            db_path=cv_db,
        )

    return {
        "cv_id": cv_id,
        "job_id": job_id,
        "title": job.get("title"),
        "company": job.get("company"),
        "markdown": result["markdown"],
        "cv_markdown": result.get("cv_markdown") or result["markdown"],
        "changes_breakdown": result.get("changes_breakdown") or [],
        "estimated_ats_score": result.get("estimated_ats_score"),
        "highlights": result.get("highlights") or [],
        "caveats": result.get("caveats") or [],
        "from_cache": bool(result.get("from_cache")),
        "saved_path": relative_path,
        "generated_at": result.get("generated_at"),
        "regenerated": bool(result.get("regenerated")),
        "improved": bool(result.get("improved")),
        "no_improvement": bool(result.get("no_improvement")),
        "message": result.get("message"),
        "matcher_feedback": result.get("matcher_feedback"),
    }


@app.get("/cvs/{cv_id}/jobs/{job_id}/tailored-cv/download-pdf")
def download_tailored_cv_pdf(
    cv_id: str,
    job_id: int,
    user: dict = Depends(auth.get_current_user),
):
    """Render the saved tailored CV Markdown to a professionally styled A4 PDF."""
    db.ensure_multi_cv_storage()
    # Workspace-mode matches store tailored CVs under the user workspace, while
    # legacy per-CV scans store them under data/cvs/<cv_id>/. Accept either.
    if cv_id == db.WORKSPACE_CV_ID:
        pass
    else:
        _require_owned_cv(cv_id, user)

    saved = load_saved_tailored_cv(cv_id, job_id)
    if not saved and cv_id != db.WORKSPACE_CV_ID:
        saved = load_saved_tailored_cv(db.WORKSPACE_CV_ID, job_id)
    if not saved:
        raise HTTPException(
            status_code=404,
            detail="לא נמצא קובץ קורות חיים מותאם — יש ליצור קודם",
        )

    cv_body = extract_cv_markdown_for_copy(saved)
    try:
        pdf_bytes, filename = generate_tailored_cv_pdf(cv_body)
    except PdfGeneratorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    # ASCII fallback keeps Content-Disposition compatible with older clients.
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "CV_Tailored.pdf"
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"',
        "Cache-Control": "no-store",
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@app.patch("/jobs/matches/{match_id}/status")
def update_workspace_match_status(
    match_id: int,
    req: MatchStatusRequest,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    if req.status not in db.CV_APP_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"סטטוס לא חוקי: {req.status}",
        )
    workspace_db = user_db_path(user["id"])
    updated = db.update_cv_match_status(
        db.WORKSPACE_CV_ID,
        match_id,
        req.status,
        notes=req.notes,
        db_path=workspace_db,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="התאמה לא נמצאה")
    return {"updated": True, "match": _match_public(_reshape_match_row(updated))}


def _resolve_job_context(
    job_id: int,
    cv_id: str | None = None,
    *,
    user_id: str = db.DEFAULT_USER_ID,
) -> tuple[dict[str, Any], Path, str]:
    """Find a job in the user workspace DB first, then per-CV DB."""
    workspace_db = user_db_path(user_id)
    job = db.get_job_by_id(job_id, db_path=workspace_db)
    if job is not None:
        return job, workspace_db, db.WORKSPACE_CV_ID
    if cv_id:
        cv_db = cv_db_path(cv_id)
        job = db.get_job_by_id(job_id, db_path=cv_db)
        if job is not None:
            return job, cv_db, cv_id
    raise HTTPException(status_code=404, detail="משרה לא נמצאה")


@app.post("/jobs/{job_id}/tailor-cv")
def tailor_workspace_job(
    job_id: int,
    regenerate: bool = False,
    source_cv_id: str | None = None,
    req: TailorCvRequest | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Tailor CV for a workspace job using the aggregated master profile."""
    db.ensure_multi_cv_storage()
    if source_cv_id:
        _require_owned_cv(source_cv_id, user)
    job, workspace_db, profile_cv_id = _resolve_job_context(
        job_id, source_cv_id, user_id=user["id"]
    )
    force = (req.force if req else False) or regenerate
    try:
        result = tailor_cv_for_job(
            profile_cv_id,
            job,
            force=force,
            regenerate=regenerate,
            user_id=user["id"],
        )
    except TailorCvError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:  # noqa: BLE001 — never leak an opaque 500 to the UI
        raise HTTPException(
            status_code=500,
            detail=f"התאמת קורות החיים נכשלה: {exc}",
        ) from exc

    relative_path = f"data/users/{user['id']}/tailored_cvs/{job_id}.md"
    # Record tailored metadata on the workspace match when content changed.
    if not result.get("no_improvement"):
        db.mark_cv_match_tailored(
            db.WORKSPACE_CV_ID,
            job_id,
            tailored_cv_path=relative_path,
            db_path=workspace_db,
        )

    cv_body = result.get("cv_markdown") or extract_cv_markdown_for_copy(
        result.get("markdown") or ""
    )
    return {
        "cv_id": profile_cv_id,
        "job_id": job_id,
        "title": job.get("title"),
        "company": job.get("company"),
        "markdown": result["markdown"],
        "cv_markdown": cv_body,
        "changes_breakdown": result.get("changes_breakdown") or [],
        "estimated_ats_score": result.get("estimated_ats_score"),
        "highlights": result.get("highlights") or [],
        "caveats": result.get("caveats") or [],
        "from_cache": bool(result.get("from_cache")),
        "saved_path": relative_path,
        "generated_at": result.get("generated_at"),
        "regenerated": bool(result.get("regenerated")),
        "improved": result.get("improved"),
        "no_improvement": bool(result.get("no_improvement")),
        "message": result.get("message"),
        "matcher_feedback": result.get("matcher_feedback"),
    }


@app.patch("/cvs/{cv_id}/matches/{match_id}/status")
def update_match_status(
    cv_id: str,
    match_id: int,
    req: MatchStatusRequest,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    if req.status not in db.CV_APP_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"סטטוס לא חוקי: {req.status}",
        )
    updated = db.update_cv_match_status(
        cv_id, match_id, req.status, notes=req.notes, db_path=cv_db_path(cv_id)
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="התאמה לא נמצאה")
    return {"updated": True, "match": _match_public(_reshape_match_row(updated))}


class ApplyJobRequest(BaseModel):
    force: bool = False


def _application_error_response(exc: ApplicationError) -> HTTPException:
    detail: dict | str = exc.message
    if exc.code:
        detail = {"message": exc.message, "code": exc.code}
    return HTTPException(status_code=exc.status_code, detail=detail)


@app.post("/cvs/{cv_id}/jobs/{job_id}/apply")
def apply_to_job(
    cv_id: str,
    job_id: int,
    req: ApplyJobRequest | None = None,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)

    browser_ok, browser_error = _playwright_browser_ready()
    if not browser_ok:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "דפדפן האוטומציה אינו זמין בשרת. "
                    "לא ניתן להגיש קורות חיים אוטומטית כרגע."
                ),
                "code": "playwright_unavailable",
                "detail": browser_error,
            },
        )

    cv_db = cv_db_path(cv_id)
    force = req.force if req else False
    try:
        result = start_application(cv_id, job_id, force=force, db_path=cv_db)
    except ApplicationError as exc:
        raise _application_error_response(exc) from exc

    app = result["application"]
    enqueue_application(app["id"], cv_id, job_id, db_path=cv_db)
    return {
        "application_id": app["id"],
        "status": app["status"],
        "application": public_application(app),
    }


@app.get("/cvs/{cv_id}/job-applications/{application_id}")
def get_job_application(
    cv_id: str,
    application_id: str,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    cv_db = cv_db_path(cv_id)
    try:
        return get_application_for_cv(cv_id, application_id, cv_db)
    except ApplicationError as exc:
        raise _application_error_response(exc) from exc


@app.get("/cvs/{cv_id}/jobs/{job_id}/application-status")
def job_application_status(
    cv_id: str,
    job_id: int,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    cv_db = cv_db_path(cv_id)
    try:
        status = get_job_application_status(cv_id, job_id, cv_db)
    except ApplicationError as exc:
        raise _application_error_response(exc) from exc
    if status is None:
        return {"status": None, "application": None}
    status["active"] = is_application_active(status["application_id"])
    return {"status": status["status"], "application": status}


@app.post("/cvs/{cv_id}/job-applications/{application_id}/retry")
def retry_job_application(
    cv_id: str,
    application_id: str,
    user: dict = Depends(auth.get_current_user),
):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    cv_db = cv_db_path(cv_id)
    try:
        existing = get_application_for_cv(cv_id, application_id, cv_db)
    except ApplicationError as exc:
        raise _application_error_response(exc) from exc

    if existing["status"] == db.JOB_APP_IN_PROGRESS or is_application_active(application_id):
        raise HTTPException(status_code=409, detail="הגשה כבר בתהליך")

    result = start_application(
        cv_id,
        int(existing["job_id"]),
        force=True,
        db_path=cv_db,
    )
    app = result["application"]
    enqueue_application(app["id"], cv_id, int(existing["job_id"]), db_path=cv_db)
    return {
        "application_id": app["id"],
        "status": app["status"],
        "application": public_application(app),
    }


class LinkedInSessionRequest(BaseModel):
    cookies: list[dict]
    origins: list[dict] | None = None


class SiteCredentialInput(BaseModel):
    email: str = ""
    password: str | None = None


class SiteCredentialsUpdate(BaseModel):
    linkedin: SiteCredentialInput | None = None
    drushim: SiteCredentialInput | None = None


@app.get("/cvs/{cv_id}/site-credentials")
def get_site_credentials(cv_id: str, user: dict = Depends(auth.get_current_user)):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    return {"credentials": public_site_credentials(cv_id)}


@app.put("/cvs/{cv_id}/site-credentials")
def save_site_credentials(
    cv_id: str,
    req: SiteCredentialsUpdate,
    user: dict = Depends(auth.get_current_user),
):
    """Save per-CV site logins used for one-click job applications."""
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)

    linkedin_patch = None
    drushim_patch = None
    if req.linkedin is not None:
        linkedin_patch = {
            "email": req.linkedin.email.strip(),
            "password": req.linkedin.password,
        }
    if req.drushim is not None:
        drushim_patch = {
            "email": req.drushim.email.strip(),
            "password": req.drushim.password,
        }

    credentials = update_site_credentials(
        cv_id,
        linkedin=linkedin_patch,
        drushim=drushim_patch,
    )
    return {"saved": True, "credentials": credentials}


@app.put("/cvs/{cv_id}/site-sessions/linkedin")
def save_linkedin_session(
    cv_id: str,
    req: LinkedInSessionRequest,
    user: dict = Depends(auth.get_current_user),
):
    """Import Playwright storage_state cookies so the server can apply on LinkedIn."""
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    if not req.cookies:
        raise HTTPException(status_code=400, detail="נדרש לפחות cookie אחד")
    payload = {"cookies": req.cookies, "origins": req.origins or []}
    path = import_linkedin_storage_state(cv_id, payload)
    return {"saved": True, "path": str(path)}


@app.get("/cvs/{cv_id}/site-sessions/linkedin")
def linkedin_session_status(cv_id: str, user: dict = Depends(auth.get_current_user)):
    db.ensure_multi_cv_storage()
    _require_owned_cv(cv_id, user)
    path = linkedin_storage_state_path(cv_id)
    return {"configured": path.is_file(), "path": str(path)}


# Aliases matching the suggested /api/ routes (cv_profile_id in body).
class ApiApplyJobRequest(BaseModel):
    cv_profile_id: str
    job_id: int | None = None
    force: bool = False


@app.post("/api/jobs/{job_id}/apply")
def api_apply_to_job(job_id: int, req: ApiApplyJobRequest):
    return apply_to_job(req.cv_profile_id, job_id, ApplyJobRequest(force=req.force))


@app.get("/api/job-applications/{application_id}")
def api_get_job_application(application_id: str, cv_profile_id: str):
    return get_job_application(cv_profile_id, application_id)


@app.get("/api/jobs/{job_id}/application-status")
def api_job_application_status(job_id: int, cv_profile_id: str):
    return job_application_status(cv_profile_id, job_id)


@app.post("/api/job-applications/{application_id}/retry")
def api_retry_job_application(application_id: str, cv_profile_id: str):
    return retry_job_application(cv_profile_id, application_id)


def _reshape_match_row(row: dict) -> dict:
    """Adapt a raw cv_job_matches row to the shape _match_public expects."""
    row = dict(row)
    row.setdefault("match_id", row.get("id"))
    row.setdefault("match_updated_at", row.get("updated_at"))
    return row


def _playwright_browser_ready() -> tuple[bool, str | None]:
    """Check whether Playwright Chromium is installed (without launching a browser)."""
    import os
    from pathlib import Path

    browsers_path = Path(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")).expanduser()
    if browsers_path.is_dir():
        if any(browsers_path.glob("chromium*")) or any(
            browsers_path.glob("chromium_headless_shell*")
        ):
            return True, None
        return False, f"No chromium binaries under {browsers_path}"

    return False, "PLAYWRIGHT_BROWSERS_PATH not set or missing"


def _data_dir_looks_persistent() -> bool:
    """Heuristic: a dedicated mount (Render disk / Docker volume) has a different device id."""
    forced = os.getenv("DATA_PERSISTENT", "").strip().lower()
    if forced in {"1", "true", "yes"}:
        return True
    if forced in {"0", "false", "no"}:
        return False
    try:
        data = DATA_DIR.resolve()
        data.mkdir(parents=True, exist_ok=True)
        return data.stat().st_dev != data.parent.stat().st_dev
    except OSError:
        return False


@app.get("/api/health")
async def health():
    browser_ok, browser_error = _playwright_browser_ready()
    data_persistent = _data_dir_looks_persistent()
    return {
        "ok": True,
        "pipeline_running": _pipeline_state["running"],
        "scan_running": _scan_state["running"],
        "playwright_ready": browser_ok,
        "playwright_error": browser_error,
        "data_dir": str(DATA_DIR),
        "data_persistent": data_persistent,
        "registry_db_exists": db.REGISTRY_DB_PATH.is_file(),
    }


# ---------------------------------------------------------------------------
# Frontend (production) — serve built React app from the same origin
# ---------------------------------------------------------------------------

FRONTEND_DIST = PROJECT_ROOT.parent / "resume-agent-web" / "dist"

if FRONTEND_DIST.is_dir():
    _assets = FRONTEND_DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="frontend-assets")

    @app.get("/")
    async def frontend_index():
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/favicon.svg")
    async def frontend_favicon():
        return FileResponse(FRONTEND_DIST / "favicon.svg")

    @app.get("/icons.svg")
    async def frontend_icons():
        return FileResponse(FRONTEND_DIST / "icons.svg")

    @app.get("/{page_path:path}")
    async def frontend_spa(page_path: str):
        # Never let the SPA catch-all shadow API routes if matching order slips.
        first = (page_path or "").split("/", 1)[0]
        if first in {"api", "cvs", "jobs"}:
            raise HTTPException(status_code=404, detail="Not found")
        candidate = FRONTEND_DIST / page_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Job Agent HTTP API")
    parser.add_argument("--host", default=API_HOST, help=f"bind address (default: {API_HOST})")
    parser.add_argument("--port", type=int, default=API_PORT, help=f"listen port (default: {API_PORT})")
    args = parser.parse_args()
    host = os.getenv("API_HOST", args.host)
    port = int(os.getenv("PORT", os.getenv("API_PORT", str(args.port))))
    uvicorn.run(app, host=host, port=port)
