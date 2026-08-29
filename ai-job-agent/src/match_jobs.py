"""ATS-style job matching — deterministic scoring only (no per-job AI).

Each job is analyzed with rule-based extraction, then scored by the profile
matcher and ATS engine. AI is used only during CV profile creation.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_client import clamp_score
from ats_candidate import build_ats_candidate
from ats_scorer import (
    score as ats_score,
)
from console_utils import configure_console, safe_print
from collection_report import emit_job_found, emit_match_summary, emit_status_update
from config import AGENT_CV_ID, AGENT_SCAN_ID, AGENT_USER_ID, AI_RERANK_ENABLED
from db import (
    cv_job_needs_matching,
    get_all_jobs,
    init_db,
    job_needs_analysis,
    job_needs_matching,
    mark_all_jobs_for_rematch,
    refresh_cv_job_match_scan,
    set_cv_last_scan,
    update_job_profile,
    update_match_result,
    upsert_cv_job_match,
    workspace_scope_id,
)
from job_analyzer import (
    JobProfile,
    analyze_job,
    job_profile_hash,
    parse_stored_job_profile,
)
from job_classifier import classify_job_with_strategy, score_to_action, score_to_decision
from job_identity import compute_candidate_strategy_hash
from multilingual_normalizer import register_profile_terms
from parse_cv import load_cv_profile
from pipeline_state import load_pipeline_state, save_pipeline_state
from profile_matcher import score as profile_score
from profile_utils import load_profile
from role_analyzer import fallback_matching_strategy, load_ai_roles, load_matching_strategy
from universal_profile import build_universal_profile_fallback, get_universal_profile

from match_scoring import blend_match_scores, compute_final_match_score


def _resolved_scan_id() -> int | None:
    """The scan id this run belongs to, from the AGENT_SCAN_ID env var."""
    if not AGENT_SCAN_ID:
        return None
    try:
        return int(AGENT_SCAN_ID)
    except (TypeError, ValueError):
        return None


def _to_cv_match_fields(fields: dict) -> dict:
    """Map job-row match fields onto the cv_job_matches column names."""
    cv_fields = dict(fields)
    cv_fields["matched_skills"] = fields.get("matched_keywords") or fields.get("matched_skills")
    cv_fields["missing_skills"] = fields.get("missing_keywords") or fields.get("missing_skills")
    return cv_fields


def _store_match_result(
    job_id: int,
    fields: dict,
    *,
    cv_id: str | None,
    scan_id: int | None,
) -> int | None:
    """Persist a match result per-CV (multi-CV mode) or globally (legacy mode).

    Returns the cv_job_matches row id when in multi-CV mode, else None.
    """
    if cv_id:
        return upsert_cv_job_match(
            cv_id, job_id, _to_cv_match_fields(fields), scan_id=scan_id
        )
    update_match_result(job_id, fields)
    return None


def _parse_json_list(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _job_stream_payload(
    job: dict,
    fields: dict,
    *,
    match_id: int | None,
    scan_id: int | None,
) -> dict:
    """Build the public match shape streamed to the UI via SSE."""
    from date_utils import inject_posted_date_header, normalize_posted_date

    posted_date = normalize_posted_date(
        job.get("posted_date")
        or job.get("first_seen_at")
        or job.get("collected_at")
        or job.get("created_at"),
        default_to_today=True,
    )
    full = (job.get("full_description") or "").strip()
    body = full if full else (job.get("description") or "").strip()
    description = inject_posted_date_header(body, posted_date)

    matched_skills = (
        _parse_json_list(fields.get("matched_keywords"))
        or _parse_json_list(fields.get("matched_skills"))
        or _parse_json_list(fields.get("ai_strengths"))
    )
    missing_skills = (
        _parse_json_list(fields.get("missing_keywords"))
        or _parse_json_list(fields.get("missing_skills"))
        or _parse_json_list(fields.get("ai_missing_skills"))
    )

    return {
        "match_id": match_id,
        "job_id": job.get("id"),
        "scan_id": scan_id,
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "job_url": job.get("job_url"),
        "source": job.get("source"),
        "description": description,
        "posted_date": posted_date,
        "job_created_at": posted_date
        or job.get("created_at")
        or job.get("first_seen_at")
        or job.get("collected_at"),
        "match_score": fields.get("match_score"),
        "match_reason": fields.get("match_reason"),
        "explanation": fields.get("ai_explanation") or fields.get("match_reason"),
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "score_label": fields.get("ats_score_label"),
        "missing_mandatory": _parse_json_list(fields.get("ats_missing_mandatory")),
        "relevant_experience": _parse_json_list(fields.get("ats_relevant_experience")),
        "score_reasons": _parse_json_list(fields.get("ats_reasons")),
        "cv_improvements": _parse_json_list(fields.get("ats_improvements")),
        "is_potential_junior_match": bool(fields.get("is_potential_junior_match")),
        "has_tailored_cv": False,
        "tailored_cv_updated_at": None,
        "application_status": "not_sent",
        "application_notes": None,
        "job_application": None,
        "updated_at": None,
        "streamed": True,
    }


def _emit_scored_job(
    job: dict,
    fields: dict,
    *,
    match_id: int | None,
    scan_id: int | None,
) -> None:
    try:
        emit_job_found(
            _job_stream_payload(job, fields, match_id=match_id, scan_id=scan_id)
        )
    except Exception:  # noqa: BLE001 — streaming must not fail matching
        pass


def _ensure_job_profile(job: dict, *, use_ai: bool = False) -> JobProfile:
    """Return structured job profile using rule-based extraction only."""
    content_hash = job_profile_hash(job)
    stored = parse_stored_job_profile(job.get("job_profile"))
    stored_hash = job.get("job_profile_hash") or ""

    if stored and stored_hash == content_hash and job.get("is_analyzed"):
        return stored

    profile = analyze_job(job, use_ai=use_ai)
    update_job_profile(
        job["id"],
        json.dumps(profile.to_dict(), ensure_ascii=False),
        content_hash,
    )
    job["job_profile"] = json.dumps(profile.to_dict(), ensure_ascii=False)
    job["job_profile_hash"] = content_hash
    job["is_analyzed"] = 1
    return profile


def _blend_scores(profile_match_score: int, ats_match_score: int) -> int:
    return blend_match_scores(profile_match_score, ats_match_score)


def _combined_db_fields(
    *,
    final_score: int,
    score_label: str,
    profile_result,
    ats_result,
    strategy_hash: str,
    fallback_score: int,
) -> dict:
    """Merge profile matcher + ATS results into DB-ready fields (JSON-serialized lists)."""
    fields = ats_result.to_db_fields(
        strategy_hash=strategy_hash,
        fallback_score=fallback_score,
    )

    decision = score_to_decision(final_score)
    action = score_to_action(final_score)
    reasons = list(profile_result.score_reasons[:4]) + list(ats_result.score_reasons[:3])
    explanation = "; ".join(reasons[:6]) if reasons else score_label

    matched_skills = list(
        dict.fromkeys(profile_result.matched_skills + ats_result.matched_required_skills)
    )
    missing_skills = list(
        dict.fromkeys(profile_result.missing_skills + ats_result.missing_required_skills)
    )

    fields.update({
        "match_score": final_score,
        "match_reason": (
            f"[Profile+ATS] {score_label} ({final_score}); "
            f"decision: {decision} -> {action}; {explanation}"
        ),
        "match_method": "profile_ats",
        "match_category": "profile_ats",
        "matched_keywords": json.dumps(matched_skills, ensure_ascii=False),
        "missing_keywords": json.dumps(missing_skills, ensure_ascii=False),
        "ai_decision": decision,
        "ai_strengths": json.dumps(matched_skills, ensure_ascii=False),
        "ai_missing_skills": json.dumps(missing_skills, ensure_ascii=False),
        "ai_recommended_action": action,
        "ai_explanation": explanation,
        "ats_score_label": score_label,
    })
    return fields


@dataclass
class MatchContext:
    """Shared, once-per-scan state needed to score any individual job.

    Building this (loading profile/strategy/universal profile/ATS candidate)
    is relatively expensive, so it is computed once per scan and then reused
    by :func:`score_one_job` for every job — whether scored in a batch via
    :func:`match_all_jobs` or inline, immediately after each job is collected.
    """

    cv_id: str | None
    scan_id: int | None
    profile: dict
    cv_profile: dict
    strategy: dict
    strategy_hash: str
    universal: dict
    candidate: Any
    min_score: int


def build_match_context(
    *,
    cv_id: str | None = None,
    scan_id: int | None = None,
) -> MatchContext:
    """Load and precompute everything :func:`score_one_job` needs, once per scan."""
    if cv_id is None:
        if AGENT_USER_ID:
            cv_id = workspace_scope_id(AGENT_USER_ID)
        else:
            cv_id = AGENT_CV_ID or None
    if scan_id is None:
        scan_id = _resolved_scan_id()

    profile = load_profile()
    cv_profile = load_cv_profile()
    strategy = load_matching_strategy()
    if not strategy:
        strategy = fallback_matching_strategy(profile, cv_profile)

    strategy_hash = compute_candidate_strategy_hash(profile, strategy)
    stored_hash = load_pipeline_state().get("candidate_strategy_hash")
    if cv_id is None and stored_hash and stored_hash != strategy_hash:
        invalidated = mark_all_jobs_for_rematch()
        safe_print(
            f"Matching strategy changed — marked {invalidated} job(s) for rematch "
            f"(hash {stored_hash[:8]}... -> {strategy_hash[:8]}...)"
        )

    universal = get_universal_profile(cv_profile)
    if not universal:
        universal = build_universal_profile_fallback(cv_profile)
    register_profile_terms(universal)

    candidate = build_ats_candidate(cv_profile)
    min_score = profile.get("min_match_score", 0)

    return MatchContext(
        cv_id=cv_id,
        scan_id=scan_id,
        profile=profile,
        cv_profile=cv_profile,
        strategy=strategy,
        strategy_hash=strategy_hash,
        universal=universal,
        candidate=candidate,
        min_score=min_score,
    )


def score_one_job(job: dict, ctx: MatchContext, *, rematch: bool = False) -> dict | None:
    """Analyze, score, persist and stream a single job using a shared context.

    Reused both by the batch :func:`match_all_jobs` loop and by the inline
    collect->enrich->match pipeline so a job scored right after collection
    behaves identically to one scored in a full batch run. Returns ``None``
    when the job is missing an id, otherwise a small dict describing what
    happened: ``{"action": "skipped"}`` or
    ``{"action": "scored", "analyzed": bool, "final_score": int, "matched": bool, "match_id": int|None}``.
    """
    if not job or job.get("id") is None:
        return None

    cv_id = ctx.cv_id
    scan_id = ctx.scan_id

    if cv_id:
        needs = cv_job_needs_matching(
            cv_id, job["id"], current_strategy_hash=ctx.strategy_hash, rematch=rematch
        )
    else:
        needs = job_needs_matching(
            job, current_strategy_hash=ctx.strategy_hash, rematch=rematch
        )
    if not needs:
        # Keep already-scored jobs visible on the current scan in the UI.
        if cv_id and scan_id is not None:
            refresh_cv_job_match_scan(cv_id, int(job["id"]), int(scan_id))
        return {"action": "skipped"}

    analyzed = job_needs_analysis(job)
    job_profile = _ensure_job_profile(job, use_ai=False)

    legacy_result = classify_job_with_strategy(
        job, profile=ctx.profile, cv_profile=ctx.cv_profile, strategy=ctx.strategy
    )
    fallback_score = legacy_result.match_score

    pm_result = profile_score(ctx.universal, job, job_profile)
    ats_result = ats_score(
        ctx.candidate,
        job_profile,
        job,
        fallback_score=fallback_score,
    )

    final_score = compute_final_match_score(
        pm_result.score,
        ats_result,
        profile_exclusion_hit=pm_result.exclusion_hit,
    )

    score_label = pm_result.score_label if final_score == pm_result.score else ats_result.score_label
    if final_score >= 85:
        score_label = "Excellent Match"
    elif final_score >= 70:
        score_label = "Good Match"
    elif final_score >= 50:
        score_label = "Partial Match"
    elif ats_result.is_potential_junior_match:
        score_label = "Potential Match"
    else:
        score_label = "Weak Match"

    fields = _combined_db_fields(
        final_score=final_score,
        score_label=score_label,
        profile_result=pm_result,
        ats_result=ats_result,
        strategy_hash=ctx.strategy_hash,
        fallback_score=fallback_score,
    )
    fields["is_potential_junior_match"] = (
        1 if ats_result.is_potential_junior_match else 0
    )
    match_id = _store_match_result(
        job["id"], fields, cv_id=cv_id, scan_id=scan_id
    )
    _emit_scored_job(job, fields, match_id=match_id, scan_id=scan_id)

    safe_print(
        f"  [{final_score}] {score_label} "
        f"(profile={pm_result.score}, ats={ats_result.ats_score}): "
        f"{job.get('title', '')} @ {job.get('company', '')}"
    )

    return {
        "action": "scored",
        "analyzed": analyzed,
        "final_score": final_score,
        "matched": final_score >= ctx.min_score,
        "match_id": match_id,
    }


def match_all_jobs(
    *,
    rematch: bool = False,
    ai_rerank: bool = False,
    cv_id: str | None = None,
    scan_id: int | None = None,
) -> dict[str, int]:
    """Score jobs using deterministic profile matcher + ATS engine (no per-job AI)."""
    configure_console()
    ctx = build_match_context(cv_id=cv_id, scan_id=scan_id)
    jobs = get_all_jobs()
    emit_status_update(f"מחשב ציוני התאמה ({len(jobs)} משרות)…")

    stats = {
        "total": len(jobs),
        "matched": 0,
        "skipped": 0,
        "below_min": 0,
        "ats_scored": 0,
        "analyzed": 0,
        "ai_reranked": 0,
    }

    for job in jobs:
        result = score_one_job(job, ctx, rematch=rematch)
        if result is None:
            continue
        if result["action"] == "skipped":
            stats["skipped"] += 1
            safe_print(
                f"Reusing prior match for current scan: "
                f"{job.get('title', '')} @ {job.get('company', '')}"
            )
            continue
        if result["analyzed"]:
            stats["analyzed"] += 1
        stats["ats_scored"] += 1
        if result["matched"]:
            stats["matched"] += 1
        else:
            stats["below_min"] += 1

    if ai_rerank and AI_RERANK_ENABLED:
        safe_print("\nAI rerank is disabled — matching is fully deterministic.")

    save_pipeline_state({"candidate_strategy_hash": ctx.strategy_hash})
    if ctx.cv_id:
        set_cv_last_scan(ctx.cv_id)
    return stats


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Match jobs using deterministic profile + ATS scoring (no per-job AI)"
    )
    parser.add_argument(
        "--rematch",
        action="store_true",
        help="Re-match all jobs even if already matched",
    )
    parser.add_argument(
        "--ai-rerank",
        action="store_true",
        help="(Deprecated) AI rerank is disabled; matching is deterministic",
    )
    args = parser.parse_args()
    configure_console()

    safe_print("AI Job Agent — deterministic job matching")
    safe_print("Mode: rule-based extraction + profile matcher + ATS scoring (no per-job AI)")

    init_db()
    profile = load_profile()
    ai_roles = load_ai_roles()
    min_score = profile.get("min_match_score", 0)

    strategy = load_matching_strategy()
    if strategy:
        categories = len(strategy.get("job_categories", []))
        safe_print(f"Strategy: {strategy.get('source', 'unknown')} ({categories} categories)")
    else:
        safe_print("No matching strategy — run: python src/analyze_roles.py")

    if ai_roles:
        roles = [r["role"] for r in ai_roles.get("best_fit_roles", [])[:4]]
        safe_print(f"Best-fit roles: {', '.join(roles) or 'none'}")

    try:
        stats = match_all_jobs(rematch=args.rematch, ai_rerank=args.ai_rerank)
    except Exception as exc:
        safe_print(f"\nError during job matching: {exc}", file=sys.stderr)
        sys.exit(1)

    processed = stats["total"] - stats["skipped"]
    safe_print(f"\nJobs in database: {stats['total']}")
    safe_print(f"  Processed this run: {processed}")
    safe_print(f"  Skipped (already matched): {stats['skipped']}")
    safe_print(f"  Jobs analyzed: {stats['analyzed']}")
    safe_print(f"  Scored: {stats['ats_scored']}")
    safe_print(f"  Meets min_match_score ({min_score}): {stats['matched']}")
    safe_print(f"  Below threshold: {stats['below_min']}")
    emit_match_summary(
        {
            "new_jobs_scored": stats["ats_scored"],
            "new_matches": stats["matched"],
            "below_min": stats["below_min"],
            "skipped_existing": stats["skipped"],
            "total_jobs": stats["total"],
        }
    )
    safe_print("\nRun: python src/list_jobs.py --why")


if __name__ == "__main__":
    main()
