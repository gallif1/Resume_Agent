"""HTTP API for the AI Job Agent — lets the web client run the pipeline.

Run with:
    python src/api_server.py            # starts on http://localhost:8000 (or API_PORT from .env)
    python src/api_server.py --port 8001

Multi-CV endpoints (each CV has isolated data):
    GET    /cvs                             list uploaded CVs + metadata
    POST   /cvs/upload                      upload a CV (dedup by content hash)
    GET    /cvs/{cv_id}                     one CV + its latest scan
    DELETE /cvs/{cv_id}                     delete a CV and all its data
    POST   /cvs/{cv_id}/run-agent           run the agent for a single CV
    GET    /cvs/{cv_id}/scan-status         live scan progress + log tail
    GET    /cvs/{cv_id}/matches             CV's job matches (query: latest, min_score)
    PATCH  /cvs/{cv_id}/matches/{id}/status set the application status for a match
    POST   /cvs/{cv_id}/jobs/{job_id}/tailor-cv  generate ATS-tailored CV markdown for a job
           (?regenerate=true runs matcher feedback loop to improve ATS score)
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import cv_service
import db
from application_service import ApplicationError, get_application_for_cv, get_job_application_status, public_application, start_application
from application_worker import enqueue_application, is_application_active
from collection_report import parse_agent_line
from config import API_HOST, API_PORT, CV_PROFILE_PATH, PROJECT_ROOT, RESUMES_DIR, cv_db_path
from job_boards import list_job_boards, normalize_job_board_ids
from pdf_generator_service import PdfGeneratorError, generate_tailored_cv_pdf
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local tool — the web client runs on a different port
    allow_methods=["*"],
    allow_headers=["*"],
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    "cv_id": None,
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
        if parsed is None:
            return
        if parsed.get("type") == "warning":
            message = parsed.get("message")
            if message and message not in _scan_state["warnings"]:
                _scan_state["warnings"].append(message)
        elif parsed.get("type") == "summary":
            _scan_state["collection"] = parsed.get("summary")


def _scan_set_step(key: str, status: str) -> None:
    with _scan_lock:
        for step in _scan_state["steps"]:
            if step["key"] == key:
                step["status"] = status
                if status == "running":
                    _scan_state["current_detail"] = step["name"]
                break


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
) -> None:
    error: str | None = None
    try:
        scan = cv_service.run_scan(
            cv_id,
            skip_collect=skip_collect,
            skip_enrich=skip_enrich,
            job_sites=job_sites,
            log=_scan_log,
            set_step_status=_scan_set_step,
        )
        if scan.get("status") == db.SCAN_FAILED:
            error = scan.get("error_message") or "הסריקה נכשלה"
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
            _scan_state["error"] = error


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


def _match_public(match: dict) -> dict:
    return {
        "match_id": match.get("match_id"),
        "job_id": match.get("job_id"),
        "scan_id": match.get("scan_id"),
        "title": match.get("title"),
        "company": match.get("company"),
        "location": match.get("location"),
        "job_url": match.get("job_url"),
        "source": match.get("source"),
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


@app.get("/cvs")
def list_cvs():
    db.ensure_multi_cv_storage()
    # Import any pre-existing single-CV setup so it appears as the first CV.
    try:
        cv_service.adopt_legacy_cv()
    except Exception:  # noqa: BLE001 — adoption is best-effort
        pass
    return {"cvs": [_cv_public(cv) for cv in db.list_cvs()]}


@app.post("/cvs/upload")
async def upload_cv_multi(
    file: UploadFile = File(...),
    as_new_version: bool = Form(False),
    display_name: str | None = Form(None),
):
    db.ensure_multi_cv_storage()
    data = await file.read()
    try:
        cv = cv_service.upload_cv(
            file.filename or "cv",
            data,
            display_name=display_name,
            as_new_version=as_new_version,
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
    return {"cv": _cv_public(cv)}


@app.get("/cvs/{cv_id}")
def get_cv(cv_id: str):
    db.ensure_multi_cv_storage()
    cv = db.get_cv(cv_id)
    if cv is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")
    public = _cv_public(cv)
    public["latest_scan"] = db.get_latest_scan(cv_id, db_path=cv_db_path(cv_id))
    return {"cv": public}


@app.delete("/cvs/{cv_id}")
def delete_cv(cv_id: str):
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")
    with _scan_lock:
        if _scan_state["running"] and _scan_state["cv_id"] == cv_id:
            raise HTTPException(status_code=409, detail="לא ניתן למחוק בזמן סריקה")
    summary = cv_service.delete_cv(cv_id)
    return {"deleted": True, **summary}


class RunAgentRequest(BaseModel):
    skip_collect: bool = False
    skip_enrich: bool = False
    job_sites: list[str] | None = None


@app.get("/api/job-sites")
def get_job_sites():
    return {"sites": list_job_boards()}


@app.post("/cvs/{cv_id}/run-agent")
def run_agent_for_cv(cv_id: str, req: RunAgentRequest):
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")

    try:
        normalize_job_board_ids(req.job_sites)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _scan_lock:
        if _scan_state["running"]:
            raise HTTPException(status_code=409, detail="סריקה אחרת כבר רצה")
        _scan_state.update(
            {
                "running": True,
                "cv_id": cv_id,
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

    thread = threading.Thread(
        target=_run_scan_thread,
        args=(cv_id, req.skip_collect, req.skip_enrich, req.job_sites),
        daemon=True,
    )
    thread.start()
    return {"started": True, "cv_id": cv_id}


@app.get("/cvs/{cv_id}/scan-status")
def cv_scan_status(cv_id: str):
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
    latest_scan = db.get_latest_scan(cv_id, db_path=cv_db)
    if latest_scan and not live.get("warnings"):
        summary_data = _parse_scan_summary(latest_scan.get("summary"))
        live["warnings"] = summary_data.get("warnings") or []
        if not live.get("collection"):
            live["collection"] = summary_data.get("collection")
    live["latest_scan"] = latest_scan
    return live


@app.get("/cvs/{cv_id}/matches")
def get_cv_matches(cv_id: str, latest: bool = True, min_score: int | None = None):
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")
    cv_db = cv_db_path(cv_id)
    db.init_db(cv_db)
    matches = db.get_cv_matches(cv_id, latest_only=latest, min_score=min_score, db_path=cv_db)
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
):
    """Generate an ATS-optimized Markdown CV tailored to one job (no hallucinated experience).

    Pass ``?regenerate=true`` to score the previous draft with the deterministic
    matcher and ask the LLM to close measured keyword/skill gaps.
    """
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id, db_path=db.REGISTRY_DB_PATH) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")

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
def download_tailored_cv_pdf(cv_id: str, job_id: int):
    """Render the saved tailored CV Markdown to a professionally styled A4 PDF."""
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id, db_path=db.REGISTRY_DB_PATH) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")

    cv_db = cv_db_path(cv_id)
    db.init_db(cv_db)
    job = db.get_job_by_id(job_id, db_path=cv_db)
    if job is None:
        raise HTTPException(status_code=404, detail="משרה לא נמצאה")

    saved = load_saved_tailored_cv(cv_id, job_id)
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


@app.patch("/cvs/{cv_id}/matches/{match_id}/status")
def update_match_status(cv_id: str, match_id: int, req: MatchStatusRequest):
    db.ensure_multi_cv_storage()
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
def apply_to_job(cv_id: str, job_id: int, req: ApplyJobRequest | None = None):
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id, db_path=db.REGISTRY_DB_PATH) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")

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
def get_job_application(cv_id: str, application_id: str):
    db.ensure_multi_cv_storage()
    cv_db = cv_db_path(cv_id)
    try:
        return get_application_for_cv(cv_id, application_id, cv_db)
    except ApplicationError as exc:
        raise _application_error_response(exc) from exc


@app.get("/cvs/{cv_id}/jobs/{job_id}/application-status")
def job_application_status(cv_id: str, job_id: int):
    db.ensure_multi_cv_storage()
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
def retry_job_application(cv_id: str, application_id: str):
    db.ensure_multi_cv_storage()
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
def get_site_credentials(cv_id: str):
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id, db_path=db.REGISTRY_DB_PATH) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")
    return {"credentials": public_site_credentials(cv_id)}


@app.put("/cvs/{cv_id}/site-credentials")
def save_site_credentials(cv_id: str, req: SiteCredentialsUpdate):
    """Save per-user site logins used for one-click job applications."""
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id, db_path=db.REGISTRY_DB_PATH) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")

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
def save_linkedin_session(cv_id: str, req: LinkedInSessionRequest):
    """Import Playwright storage_state cookies so the server can apply on LinkedIn."""
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id, db_path=db.REGISTRY_DB_PATH) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")
    if not req.cookies:
        raise HTTPException(status_code=400, detail="נדרש לפחות cookie אחד")
    payload = {"cookies": req.cookies, "origins": req.origins or []}
    path = import_linkedin_storage_state(cv_id, payload)
    return {"saved": True, "path": str(path)}


@app.get("/cvs/{cv_id}/site-sessions/linkedin")
def linkedin_session_status(cv_id: str):
    db.ensure_multi_cv_storage()
    if db.get_cv(cv_id, db_path=db.REGISTRY_DB_PATH) is None:
        raise HTTPException(status_code=404, detail="קורות חיים לא נמצאו")
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


@app.get("/api/health")
async def health():
    browser_ok, browser_error = _playwright_browser_ready()
    return {
        "ok": True,
        "pipeline_running": _pipeline_state["running"],
        "scan_running": _scan_state["running"],
        "playwright_ready": browser_ok,
        "playwright_error": browser_error,
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
        if page_path.startswith(("api/", "cvs", "cvs/")):
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
