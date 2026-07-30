"""Stage 8 — Resume generation (LLM) producing tailored_resume + change_log."""

from __future__ import annotations

import json
from typing import Any

from intelligent_tailoring.llm_utils import call_stage_json
from intelligent_tailoring.prompts.stage_prompts import (
    RESUME_GENERATION_SYSTEM,
    build_resume_generation_user_prompt,
)
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    InferredCompetency,
    SchemaValidationError,
    validate_change_log_item,
    validate_tailored_resume,
)
from intelligent_tailoring.stages.resume_extraction import resume_facts_for_prompt


def _validate(data: dict[str, Any]) -> None:
    if "tailored_resume" not in data:
        raise SchemaValidationError("missing tailored_resume")
    validate_tailored_resume(data["tailored_resume"])
    if "change_log" not in data or not isinstance(data["change_log"], list):
        raise SchemaValidationError("change_log must be a list")
    for i, item in enumerate(data["change_log"]):
        validate_change_log_item(item, index=i)


def generate_tailored_resume(
    *,
    resume_facts: dict[str, Any],
    ranked_requirements: list[dict[str, Any]],
    inferred: list[InferredCompetency],
    triage: dict[str, Any],
    evidence_map: list[dict[str, Any]],
    language: str = "en",
    use_cache: bool = True,
) -> dict[str, Any]:
    raw = call_stage_json(
        system_prompt=RESUME_GENERATION_SYSTEM,
        user_prompt=build_resume_generation_user_prompt(
            resume_facts=resume_facts_for_prompt(resume_facts),
            ranked_requirements_json=json.dumps(
                ranked_requirements, ensure_ascii=False, indent=2
            ),
            inferred_json=json.dumps(
                [i.to_dict() for i in inferred], ensure_ascii=False, indent=2
            ),
            triage_json=json.dumps(triage, ensure_ascii=False, indent=2),
            evidence_map_json=json.dumps(evidence_map, ensure_ascii=False, indent=2),
            language=language,
        ),
        validate=_validate,
        use_cache=use_cache,
        cache_namespace=f"{PIPELINE_VERSION}_generate",
        cache_payload=(
            f"{language}|{resume_facts_for_prompt(resume_facts)[:3000]}|"
            f"{json.dumps(ranked_requirements)[:1500]}"
        ),
    )
    resume = validate_tailored_resume(raw["tailored_resume"])
    change_log = [
        validate_change_log_item(item, index=i).to_dict()
        for i, item in enumerate(raw.get("change_log") or [])
    ]
    return {
        "tailored_resume": resume.to_dict(),
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
    }
