"""TailoringReporter — internal tailoring quality report."""

from __future__ import annotations

from typing import Any


def build_tailoring_report(
    *,
    strategy: dict[str, Any],
    scores: dict[str, Any],
    validation: dict[str, Any],
    generated: dict[str, Any],
    original_score: int,
    tailored_score: int,
    regeneration_attempts: int = 0,
    cross_similarity: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Produce internal tailoring report (may be hidden from end user)."""
    change_log = generated.get("change_log") or []
    bullets_rewritten = sum(
        1
        for c in change_log
        if c.get("original_text") and c.get("new_text")
        and c.get("original_text") != c.get("new_text")
    )
    sections_changed = _sections_changed(change_log, generated.get("tailored_resume") or {})

    ats_added = list(generated.get("ats_keywords_added") or [])
    ats_improvement = max(0, tailored_score - original_score)

    similarity = validation.get("similarity_metrics") or {}
    overall_sim = float(validation.get("overall_similarity") or similarity.get("overall_similarity") or 0)

    tailoring_score = _tailoring_quality_score(
        overall_similarity=overall_sim,
        bullets_rewritten=bullets_rewritten,
        ats_improvement=ats_improvement,
        validation_passed=bool(validation.get("passed")),
    )

    return {
        "tailoring_score": tailoring_score,
        "sections_changed": sections_changed,
        "bullets_rewritten": bullets_rewritten,
        "projects_reordered": bool(strategy.get("project_priority")),
        "skills_reordered": bool(strategy.get("skills_to_emphasize")),
        "keywords_inserted": ats_added,
        "ats_score_improvement": ats_improvement,
        "original_match_score": original_score,
        "tailored_match_score": tailored_score,
        "resume_similarity": similarity,
        "overall_similarity": overall_sim,
        "tailoring_quality": _quality_label(tailoring_score),
        "job_family": strategy.get("job_family"),
        "regeneration_attempts": regeneration_attempts,
        "validation_passed": bool(validation.get("passed")),
        "cross_job_similarity": cross_similarity or {},
        "strategy_snapshot": {
            "summary_focus": strategy.get("summary_focus"),
            "experience_focus": strategy.get("experience_focus"),
            "top_projects": strategy.get("top_projects"),
            "skills_to_emphasize": strategy.get("skills_to_emphasize"),
        },
    }


def _sections_changed(change_log: list[dict[str, Any]], resume: dict[str, Any]) -> list[str]:
    sections: set[str] = set()
    for item in change_log:
        orig = str(item.get("original_text") or "")
        new = str(item.get("new_text") or "")
        if orig != new and new:
            if orig in str(resume.get("professional_summary") or "") or new in str(
                resume.get("professional_summary") or ""
            ):
                sections.add("summary")
            if any(new in str(s) for s in (resume.get("skills") or [])):
                sections.add("skills")
            for exp in resume.get("experience") or []:
                if any(new in str(b) for b in (exp.get("bullets") or [])):
                    sections.add("experience")
            for proj in resume.get("projects") or []:
                if any(new in str(b) for b in (proj.get("bullets") or [])):
                    sections.add("projects")
    return sorted(sections)


def _tailoring_quality_score(
    *,
    overall_similarity: float,
    bullets_rewritten: int,
    ats_improvement: int,
    validation_passed: bool,
) -> int:
    base = 40
    if validation_passed:
        base += 20
    # Lower similarity to baseline = more tailoring
    base += int((1.0 - min(overall_similarity, 1.0)) * 25)
    base += min(bullets_rewritten * 3, 15)
    base += min(ats_improvement, 10)
    return max(0, min(100, base))


def _quality_label(score: int) -> str:
    if score >= 80:
        return "excellent"
    if score >= 65:
        return "good"
    if score >= 50:
        return "moderate"
    return "weak"
