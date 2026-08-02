"""Single Resume Generation Agent — the only primary LLM call.

Receives pre-parsed resume facts, normalized requirements, strategy, and
evidence (all prepared in code) and returns the final structured resume JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from intelligent_tailoring.llm_utils import call_stage_json, record_primary_llm_call
from intelligent_tailoring.prompts.resume_generation_agent_prompts import (
    RESUME_GENERATION_AGENT_PROMPT_VERSION,
    RESUME_GENERATION_AGENT_SYSTEM,
    build_resume_generation_agent_user_prompt,
)
from intelligent_tailoring.prompts.stage_prompts import DEEP_TAILOR_REWRITE_SYSTEM
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    InferredCompetency,
    SchemaValidationError,
    sanitize_change_log_raw,
    validate_change_log_item,
    validate_tailored_resume,
)
from intelligent_tailoring.services.resume_rewriter import (
    _fallback_from_rebuilt,
    _merge_experience_order,
    _merge_project_order,
)
from intelligent_tailoring.stages.resume_extraction import resume_facts_for_prompt

logger = logging.getLogger("intelligent_tailoring.single_resume_generation")


def _validate(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise SchemaValidationError("generation response must be an object")
    if "tailored_resume" not in data:
        for alt in ("resume", "tailored_cv", "cv"):
            if isinstance(data.get(alt), dict):
                data["tailored_resume"] = data[alt]
                break
    if "tailored_resume" not in data:
        raise SchemaValidationError("missing tailored_resume")
    validate_tailored_resume(data["tailored_resume"])
    data["change_log"] = sanitize_change_log_raw(data.get("change_log"))
    for i, item in enumerate(data["change_log"]):
        validate_change_log_item(item, index=i)


def _compact_evidence(evidence_map: list[dict[str, Any]]) -> str:
    rows = []
    for e in (evidence_map or [])[:40]:
        rows.append(
            {
                "requirement": e.get("requirement"),
                "status": e.get("candidate_status"),
                "strength": e.get("evidence_strength"),
                "evidence": str(e.get("supporting_evidence") or "")[:140],
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def generate_resume_single_agent(
    *,
    resume_facts: dict[str, Any],
    rebuilt_resume: dict[str, Any],
    strategy: dict[str, Any],
    scores: dict[str, Any],
    ranked_requirements: list[dict[str, Any]],
    inferred: list[InferredCompetency],
    evidence_map: list[dict[str, Any]],
    language: str = "en",
    use_cache: bool = True,
    regeneration_attempt: int = 0,
    knowledge_base_summary: str = "",
) -> dict[str, Any]:
    """One primary LLM call that selects content AND writes final prose."""
    cache_suffix = f"|regen{regeneration_attempt}" if regeneration_attempt else ""
    user_prompt = build_resume_generation_agent_user_prompt(
        language=language,
        strategy_json=json.dumps(strategy, ensure_ascii=False, indent=2),
        rebuilt_resume_json=json.dumps(rebuilt_resume, ensure_ascii=False, indent=2),
        ranked_requirements_json=json.dumps(
            ranked_requirements, ensure_ascii=False, indent=2
        ),
        evidence_map_compact=_compact_evidence(evidence_map),
        resume_facts_compact=resume_facts_for_prompt(resume_facts)[:4500],
        inferred_json=json.dumps(
            [i.to_dict() if hasattr(i, "to_dict") else i for i in (inferred or [])],
            ensure_ascii=False,
            indent=2,
        ),
        scores_json=json.dumps(scores or {}, ensure_ascii=False, indent=2),
        knowledge_base_summary=(knowledge_base_summary or "")[:2000],
        genuine_gaps=", ".join(
            str(x) for x in (strategy.get("genuine_gaps") or [])[:20]
        ),
        forbidden_claims=", ".join(
            str(x) for x in (strategy.get("forbidden_claims") or [])[:20]
        ),
        regeneration_attempt=regeneration_attempt,
    )
    cache_payload = (
        f"{language}|{strategy.get('job_family')}|"
        f"{resume_facts_for_prompt(resume_facts)[:2500]}{cache_suffix}"
    )

    raw: dict[str, Any] | None = None
    # Count the composed generation as the sole primary LLM agent call even
    # when tests mock call_stage_json (mirrors prior intelligence-bundle pattern).
    record_primary_llm_call("resume_generation_agent")
    try:
        raw = call_stage_json(
            system_prompt=RESUME_GENERATION_AGENT_SYSTEM,
            user_prompt=user_prompt,
            validate=_validate,
            use_cache=use_cache and regeneration_attempt == 0,
            cache_namespace=f"{RESUME_GENERATION_AGENT_PROMPT_VERSION}_gen",
            cache_payload=cache_payload,
            temperature=0.25 if regeneration_attempt else 0.2,
            # Already recorded above — avoid double-count when call_stage_json
            # also supports count_as_primary.
        )
    except SchemaValidationError as composed_error:
        logger.warning(
            "single resume agent schema failed (%s) — falling back to deep-tailor prompt",
            composed_error,
        )
        try:
            raw = call_stage_json(
                system_prompt=DEEP_TAILOR_REWRITE_SYSTEM,
                user_prompt=user_prompt,
                validate=_validate,
                use_cache=False,
                cache_namespace=f"{PIPELINE_VERSION}_deep_rewrite_fallback",
                cache_payload=f"fallback|{cache_payload}",
                temperature=0.2,
                # Primary already counted for the composed call attempt
            )
        except SchemaValidationError as fallback_error:
            logger.warning(
                "deep-tailor fallback also failed (%s) — using rebuilt resume",
                fallback_error,
            )
            return _fallback_from_rebuilt(
                rebuilt_resume=rebuilt_resume,
                resume_facts=resume_facts,
                strategy=strategy,
            )

    assert raw is not None
    resume = validate_tailored_resume(raw["tailored_resume"])
    resume_dict = resume.to_dict()

    if not (resume_dict.get("skills") or []):
        if rebuilt_resume.get("skills"):
            resume_dict["skills"] = rebuilt_resume["skills"]
    if rebuilt_resume.get("experience"):
        _merge_experience_order(resume_dict, rebuilt_resume)
    if rebuilt_resume.get("projects"):
        _merge_project_order(resume_dict, rebuilt_resume)

    raw["change_log"] = sanitize_change_log_raw(raw.get("change_log"))
    change_log = [
        validate_change_log_item(item, index=i).to_dict()
        for i, item in enumerate(raw.get("change_log") or [])
    ]
    return {
        "tailored_resume": resume_dict,
        "change_log": change_log,
        "matched_requirements": [
            str(x).strip()
            for x in (raw.get("matched_requirements") or [])
            if str(x).strip()
        ],
        "missing_requirements": [
            str(x).strip()
            for x in (raw.get("missing_requirements") or [])
            if str(x).strip()
        ],
        "removed_or_deprioritized_content": [
            str(x).strip()
            for x in (raw.get("removed_or_deprioritized_content") or [])
            if str(x).strip()
        ],
        "ats_keywords_added": [
            str(x).strip()
            for x in (raw.get("ats_keywords_added") or [])
            if str(x).strip()
        ],
        "_from_cache": bool(raw.get("_from_cache")),
        "primary_llm_calls": 1,
        "agent_id": "resume_generation_agent",
    }
