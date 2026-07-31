"""Human writing stage orchestrator.

Pipeline (after claim validation):
  Human Resume Writer
  → Senior Recruiter Review (≤3 cycles)
  → Grammar Validation
  → Style / Writing Quality Validation
  → AI Writing Detection
  → ATS presentation validation (structure)
  → Final quality gate

Tailoring / fact selection is NOT modified here.
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
    # Detect characters that often break ATS parsers when used as layout hacks
    blob = summary + " ".join(str(s) for s in skills)
    for entry in resume.get("experience") or []:
        if isinstance(entry, dict):
            blob += " ".join(str(b) for b in (entry.get("bullets") or []))
    if "\t\t" in blob or "||||" in blob:
        failures.append("ats_layout_artifacts")
    if any(ord(ch) > 0x2FFF for ch in blob if ch not in "\n\r\t"):
        # Allow common punctuation / Hebrew; flag rare symbol spam
        symbolish = sum(1 for ch in blob if ord(ch) > 0x2FFF)
        if symbolish > 12:
            warnings.append("unusual_symbols")
    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "warnings": warnings,
        "validator": "ATSValidation",
    }


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
) -> dict[str, Any]:
    """Run writer → recruiter review → grammar/style/ATS gates.

    Returns polished resume plus audit trail. Facts remain locked to the
    validated baseline; any factual drift reverts to the baseline text.
    """
    baseline = _sync(validated_resume)
    cycles: list[dict[str, Any]] = []
    review_feedback: dict[str, Any] | None = None
    sections: list[str] | None = None

    writer_result = write_human_resume(
        validated_resume=baseline,
        strategy=strategy,
        knowledge_base=knowledge_base,
        output_language=output_language,
        use_cache=use_cache,
        allow_llm=allow_llm,
    )
    current = _sync(writer_result["tailored_resume"])

    for cycle in range(1, max(1, max_review_cycles) + 1):
        review = review_resume(
            resume=current,
            output_language=output_language,
            use_cache=use_cache and cycle == 1,
            allow_llm=allow_llm,
        )
        grammar = validate_grammar(current)
        style = evaluate_writing_quality(current, threshold=style_threshold)
        ai = detect_ai_writing(current)
        ats = _ats_structure_validation(current)
        fact_cmp = compare_facts(baseline, current)

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

        # Normalize + dedupe
        normalized: list[str] = []
        for s in needs_sections:
            key = "summary" if s in {"summary", "professional_summary"} else s
            if key in {"summary", "experience", "projects", "skills"} and key not in normalized:
                normalized.append(key)

        approved = (
            bool(review.get("approved"))
            and bool(grammar.get("passed"))
            and bool(style.get("passed"))
            and bool(ai.get("passed"))
            and bool(ats.get("passed"))
            and bool(fact_cmp.get("passed"))
        )
        if approved or not normalized or cycle >= max_review_cycles:
            break

        logger.info(
            "human_writing: review cycle %s regenerating sections=%s",
            cycle,
            normalized,
        )
        review_feedback = review
        sections = normalized
        writer_result = write_human_resume(
            validated_resume=current if fact_cmp["passed"] else baseline,
            strategy=strategy,
            knowledge_base=knowledge_base,
            output_language=output_language,
            review_feedback=review_feedback,
            sections=sections,
            use_cache=False,
            allow_llm=allow_llm,
        )
        # Always fact-lock against the original validated baseline
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

    # Hard grammar = structural corruption only (not style nits like repeated openings)
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
        # Style/AI are soft-hard: block only on severe AI risk or very low overall
        and int(style.get("overall_score") or 0) >= max(55, style_threshold - 15)
        and int(ai.get("ai_risk") or 0) < 70
    )

    failures: list[str] = []
    if not fact_cmp.get("passed"):
        failures.append("facts_changed")
    for issue in hard_grammar_issues[:6]:
        failures.append(f"grammar:{issue.get('claim_id')}")
    if not ats.get("passed"):
        failures.extend(f"ats:{f}" for f in (ats.get("failures") or []))
    if int(style.get("overall_score") or 0) < max(55, style_threshold - 15):
        failures.append(f"style_score:{style.get('overall_score')}")
    if int(ai.get("ai_risk") or 0) >= 70:
        failures.append(f"ai_risk:{ai.get('ai_risk')}")

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
        "facts_unchanged": bool(fact_cmp.get("passed")),
        "fact_comparison": fact_cmp,
        "export_ready": export_ready,
        "quality_gate_failures": failures,
        "passed": export_ready,
        "stage": "human_resume_writer",
    }
