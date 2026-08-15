"""Merged Agent 3 — claim-aware human writing + recruiter review in one LLM call.

Deterministic claim validation still runs before this stage. This module replaces
separate Human Writer + Senior Recruiter LLM round-trips with a single composed
prompt (max two internal repair passes).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ai_client import is_ai_available
from intelligent_tailoring.llm_utils import (
    call_stage_json_with_content_validation,
    record_primary_llm_call,
)
from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_3_SYSTEM,
    MERGED_AGENT_3_PROMPT_VERSION,
    build_agent_3_user_prompt,
)
from intelligent_tailoring.schemas import SchemaValidationError, validate_tailored_resume
from intelligent_tailoring.prompts.human_writer_prompts import (
    sanitize_strategy_for_writer,
)
from intelligent_tailoring.services.human_resume_writer import write_human_resume
from intelligent_tailoring.writing.deterministic_polish import (
    polish_resume_deterministic,
)
from intelligent_tailoring.services.senior_recruiter_review import (
    review_resume,
)
from intelligent_tailoring.writing.fact_lock import compare_facts, enforce_fact_lock
from intelligent_tailoring.writing.writing_pipeline import (
    _ats_structure_validation,
    _compose_writer_feedback,
    _normalize_sections,
    _sync,
)
from intelligent_tailoring.writing.ai_detector import detect_ai_writing
from intelligent_tailoring.writing.grammar_validator import validate_grammar
from intelligent_tailoring.writing.resume_quality_score import (
    DEFAULT_QUALITY_THRESHOLD,
    evaluate_resume_quality,
)
from intelligent_tailoring.writing.style_validator import (
    DEFAULT_THRESHOLD,
    evaluate_writing_quality,
)
from intelligent_tailoring.structured_resume import (
    assign_stable_ids,
    stamp_ids_on_resume,
)
from intelligent_tailoring.structured_validation import (
    repair_structured_resume,
    validate_structured_resume,
)

logger = logging.getLogger("intelligent_tailoring.merged_writing")

MAX_INTERNAL_REPAIR_PASSES = 1


def _coerce_writer_resume(resume: dict[str, Any]) -> dict[str, Any]:
    """Normalize structured-schema aliases from Agent 3 into pipeline shape."""
    from intelligent_tailoring.services.resume_rewriter import _coerce_llm_resume_to_pipeline

    return _coerce_llm_resume_to_pipeline(resume if isinstance(resume, dict) else {})


def _validate_merged_writer(data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or "tailored_resume" not in data:
        raise SchemaValidationError("merged writer missing tailored_resume")
    if isinstance(data.get("tailored_resume"), dict):
        data["tailored_resume"] = _coerce_writer_resume(data["tailored_resume"])
    validate_tailored_resume(data["tailored_resume"])


def _kb_compact(knowledge_base: Any) -> str:
    if knowledge_base is None:
        return "[]"
    facts = getattr(knowledge_base, "facts", None) or []
    rows = []
    for f in list(facts)[:60]:
        data = f.to_dict() if hasattr(f, "to_dict") else (f if isinstance(f, dict) else {})
        rows.append(
            {
                "entry": data.get("source_entry_id"),
                "section": data.get("source_section"),
                "text": str(data.get("original_text") or "")[:160],
                "skills": (data.get("explicit_skills") or [])[:6],
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def run_merged_writing_review(
    *,
    validated_resume: dict[str, Any],
    strategy: dict[str, Any] | None = None,
    knowledge_base: Any = None,
    output_language: str = "en",
    use_cache: bool = True,
    allow_llm: bool = True,
    rejected_claims: list[str] | None = None,
    evidence_compact: str = "",
    review_feedback: dict[str, Any] | None = None,
    sections: list[str] | None = None,
    max_repair_passes: int = MAX_INTERNAL_REPAIR_PASSES,
    style_threshold: int = DEFAULT_THRESHOLD,
    quality_threshold: int = DEFAULT_QUALITY_THRESHOLD,
    highlight_plan: dict[str, Any] | None = None,
    evidence_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One primary LLM call for write+recruiter; deterministic gates afterward."""
    # Resolve source facts for stable-id / contact / fullness checks
    source_facts: dict[str, Any] = {}
    if knowledge_base is not None:
        try:
            from intelligent_tailoring.knowledge_base import knowledge_base_to_resume_facts

            source_facts = knowledge_base_to_resume_facts(knowledge_base) or {}
        except Exception:  # noqa: BLE001
            source_facts = {}
    if not source_facts and isinstance(strategy, dict):
        source_facts = strategy.get("resume_facts") or {}
    # Prefer ids/contact already on the validated resume as the source of truth
    baseline_input = stamp_ids_on_resume(
        validated_resume if isinstance(validated_resume, dict) else {},
        source_facts=source_facts or None,
    )
    if isinstance(baseline_input.get("contact"), dict):
        source_facts = assign_stable_ids(
            {
                **(source_facts or {}),
                "contact": baseline_input.get("contact"),
                "experience_roles": baseline_input.get("experience") or [],
                "projects": baseline_input.get("projects") or [],
                "education": baseline_input.get("education") or [],
                "skills": baseline_input.get("skills") or [],
            }
        )
    elif source_facts:
        source_facts = assign_stable_ids(source_facts)

    # Carry JD snapshot for contamination checks (never as candidate claims).
    jd_blob = ""
    if isinstance(strategy, dict):
        jd_blob = str(
            strategy.get("jd_text") or strategy.get("job_description") or ""
        ).strip()
    if not jd_blob and isinstance(source_facts, dict):
        jd_blob = str(source_facts.get("jd_text") or "").strip()
    if jd_blob and isinstance(source_facts, dict):
        source_facts["jd_text"] = jd_blob

    baseline = _sync(baseline_input)
    deterministic = polish_resume_deterministic(baseline)
    locked = enforce_fact_lock(baseline, deterministic)
    working = _sync(locked["resume"])
    working = stamp_ids_on_resume(working, source_facts=source_facts or None)

    recruiter_review: dict[str, Any] = {}
    validation_warnings: list[dict[str, Any]] = []
    rejected: list[str] = list(rejected_claims or [])
    mode = "deterministic"
    primary_calls = 0
    repair_passes = 0
    structured_validation: dict[str, Any] = {}

    def _validate_content(payload: dict[str, Any]):
        resume = payload.get("tailored_resume") if isinstance(payload, dict) else {}
        return validate_structured_resume(
            resume if isinstance(resume, dict) else {},
            source_facts=source_facts or baseline,
            enforce_fullness=True,
            require_summary=True,
            jd_text=jd_blob,
        )

    if allow_llm and is_ai_available():
        try:
            record_primary_llm_call("human_writing_credibility")
            primary_calls = 1
            safe_strategy = sanitize_strategy_for_writer(strategy or {})
            focus_sections = ",".join(_normalize_sections(sections or []))
            raw = call_stage_json_with_content_validation(
                system_prompt=AGENT_3_SYSTEM,
                user_prompt=build_agent_3_user_prompt(
                    language=output_language or "en",
                    validated_resume_json=json.dumps(working, ensure_ascii=False, indent=2),
                    strategy_compact=json.dumps(safe_strategy, ensure_ascii=False)[:6000],
                    evidence_compact=(evidence_compact or _kb_compact(knowledge_base))[:4000],
                    rejected_claims=json.dumps(rejected[:40], ensure_ascii=False),
                    sections=focus_sections,
                    review_feedback=json.dumps(review_feedback or {}, ensure_ascii=False)[
                        :3000
                    ],
                ),
                validate=_validate_merged_writer,
                content_validate=_validate_content,
                use_cache=use_cache and not review_feedback and not sections,
                cache_namespace=f"{MERGED_AGENT_3_PROMPT_VERSION}_write_review",
                cache_payload=(
                    f"{output_language}|{json.dumps(working, sort_keys=True)[:2500]}|"
                    f"{focus_sections}|{json.dumps(rejected[:10])}"
                ),
                temperature=0.35,
                max_content_retries=1,
            )
            polished = validate_tailored_resume(raw["tailored_resume"]).to_dict()
            if isinstance(raw["tailored_resume"], dict) and isinstance(
                raw["tailored_resume"].get("contact"), dict
            ):
                polished["contact"] = dict(raw["tailored_resume"]["contact"])
            fact_locked = enforce_fact_lock(baseline, polished)
            working = _sync(fact_locked["resume"])
            working = stamp_ids_on_resume(working, source_facts=source_facts or baseline)
            mode = "merged_llm"
            structured_validation = raw.get("_content_validation") or {}
            recruiter_review = (
                raw.get("recruiter_review")
                if isinstance(raw.get("recruiter_review"), dict)
                else {}
            )
            validation_warnings = [
                w
                for w in (raw.get("validation_warnings") or [])
                if isinstance(w, dict)
            ]
            for claim in raw.get("rejected_claims") or []:
                text = str(claim).strip()
                if text and text not in rejected:
                    rejected.append(text)
            if raw.get("_content_validation_failed"):
                working = repair_structured_resume(
                    working, source_facts=source_facts or baseline
                )
        except (SchemaValidationError, Exception) as exc:  # noqa: BLE001
            logger.warning("merged writing LLM failed (%s) — falling back", exc)
            fallback = write_human_resume(
                validated_resume=baseline,
                strategy=strategy,
                knowledge_base=knowledge_base,
                output_language=output_language,
                review_feedback=review_feedback,
                sections=sections,
                use_cache=use_cache,
                allow_llm=allow_llm,
            )
            working = _sync(fallback.get("tailored_resume") or baseline)
            working = stamp_ids_on_resume(working, source_facts=source_facts or baseline)
            mode = "fallback_writer"
            # Recruiter via heuristic only to avoid a second primary LLM call
            recruiter_review = review_resume(
                resume=working,
                output_language=output_language,
                use_cache=use_cache,
                allow_llm=False,
            )

    if not recruiter_review:
        recruiter_review = review_resume(
            resume=working,
            output_language=output_language,
            use_cache=use_cache,
            allow_llm=False,
        )

    # Deterministic quality gates + optional targeted repair (counts toward Agent 3 cap)
    cycles: list[dict[str, Any]] = []
    quality_report: dict[str, Any] = {}
    for pass_idx in range(max(0, max_repair_passes)):
        grammar = validate_grammar(working)
        style = evaluate_writing_quality(working, threshold=style_threshold)
        ai = detect_ai_writing(working)
        ats = _ats_structure_validation(working)
        fact_cmp = compare_facts(baseline, working)
        quality_report = evaluate_resume_quality(
            working,
            strategy=strategy,
            highlight_plan=highlight_plan or (strategy or {}).get("highlight_plan"),
            evidence_inventory=evidence_inventory
            or (strategy or {}).get("evidence_inventory"),
            recruiter_review=recruiter_review,
            threshold=quality_threshold,
        )
        cycles.append(
            {
                "pass": pass_idx + 1,
                "grammar_passed": grammar.get("passed"),
                "style_passed": style.get("passed"),
                "ai_passed": ai.get("passed"),
                "ats_passed": ats.get("passed"),
                "facts_unchanged": fact_cmp.get("unchanged", True),
                "quality_score": quality_report.get("overall_score"),
            }
        )
        needs_repair = (
            not grammar.get("passed")
            or not style.get("passed")
            or not ai.get("passed")
            or not ats.get("passed")
            or not quality_report.get("passed")
            or not recruiter_review.get("approved", True)
        )
        weak = _normalize_sections(
            list(recruiter_review.get("sections_to_regenerate") or [])
            + list(quality_report.get("weak_sections") or [])
        )
        if not needs_repair or not weak or not allow_llm or not is_ai_available():
            break
        if not fact_cmp.get("unchanged", True):
            working = baseline
            break
        repair_passes += 1
        feedback = _compose_writer_feedback(
            review=recruiter_review,
            grammar=grammar,
            style=style,
            ai=ai,
            quality=quality_report,
        )
        # Targeted section repair — does NOT start a new primary agent call
        repaired = write_human_resume(
            validated_resume=working,
            strategy=strategy,
            knowledge_base=knowledge_base,
            output_language=output_language,
            review_feedback=feedback,
            sections=weak,
            use_cache=False,
            allow_llm=True,
        )
        if repaired.get("facts_unchanged", True) and repaired.get("tailored_resume"):
            working = _sync(repaired["tailored_resume"])
            recruiter_review = review_resume(
                resume=working,
                output_language=output_language,
                use_cache=False,
                allow_llm=False,
            )
        else:
            break

    fact_cmp = compare_facts(baseline, working)
    if not fact_cmp.get("unchanged", True):
        working = baseline

    # Final structured validation before handoff to Agent 4 / formatting
    final_report = validate_structured_resume(
        working,
        source_facts=source_facts or baseline,
        enforce_fullness=True,
        require_summary=True,
        jd_text=jd_blob,
    )
    if not final_report.passed:
        logger.warning(
            "Agent 3 final structured validation failed (%s) — repairing",
            final_report.error_codes(),
        )
        working = repair_structured_resume(
            working, source_facts=source_facts or baseline
        )
        # Preserve polished summary when repair cleared it incorrectly
        if not str(working.get("professional_summary") or working.get("summary") or "").strip():
            working["professional_summary"] = str(
                baseline.get("professional_summary") or baseline.get("summary") or ""
            )
            working["summary"] = working["professional_summary"]
        final_report = validate_structured_resume(
            working,
            source_facts=source_facts or baseline,
            enforce_fullness=True,
            require_summary=False,
            jd_text=jd_blob,
        )
    structured_validation = final_report.to_dict()
    working = stamp_ids_on_resume(working, source_facts=source_facts or baseline)

    from intelligent_tailoring.requirement_coverage import preserve_contact

    working = preserve_contact(
        working,
        source_contact=(source_facts or {}).get("contact")
        if isinstance((source_facts or {}).get("contact"), dict)
        else baseline.get("contact") if isinstance(baseline.get("contact"), dict) else {},
        resume_facts=source_facts or baseline,
    )

    return {
        "tailored_resume": working,
        "recruiter_review": recruiter_review,
        "validation_warnings": validation_warnings,
        "rejected_claims": rejected,
        "mode": mode,
        "facts_unchanged": True,
        "review_cycles": len(cycles),
        "repair_passes": repair_passes,
        "quality_score": quality_report,
        "cycles": cycles,
        "passed": bool(quality_report.get("passed", True)) and final_report.passed,
        "export_ready": bool(quality_report.get("passed", True)),
        "primary_llm_calls": primary_calls,
        "quality_gate_failures": list(quality_report.get("failures") or [])
        + ([] if final_report.passed else final_report.error_codes()),
        "merged_agent": "human_writing_credibility",
        "structured_validation": structured_validation,
    }
