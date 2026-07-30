"""Stage 7 — Content triage (LLM): Preserve/Rewrite/Reorder/Expand/Condense/Remove."""

from __future__ import annotations

import json
from typing import Any

from intelligent_tailoring.llm_utils import call_stage_json
from intelligent_tailoring.prompts.stage_prompts import (
    CONTENT_TRIAGE_SYSTEM,
    build_content_triage_user_prompt,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION, SchemaValidationError, TRIAGE_ACTIONS
from intelligent_tailoring.stages.resume_extraction import resume_facts_for_prompt

VALID_ACTIONS = frozenset(TRIAGE_ACTIONS)


def _validate(data: dict[str, Any]) -> None:
    if "triage" not in data or not isinstance(data["triage"], list):
        raise SchemaValidationError("triage must be a list")


def run_content_triage(
    *,
    resume_facts: dict[str, Any],
    ranked_requirements: list[dict[str, Any]],
    language: str = "en",
    use_cache: bool = True,
) -> dict[str, Any]:
    if resume_facts.get("sparse"):
        return {"triage": [], "section_order": [], "sparse": True}

    raw = call_stage_json(
        system_prompt=CONTENT_TRIAGE_SYSTEM,
        user_prompt=build_content_triage_user_prompt(
            resume_facts=resume_facts_for_prompt(resume_facts),
            ranked_requirements_json=json.dumps(
                ranked_requirements, ensure_ascii=False, indent=2
            ),
            language=language,
        ),
        validate=_validate,
        use_cache=use_cache,
        cache_namespace=f"{PIPELINE_VERSION}_triage",
        cache_payload=(
            f"{language}|{resume_facts_for_prompt(resume_facts)[:2500]}|"
            f"{json.dumps(ranked_requirements)[:1500]}"
        ),
    )
    triage = []
    for item in raw.get("triage") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "Preserve").strip()
        if action not in VALID_ACTIONS:
            action = "Preserve"
        triage.append(
            {
                "element_type": str(item.get("element_type") or "other"),
                "original_text": str(item.get("original_text") or ""),
                "action": action,
                "reason": str(item.get("reason") or ""),
                "related_job_requirement": str(
                    item.get("related_job_requirement") or ""
                ),
            }
        )
    section_order = [
        str(s)
        for s in (raw.get("section_order") or [])
        if str(s).strip()
    ]
    return {
        "triage": triage,
        "section_order": section_order,
        "sparse": False,
        "_from_cache": bool(raw.get("_from_cache")),
    }
