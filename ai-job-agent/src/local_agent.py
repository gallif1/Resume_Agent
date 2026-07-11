"""Helpers for running job collection on the user's machine (not on the server)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db
from config import cv_data_dir, cv_db_path
from job_identity import compute_job_identity_key, normalize_job_url

BUNDLE_FILES = (
    "profile.json",
    "cv_profile.json",
    "ai_matching_strategy.json",
    "ai_roles.json",
)


def export_local_agent_bundle(cv_id: str) -> dict[str, Any]:
    """Return per-CV files the local collector needs to run collect_jobs.py."""
    cv_dir = cv_data_dir(cv_id)
    if not cv_dir.is_dir():
        raise FileNotFoundError(f"CV data directory not found: {cv_id}")

    files: dict[str, str] = {}
    for name in BUNDLE_FILES:
        path = cv_dir / name
        if path.is_file():
            files[name] = path.read_text(encoding="utf-8")

    if not files.get("profile.json") and not files.get("cv_profile.json"):
        raise FileNotFoundError(
            "CV profile not found — run parse_cv on the server first (prepare scan)."
        )

    return {"cv_id": cv_id, "files": files}


def write_local_agent_bundle(cv_id: str, bundle: dict[str, Any]) -> Path:
    """Write a downloaded bundle to data/cvs/<cv_id>/."""
    cv_dir = cv_data_dir(cv_id)
    cv_dir.mkdir(parents=True, exist_ok=True)
    files = bundle.get("files") or {}
    for name, content in files.items():
        if name in BUNDLE_FILES and isinstance(content, str):
            (cv_dir / name).write_text(content, encoding="utf-8")
    return cv_dir


def ingest_collected_jobs(
    cv_id: str,
    jobs: list[dict[str, Any]],
    *,
    reset_pool: bool = True,
) -> dict[str, int]:
    """Import jobs collected on the user's machine into the server CV database."""
    if not jobs:
        return {"received": 0, "inserted": 0, "updated": 0, "skipped": 0}

    cv_db = cv_db_path(cv_id)
    db.init_db(cv_db)

    if reset_pool:
        db.reset_cv_job_pool(cv_id)

    inserted = 0
    updated = 0
    skipped = 0
    seen_keys: set[str] = set()

    for job in jobs:
        title = (job.get("title") or "").strip()
        url = normalize_job_url(job.get("job_url") or "")
        if not title or not url:
            skipped += 1
            continue

        company = (job.get("company") or "").strip()
        location = (job.get("location") or "").strip()
        job_key = compute_job_identity_key(url, title, company, location)
        if job_key in seen_keys:
            skipped += 1
            continue
        seen_keys.add(job_key)

        job_id, is_new = db.upsert_collected_job(
            title=title,
            job_url=url,
            company=company or None,
            location=location or None,
            source=job.get("source"),
            description=job.get("description"),
            source_query=job.get("source_query"),
            source_category=job.get("source_category"),
            source_strategy_hash=job.get("source_strategy_hash"),
            db_path=cv_db,
        )

        full_description = (job.get("full_description") or "").strip()
        if job_id is not None and full_description:
            db.update_full_description(job_id, full_description, db_path=cv_db)

        if is_new:
            inserted += 1
        else:
            updated += 1

    return {
        "received": len(jobs),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }


def export_jobs_for_upload(cv_id: str) -> list[dict[str, Any]]:
    """Serialize jobs from a local per-CV database for upload to the server."""
    cv_db = cv_db_path(cv_id)
    if not cv_db.is_file():
        return []

    rows = db.get_all_jobs(db_path=cv_db)
    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append({
            "title": row.get("title"),
            "job_url": row.get("job_url"),
            "company": row.get("company"),
            "location": row.get("location"),
            "source": row.get("source"),
            "description": row.get("description"),
            "full_description": row.get("full_description"),
            "source_query": row.get("source_query"),
            "source_category": row.get("source_category"),
            "source_strategy_hash": row.get("source_strategy_hash"),
        })
    return payload
