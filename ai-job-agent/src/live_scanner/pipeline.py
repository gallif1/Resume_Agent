"""Normalize, dedupe, persist, and optionally match live-scanner jobs."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import db
from config import (
    user_ai_cache_dir,
    user_cv_profile_path,
    user_data_dir,
    user_db_path,
    user_master_profile_path,
    user_profile_prefs_path,
)
from job_identity import normalize_job_url
from live_scanner.collectors.base import CollectedJob
from live_scanner import store
from live_scanner.constants import DISCOVERED_BY, LIFECYCLE_ACTIVE

logger = logging.getLogger("live_scanner.pipeline")


@contextmanager
def workspace_runtime(user_id: str) -> Iterator[Path]:
    """Temporarily point matching/config paths at the user workspace."""
    import config
    import profile_utils
    import role_analyzer

    db_path = user_db_path(user_id)
    user_dir = user_data_dir(user_id)

    snapshots = {
        "config.AGENT_USER_ID": getattr(config, "AGENT_USER_ID", ""),
        "config.DB_PATH": getattr(config, "DB_PATH", None),
        "config.CV_PROFILE_PATH": getattr(config, "CV_PROFILE_PATH", None),
        "config.MASTER_PROFILE_PATH": getattr(config, "MASTER_PROFILE_PATH", None),
        "config.PROFILE_PATH": getattr(config, "PROFILE_PATH", None),
        "config.AI_MATCHING_STRATEGY_PATH": getattr(config, "AI_MATCHING_STRATEGY_PATH", None),
        "config.AI_ROLES_PATH": getattr(config, "AI_ROLES_PATH", None),
        "config.PIPELINE_STATE_PATH": getattr(config, "PIPELINE_STATE_PATH", None),
        "config.AI_CACHE_DIR": getattr(config, "AI_CACHE_DIR", None),
        "db.DB_PATH": getattr(db, "DB_PATH", None),
        "profile_utils.AGENT_USER_ID": getattr(profile_utils, "AGENT_USER_ID", ""),
        "role_analyzer.AI_MATCHING_STRATEGY_PATH": getattr(
            role_analyzer, "AI_MATCHING_STRATEGY_PATH", None
        ),
    }

    try:
        config.AGENT_USER_ID = user_id
        config.DB_PATH = db_path
        config.CV_PROFILE_PATH = user_cv_profile_path(user_id)
        config.MASTER_PROFILE_PATH = user_master_profile_path(user_id)
        config.PROFILE_PATH = user_profile_prefs_path(user_id)
        config.AI_MATCHING_STRATEGY_PATH = user_dir / "ai_matching_strategy.json"
        config.AI_ROLES_PATH = user_dir / "ai_roles.json"
        config.PIPELINE_STATE_PATH = user_dir / "pipeline_state.json"
        config.AI_CACHE_DIR = user_ai_cache_dir(user_id)
        db.DB_PATH = db_path
        profile_utils.AGENT_USER_ID = user_id
        role_analyzer.AI_MATCHING_STRATEGY_PATH = config.AI_MATCHING_STRATEGY_PATH
        yield db_path
    finally:
        config.AGENT_USER_ID = snapshots["config.AGENT_USER_ID"]
        config.DB_PATH = snapshots["config.DB_PATH"]
        config.CV_PROFILE_PATH = snapshots["config.CV_PROFILE_PATH"]
        config.MASTER_PROFILE_PATH = snapshots["config.MASTER_PROFILE_PATH"]
        config.PROFILE_PATH = snapshots["config.PROFILE_PATH"]
        config.AI_MATCHING_STRATEGY_PATH = snapshots["config.AI_MATCHING_STRATEGY_PATH"]
        config.AI_ROLES_PATH = snapshots["config.AI_ROLES_PATH"]
        config.PIPELINE_STATE_PATH = snapshots["config.PIPELINE_STATE_PATH"]
        config.AI_CACHE_DIR = snapshots["config.AI_CACHE_DIR"]
        db.DB_PATH = snapshots["db.DB_PATH"]
        profile_utils.AGENT_USER_ID = snapshots["profile_utils.AGENT_USER_ID"]
        role_analyzer.AI_MATCHING_STRATEGY_PATH = snapshots[
            "role_analyzer.AI_MATCHING_STRATEGY_PATH"
        ]


def persist_collected_job(
    user_id: str,
    source: dict[str, Any],
    job: CollectedJob,
    *,
    is_baseline: bool,
    baseline_created_at: str | None,
    db_path: Path,
    run_match: bool,
) -> dict[str, Any]:
    """Save a job into the shared jobs table + known-job index.

    Returns a result dict:
      action: inserted | duplicate | error
      job_id, is_new, match_score, relevant
    """
    provider = str(source.get("provider") or "")
    source_id = str(source.get("id") or "")
    company = job.company or str(source.get("company_name") or "")
    canonical = normalize_job_url(job.job_url) or job.job_url

    try:
        job_id, is_new = db.upsert_collected_job(
            title=job.title,
            job_url=canonical,
            company=company,
            location=job.location or None,
            source=provider,
            description=job.description or None,
            source_query=f"live:{source_id}",
            source_category=str(source.get("company_name") or ""),
            posted_date=job.posted_date,
            db_path=db_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to upsert live job")
        return {"action": "error", "error": str(exc)}

    if job_id is None:
        # Existing job — still register in known index so future scans skip
        existing = None
        with db.get_connection(db_path) as conn:
            existing = db.find_existing_job(
                conn,
                job_url=canonical,
                title=job.title,
                company=company,
                location=job.location or "",
                owner_cv_id=db.owner_cv_id_for_path(db_path) if db.uses_postgres() else None,
            )
        job_id = int(existing["id"]) if existing else None
        store.upsert_known_job(
            user_id,
            source_id,
            external_job_id=job.external_job_id,
            canonical_url=canonical,
            job_id=job_id,
            is_baseline=is_baseline,
            db_path=db_path,
        )
        if job_id is not None:
            # Surface in the live session feed while scanning (baseline + true inserts).
            # For live scans, skip duplicates so "New Jobs" stays accurate.
            if is_baseline:
                store.add_session_job(
                    user_id,
                    job_id=job_id,
                    is_baseline=True,
                    title=job.title,
                    company=company,
                    location=job.location or "",
                    provider=provider,
                    source_id=source_id,
                    job_url=canonical,
                    match_score=existing.get("match_score") if existing else None,
                    db_path=db_path,
                )
        return {
            "action": "duplicate",
            "job_id": job_id,
            "is_new": False,
            "match_score": existing.get("match_score") if existing else None,
            "relevant": False,
        }

    store.annotate_job_live_metadata(
        job_id,
        discovered_by=DISCOVERED_BY,
        is_baseline=is_baseline,
        baseline_created_at=baseline_created_at,
        external_job_id=job.external_job_id,
        provider=provider,
        live_source_id=source_id,
        lifecycle_status=LIFECYCLE_ACTIVE,
        db_path=db_path,
    )
    store.upsert_known_job(
        user_id,
        source_id,
        external_job_id=job.external_job_id,
        canonical_url=canonical,
        job_id=job_id,
        is_baseline=is_baseline,
        db_path=db_path,
    )

    match_score = None
    relevant = False
    if run_match:
        match_score, relevant = _try_match_job(user_id, job_id, db_path=db_path)

    # Always record in the live session feed (baseline and new), so the UI can
    # stream discoveries. New-job counters / alerts still use is_baseline=0 only.
    store.add_session_job(
        user_id,
        job_id=job_id,
        is_baseline=is_baseline,
        title=job.title,
        company=company,
        location=job.location or "",
        provider=provider,
        source_id=source_id,
        job_url=canonical,
        match_score=match_score,
        db_path=db_path,
    )

    return {
        "action": "inserted",
        "job_id": job_id,
        "is_new": is_new,
        "match_score": match_score,
        "relevant": relevant,
    }


def _try_match_job(
    user_id: str, job_id: int, *, db_path: Path
) -> tuple[int | None, bool]:
    """Run existing score_one_job when a workspace profile exists. Never raises."""
    cv_profile_path = user_cv_profile_path(user_id)
    prefs_path = user_profile_prefs_path(user_id)
    if not cv_profile_path.exists() and not prefs_path.exists():
        return None, False

    try:
        with workspace_runtime(user_id):
            from match_jobs import build_match_context, score_one_job

            row = db.get_job_by_id(job_id, db_path=db_path)
            if not row:
                return None, False
            # Cheap local path: score_one_job uses use_ai=False for analysis first.
            ctx = build_match_context(cv_id=db.workspace_scope_id(user_id), max_age_days=0)
            result = score_one_job(row, ctx)
            if not isinstance(result, dict):
                return None, False
            score = result.get("final_score")
            matched = bool(result.get("matched"))
            try:
                score_int = int(score) if score is not None else None
            except (TypeError, ValueError):
                score_int = None
            return score_int, matched
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live scanner match skipped for job %s: %s", job_id, exc)
        return None, False


def cheap_title_relevance(title: str, keywords: list[str]) -> bool:
    """Local prefilter for expensive baseline matching."""
    if not keywords:
        return True
    hay = (title or "").lower()
    return any(k.lower() in hay for k in keywords if k and len(k) > 2)
