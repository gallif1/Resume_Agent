"""Human writing stage orchestrator.

Pipeline (after claim validation):
  Human Resume Writer
  → Senior Recruiter Review (≤3 cycles)
  → Grammar / Style / AI / ATS gates
  → Resume Quality Score
  → Regenerate ONLY weak sections until threshold or cycle cap

Tailoring / fact selection is NOT modified here.
Optional Hiring Manager feedback can drive an extra refinement pass.
"""

from __future__ import annotations

import logging
from typing import Any

from intelligent_tailoring.services.human_resume_writer import write_human_resume
from intelligent_tailoring.services.senior_recruiter_review import (
    MAX_REVIEW_CYCLES,
    review_resume,
)
from intelligent_tailoring.writing.ai_detector import detect_ai_writing
from intelligent_tailoring.writing.fact_lock import compare_facts, enforce_fact_lock
from intelligent_tailoring.writing.grammar_validator import validate_grammar
from intelligent_tailoring.writing.resume_quality_score import (
    DEFAULT_QUALITY_THRESHOLD,
    evaluate_resume_quality,
)
from intelligent_tailoring.writing.style_validator import (
    DEFAULT_THRESHOLD,
    evaluate_writing_quality,
)

logger = logging.getLogger("intelligent_tailoring.writing_pipeline")


def _sync(resume: dict[str, Any]) -> dict[str, Any]:
    out = dict(resume or {})
    summary = str(out.get("professional_summary") or out.get("summary") or "")
    out["professional_summary"] = summary
    out["summary"] = summary
    return out


def _ats_structure_validation(resume: dict[str, Any]) -> dict[str, Any]:
    """Lightweight ATS-oriented structure checks (no tables/columns concerns)."""
    failures: list[str] = []
    warnings: list[str] = []
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    if not summary:
        failures.append("missing_summary")
    skills = resume.get("skills") or []
    if not skills:
        warnings.append("no_skills_section")
    blob = summary + " ".join(str(s) for s in skills)
    for entry in resume.get("experience") or []:
        if isinstance(entry, dict):
            blob += " ".join(str(b) for b in (entry.get("bullets") or []))
    if "\t\t" in blob or "||||" in blob:
        failures.append("ats_layout_artifacts")
    if any(ord(ch) > 0x2FFF for ch in blob if ch not in "\n\r\t"):
        symbolish = sum(1 for ch in blob if ord(ch) > 0x2FFF)
        if symbolish > 12:
            warnings.append("unusual_symbols")
    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "warnings": warnings,
        "validator": "ATSValidation",
    }


def _compose_writer_feedback(
    *,
    review: dict[str, Any],
    grammar: dict[str, Any],
    style: dict[str, Any],
    ai: dict[str, Any],
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge recruiter + validator guidance into one feedback object for the writer."""
    issues = list(review.get("issues") or [])
    for item in grammar.get("issues") or []:
        issues.append(
            {
                "section": item.get("section") or "overall",
                "problem": ",".join(item.get("patterns") or [])[:160],
                "guidance": "Fix grammar/awkward wording without changing facts.",
            }
        )
    for dim, score in (style.get("weak_dimensions") or {}).items():
        issues.append(
            {
                "section": "overall",
                "problem": f"{dim} score {score} below threshold",
                "guidance": f"Improve {dim.replace('_', ' ')} while keeping all facts identical.",
            }
        )
    for signal in ai.get("signals") or []:
        issues.append(
            {
                "section": "summary" if "summary" in str(signal) else "experience",
                "problem": str(signal),
                "guidance": "Rewrite to sound naturally human; remove AI clichés and repetition.",
            }
        )
    feedback = dict(review)
    feedback["issues"] = issues[:16]
    if quality:
        feedback["quality_score"] = {
            "overall_score": quality.get("overall_score"),
            "weak_sections": quality.get("weak_sections"),
            "dimensions": quality.get("dimensions"),
        }
        feedback["summary_feedback"] = (
            str(feedback.get("summary_feedback") or "")
            + f" Quality score {quality.get('overall_score')}/100."
        ).strip()
    return feedback


def _normalize_sections(sections: list[str]) -> list[str]:
    normalized: list[str] = []
    for s in sections:
        key = "summary" if s in {"summary", "professional_summary"} else s
        if key in {"summary", "experience", "projects", "skills"} and key not in normalized:
            normalized.append(key)
    return normalized


def run_human_writing_stage(
    *,
    validated_resume: dict[str, Any],
    strategy: dict[str, Any] | None = None,
    knowledge_base: Any = None,
    output_language: str = "en",
    use_cache: bool = True,
    allow_llm: bool = True,
    style_threshold: int = DEFAULT_THRESHOLD,
    max_review_cycles: int = MAX_REVIEW_CYCLES,
    hiring_manager_feedback: dict[str, Any] | None = None,
    review_feedback: dict[str, Any] | None = None,
    highlight_plan: dict[str, Any] | None = None,
    evidence_inventory: dict[str, Any] | None = None,
    quality_threshold: int = DEFAULT_QUALITY_THRESHOLD,
) -> dict[str, Any]:
    """Run writer → recruiter review → grammar/style/ATS/quality gates.

    Returns polished resume plus audit trail. Facts remain locked to the
    validated baseline; any factual drift reverts to the baseline text.
    """
    baseline = _sync(validated_resume)
    cycles: list[dict[str, Any]] = []
    seeded_review = dict(review_feedback) if review_feedback else None
    review_feedback = seeded_review
    sections: list[str] | None = None
    if seeded_review:
        sections = _normalize_sections(
            list(seeded_review.get("sections_to_regenerate") or [])
            + list(seeded_review.get("sections_to_strengthen") or [])
        ) or None
    quality_report: dict[str, Any] = {}

    writer_result = write_human_resume(
        validated_resume=baseline,
        strategy=strategy,
        knowledge_base=knowledge_base,
        output_language=output_language,
        review_feedback=review_feedback,
        hiring_manager_feedback=hiring_manager_feedback,
        sections=sections,
        use_cache=use_cache,
        allow_llm=allow_llm,
    )
    current = _sync(writer_result["tailored_resume"])

    for cycle in range(1, max(1, max_review_cycles) + 1):
        review = review_resume(
            resume=current,
            output_language=output_language,
            use_cache=use_cache and cycle == 1 and not hiring_manager_feedback,
            allow_llm=allow_llm,
        )
        grammar = validate_grammar(current)
        style = evaluate_writing_quality(current, threshold=style_threshold)
        ai = detect_ai_writing(current)
        ats = _ats_structure_validation(current)
        fact_cmp = compare_facts(baseline, current)
        quality_report = evaluate_resume_quality(
            current,
            strategy=strategy,
            highlight_plan=highlight_plan or (strategy or {}).get("highlight_plan"),
            evidence_inventory=evidence_inventory
            or (strategy or {}).get("evidence_inventory"),
            recruiter_review=review,
            hiring_manager=hiring_manager_feedback,
            threshold=quality_threshold,
        )

        cycle_report = {
            "cycle": cycle,
            "review": review,
            "grammar": {
                "passed": grammar["passed"],
                "score": grammar["score"],
                "affected_sections": grammar.get("affected_sections"),
            },
            "style": {
                "passed": style["passed"],
                "overall_score": style["overall_score"],
                "weak_dimensions": style.get("weak_dimensions"),
                "affected_sections": style.get("affected_sections"),
            },
            "ai_detector": {
                "passed": ai["passed"],
                "ai_risk": ai["ai_risk"],
                "signals": ai.get("signals"),
                "affected_sections": ai.get("affected_sections"),
            },
            "ats": ats,
            "facts_unchanged": fact_cmp["passed"],
            "quality_score": {
                "overall_score": quality_report.get("overall_score"),
                "passed": quality_report.get("passed"),
                "weak_sections": quality_report.get("weak_sections"),
                "dimensions": quality_report.get("dimensions"),
            },
        }
        cycles.append(cycle_report)

        needs_sections: list[str] = []
        if not review.get("approved"):
            needs_sections.extend(review.get("sections_to_regenerate") or [])
        if grammar.get("regeneration_required"):
            needs_sections.extend(grammar.get("affected_sections") or [])
        if style.get("regeneration_required"):
            needs_sections.extend(style.get("affected_sections") or [])
        if ai.get("regeneration_required"):
            needs_sections.extend(ai.get("affected_sections") or [])
        if not quality_report.get("passed"):
            needs_sections.extend(quality_report.get("weak_sections") or [])
        if hiring_manager_feedback and cycle == 1:
            needs_sections.extend(hiring_manager_feedback.get("weakest_sections") or [])

        normalized = _normalize_sections(needs_sections)

        approved = (
            bool(review.get("approved"))
            and bool(grammar.get("passed"))
            and bool(style.get("passed"))
            and bool(ai.get("passed"))
            and bool(ats.get("passed"))
            and bool(fact_cmp.get("passed"))
            and bool(quality_report.get("passed"))
        )
        if approved or not normalized or cycle >= max_review_cycles:
            break

        logger.info(
            "human_writing: review cycle %s regenerating sections=%s quality=%s",
            cycle,
            normalized,
            quality_report.get("overall_score"),
        )
        review_feedback = _compose_writer_feedback(
            review=review,
            grammar=grammar,
            style=style,
            ai=ai,
            quality=quality_report,
        )
        sections = normalized
        writer_result = write_human_resume(
            validated_resume=current if fact_cmp["passed"] else baseline,
            strategy=strategy,
            knowledge_base=knowledge_base,
            output_language=output_language,
            review_feedback=review_feedback,
            hiring_manager_feedback=hiring_manager_feedback,
            quality_score=quality_report,
            sections=sections,
            use_cache=False,
            allow_llm=allow_llm,
        )
        locked = enforce_fact_lock(baseline, writer_result["tailored_resume"])
        current = _sync(locked["resume"])

    # Final validators snapshot
    grammar = validate_grammar(current)
    style = evaluate_writing_quality(current, threshold=style_threshold)
    ai = detect_ai_writing(current)
    ats = _ats_structure_validation(current)
    fact_cmp = compare_facts(baseline, current)
    if not fact_cmp["passed"]:
        locked = enforce_fact_lock(baseline, current)
        current = _sync(locked["resume"])
        fact_cmp = compare_facts(baseline, current)

    quality_report = evaluate_resume_quality(
        current,
        strategy=strategy,
        highlight_plan=highlight_plan or (strategy or {}).get("highlight_plan"),
        evidence_inventory=evidence_inventory
        or (strategy or {}).get("evidence_inventory"),
        recruiter_review=(cycles[-1].get("review") if cycles else None),
        hiring_manager=hiring_manager_feedback,
        threshold=quality_threshold,
    )

    hard_grammar_issues = [
        i
        for i in (grammar.get("issues") or [])
        if any(
            str(p).startswith(
                (
                    "using_",
                    "with_",
                    "to_period",
                    "on_period",
                    "via_period",
                    "empty_",
                    "dangling_",
                    "placeholder",
                    "duplicate_sentence",
                    "repeated_ngram",
                    "keyword_stuffing",
                    "fragment",
                )
            )
            or str(p)
            in {
                "duplicate_sentence",
                "repeated_ngram",
                "keyword_stuffing",
                "title_case_soup",
            }
            for p in (i.get("patterns") or [])
        )
    ]

    export_ready = (
        bool(fact_cmp.get("passed"))
        and len(hard_grammar_issues) == 0
        and bool(ats.get("passed"))
        and int(style.get("overall_score") or 0) >= max(60, style_threshold - 10)
        and int(ai.get("ai_risk") or 0) < 60
        and int(quality_report.get("overall_score") or 0)
        >= max(60, quality_threshold - 12)
    )

    failures: list[str] = []
    if not fact_cmp.get("passed"):
        failures.append("facts_changed")
    for issue in hard_grammar_issues[:6]:
        failures.append(f"grammar:{issue.get('claim_id')}")
    if not ats.get("passed"):
        failures.extend(f"ats:{f}" for f in (ats.get("failures") or []))
    if int(style.get("overall_score") or 0) < max(60, style_threshold - 10):
        failures.append(f"style_score:{style.get('overall_score')}")
    if int(ai.get("ai_risk") or 0) >= 60:
        failures.append(f"ai_risk:{ai.get('ai_risk')}")
    if int(quality_report.get("overall_score") or 0) < max(60, quality_threshold - 12):
        failures.append(f"quality_score:{quality_report.get('overall_score')}")
    dims = dict(quality_report.get("dimensions") or {})
    if int(dims.get("interview_probability") or 100) < max(55, quality_threshold - 15):
        failures.append(
            f"interview_probability:{dims.get('interview_probability')}"
        )
    if int(dims.get("twenty_second_screen") or 100) < max(55, quality_threshold - 15):
        failures.append(f"twenty_second_screen:{dims.get('twenty_second_screen')}")

    return {
        "tailored_resume": current,
        "baseline_resume": baseline,
        "writer": {
            "mode": writer_result.get("mode"),
            "writing_notes": writer_result.get("writing_notes"),
            "fact_lock": writer_result.get("fact_lock"),
        },
        "cycles": cycles,
        "review_cycles": len(cycles),
        "grammar": grammar,
        "style": style,
        "ai_detector": ai,
        "ats_validation": ats,
        "quality_score": quality_report,
        "facts_unchanged": bool(fact_cmp.get("passed")),
        "fact_comparison": fact_cmp,
        "export_ready": export_ready,
        "quality_gate_failures": failures,
        "passed": export_ready,
        "stage": "human_resume_writer",
    }
