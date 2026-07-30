"""ResumeValidator — tailoring depth and similarity validation."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.services.similarity import compare_resumes

MAX_SIMILARITY_THRESHOLD = 0.80
MAX_REGENERATION_ATTEMPTS = 2


def validate_tailoring_depth(
    *,
    tailored_resume: dict[str, Any],
    baseline_resume: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Check whether tailoring changed enough vs baseline (original facts)."""
    metrics = compare_resumes(tailored_resume, baseline_resume)
    overall = float(metrics.get("overall_similarity") or 0.0)
    passed = overall <= MAX_SIMILARITY_THRESHOLD

    # Summary must differ meaningfully for each role
    summary_sim = float(metrics.get("summary_similarity") or 0.0)
    summary_ok = summary_sim < 0.85 or bool(
        (tailored_resume.get("professional_summary") or "").strip()
        and summary_sim < 0.95
    )

    return {
        "passed": passed and summary_ok,
        "similarity_metrics": metrics,
        "overall_similarity": overall,
        "summary_similarity": summary_sim,
        "experience_similarity": metrics.get("experience_similarity"),
        "projects_similarity": metrics.get("projects_similarity"),
        "skills_similarity": metrics.get("skills_similarity"),
        "threshold": MAX_SIMILARITY_THRESHOLD,
        "job_family": strategy.get("job_family"),
        "needs_regeneration": not passed,
        "summary_needs_rewrite": not summary_ok,
    }


def should_regenerate(validation: dict[str, Any], attempt: int) -> bool:
    if attempt >= MAX_REGENERATION_ATTEMPTS:
        return False
    return bool(validation.get("needs_regeneration") or validation.get("summary_needs_rewrite"))
