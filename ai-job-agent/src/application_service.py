"""Business logic for automated job applications."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import db
from config import AUTO_GENERATE_COVER_LETTER, cv_data_dir, cv_profile_prefs_path
from profile_utils import load_cv_profile

APPLICATION_STEPS = (
    "opening_job_page",
    "site_authentication",
    "detecting_application_provider",
    "opening_application_form",
    "filling_personal_details",
    "uploading_cv",
    "filling_experience",
    "answering_questions",
    "validating_form",
    "submitting_application",
    "verifying_submission",
)

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 10

_apply_rate_log: dict[str, list[float]] = {}


class ApplicationError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def check_rate_limit(cv_id: str) -> None:
    """Simple in-memory rate limiter per CV."""
    now = time.time()
    entries = _apply_rate_log.setdefault(cv_id, [])
    entries[:] = [t for t in entries if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if len(entries) >= RATE_LIMIT_MAX_REQUESTS:
        raise ApplicationError(
            "יותר מדי בקשות הגשה. נסה שוב בעוד דקה.",
            status_code=429,
            code="rate_limited",
        )
    entries.append(now)


def resolve_cv_file_path(cv_id: str) -> Path:
    directory = cv_data_dir(cv_id)
    if directory.exists():
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.stem.lower() == "resume":
                return path
    raise ApplicationError("קובץ קורות החיים לא נמצא", status_code=404, code="cv_not_found")


def build_user_profile(cv_id: str) -> dict[str, Any]:
    profile = load_cv_profile(cv_id)
    if not profile:
        raise ApplicationError(
            "פרופיל קורות חיים לא נמצא. הרץ ניתוח קורות חיים תחילה.",
            status_code=400,
            code="profile_missing",
        )

    prefs_path = cv_profile_prefs_path(cv_id)
    prefs: dict[str, Any] = {}
    if prefs_path.exists():
        try:
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prefs = {}

    cover_path = cv_data_dir(cv_id) / "cover_letter.txt"
    cover_letter = ""
    if cover_path.exists():
        try:
            cover_letter = cover_path.read_text(encoding="utf-8").strip()
        except OSError:
            cover_letter = ""

    profile = dict(profile)
    profile["preferences"] = prefs
    if cover_letter:
        profile["cover_letter"] = cover_letter
    return profile


def resolve_cover_letter(cv_id: str, profile: dict[str, Any], job: dict[str, Any]) -> str | None:
    saved = str(profile.get("cover_letter") or "").strip()
    if saved:
        return saved

    prefs = profile.get("preferences") if isinstance(profile.get("preferences"), dict) else {}
    if prefs.get("generate_cover_letter") or AUTO_GENERATE_COVER_LETTER:
        return _generate_cover_letter(profile, job)
    return None


def _generate_cover_letter(profile: dict[str, Any], job: dict[str, Any]) -> str | None:
    """Generate a conservative cover letter from saved CV data only."""
    contact = profile.get("contact") if isinstance(profile.get("contact"), dict) else {}
    name = str(contact.get("name") or "").strip()
    title = str(job.get("title") or "the position").strip()
    company = str(job.get("company") or "your company").strip()
    skills = profile.get("skills")
    skill_sample = ""
    if isinstance(skills, dict):
        for values in skills.values():
            if isinstance(values, list) and values:
                skill_sample = ", ".join(str(v) for v in values[:5])
                break

    lines = [
        f"Dear Hiring Team at {company},",
        "",
        f"My name is {name or 'the applicant'}. I am writing to express my interest in the {title} role.",
    ]
    if skill_sample:
        lines.append(f"My background includes experience with {skill_sample}.")
    lines.extend([
        "I believe my experience aligns with the requirements described in the job posting.",
        "",
        "Thank you for your consideration.",
        name or "",
    ])
    return "\n".join(lines).strip()


def validate_cv_owns_job(
    cv_id: str,
    job_id: int,
    db_path: Path,
    *,
    registry_db: Path | None = None,
) -> dict[str, Any]:
    registry = registry_db or db.REGISTRY_DB_PATH
    if db.get_cv(cv_id, db_path=registry) is None:
        raise ApplicationError("קורות חיים לא נמצאו", status_code=404, code="cv_not_found")

    match = db.get_cv_job_match(cv_id, job_id, db_path=db_path)
    if match is None:
        raise ApplicationError(
            "המשרה לא שייכת לתוצאות הסריקה של קורות החיים האלה",
            status_code=403,
            code="job_not_in_matches",
        )

    job = db.get_job_by_id(job_id, db_path=db_path)
    if job is None:
        raise ApplicationError("משרה לא נמצאה", status_code=404, code="job_not_found")
    if not job.get("job_url"):
        raise ApplicationError("למשרה אין קישור להגשה", status_code=400, code="no_job_url")
    return job


def check_duplicate_application(
    cv_id: str,
    job_id: int,
    *,
    force: bool = False,
    db_path: Path,
) -> dict[str, Any] | None:
    latest = db.get_latest_job_application(cv_id, job_id, db_path=db_path)
    if latest is None:
        return None

    if latest["status"] == db.JOB_APP_IN_PROGRESS:
        raise ApplicationError(
            "הגשה כבר בתהליך עבור משרה זו",
            status_code=409,
            code="application_in_progress",
        )

    if latest["status"] == db.JOB_APP_SUBMITTED and not force:
        raise ApplicationError(
            "כבר הוגשו קורות חיים למשרה זו עם פרופיל קורות החיים הזה. "
            "לאשר הגשה חוזרת?",
            status_code=409,
            code="duplicate_application",
        )

    return latest


def start_application(
    cv_id: str,
    job_id: int,
    *,
    force: bool = False,
    db_path: Path,
    registry_db: Path | None = None,
) -> dict[str, Any]:
    check_rate_limit(cv_id)
    job = validate_cv_owns_job(cv_id, job_id, db_path, registry_db=registry_db)
    previous = check_duplicate_application(cv_id, job_id, force=force, db_path=db_path)

    attempt_number = 1
    if previous is not None:
        attempt_number = int(previous.get("attempt_number") or 0) + 1

    application_id = uuid.uuid4().hex
    app = db.create_job_application(
        application_id,
        cv_id,
        job_id,
        application_url=job.get("job_url"),
        attempt_number=attempt_number,
        db_path=db_path,
    )
    return {"application": app, "job": job}


def public_application(app: dict[str, Any], steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "application_id": app["id"],
        "cv_id": app["cv_id"],
        "job_id": app["job_id"],
        "status": app["status"],
        "application_url": app.get("application_url"),
        "started_at": app.get("started_at"),
        "completed_at": app.get("completed_at"),
        "submitted_at": app.get("submitted_at"),
        "failure_reason": app.get("failure_reason"),
        "failure_category": app.get("failure_category"),
        "requires_user_action_reason": app.get("requires_user_action_reason"),
        "external_confirmation_text": app.get("external_confirmation_text"),
        "external_confirmation_url": app.get("external_confirmation_url"),
        "attempt_number": app.get("attempt_number"),
        "provider_name": app.get("provider_name"),
        "current_step_url": app.get("current_step_url"),
        "created_at": app.get("created_at"),
        "updated_at": app.get("updated_at"),
        "steps": steps,
    }


def get_application_for_cv(
    cv_id: str,
    application_id: str,
    db_path: Path,
) -> dict[str, Any]:
    app = db.get_job_application(application_id, db_path=db_path)
    if app is None or app["cv_id"] != cv_id:
        raise ApplicationError("הגשה לא נמצאה", status_code=404, code="not_found")
    steps = db.get_job_application_steps(application_id, db_path=db_path)
    return public_application(app, steps)


def get_job_application_status(
    cv_id: str,
    job_id: int,
    db_path: Path,
) -> dict[str, Any] | None:
    validate_cv_owns_job(cv_id, job_id, db_path)
    app = db.get_latest_job_application(cv_id, job_id, db_path=db_path)
    if app is None:
        return None
    steps = db.get_job_application_steps(app["id"], db_path=db_path)
    return public_application(app, steps)
