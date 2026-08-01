"""ResumeRewriter — strategy-driven LLM section rewriting."""

from __future__ import annotations

import json
import logging
from typing import Any

from intelligent_tailoring.llm_utils import call_stage_json
from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_2_SYSTEM,
    MERGED_AGENT_2_PROMPT_VERSION,
)
from intelligent_tailoring.prompts.stage_prompts import (
    DEEP_TAILOR_REWRITE_SYSTEM,
    build_deep_tailor_rewrite_user_prompt,
)
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    InferredCompetency,
    SchemaValidationError,
    sanitize_change_log_raw,
    validate_change_log_item,
    validate_tailored_resume,
)
from intelligent_tailoring.stages.resume_extraction import resume_facts_for_prompt

logger = logging.getLogger("intelligent_tailoring.resume_rewriter")


def _validate(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise SchemaValidationError("rewrite response must be an object")
    # Tolerate models that nest under alternate keys after composition
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


def _fallback_from_rebuilt(
    *,
    rebuilt_resume: dict[str, Any],
    resume_facts: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic structure when the LLM omits tailored_resume."""
    resume = dict(rebuilt_resume or {})
    if not resume.get("skills"):
        resume["skills"] = list(
            resume_facts.get("display_skills") or resume_facts.get("skills") or []
        )
    if not resume.get("experience"):
        resume["experience"] = list(resume_facts.get("experience_roles") or [])
    if not resume.get("projects"):
        resume["projects"] = list(resume_facts.get("projects") or [])
    if not resume.get("education"):
        resume["education"] = list(resume_facts.get("education") or [])
    summary = str(
        resume.get("professional_summary")
        or resume.get("summary")
        or (strategy.get("summary_focus") or [""])[0]
        or ""
    ).strip()
    resume["professional_summary"] = summary
    resume["summary"] = summary
    if not resume.get("professional_title"):
        resume["professional_title"] = str(
            strategy.get("target_title")
            or resume_facts.get("professional_title")
            or ""
        )
    validated = validate_tailored_resume(resume).to_dict()
    return {
        "tailored_resume": validated,
        "change_log": [],
        "matched_requirements": list(strategy.get("must_highlight_in_summary") or []),
        "missing_requirements": list(strategy.get("genuine_gaps") or []),
        "removed_or_deprioritized_content": list(
            strategy.get("facts_to_condense") or []
        ),
        "ats_keywords_added": [],
        "_from_cache": False,
        "_fallback": "rebuilt_resume",
    }


def rewrite_resume_with_strategy(
    *,
    resume_facts: dict[str, Any],
    rebuilt_resume: dict[str, Any],
    strategy: dict[str, Any],
    scores: dict[str, Any],
    ranked_requirements: list[dict[str, Any]],
    inferred: list[InferredCompetency],
    evidence_map: list[dict[str, Any]],
    triage: dict[str, Any],
    language: str = "en",
    use_cache: bool = True,
    regeneration_attempt: int = 0,
) -> dict[str, Any]:
    """Rewrite summary, bullets, and project descriptions per tailoring strategy.

    Uses the composed Agent 2 prompt (strategy + triage rules + deep tailor).
    Falls back to the legacy deep-tailor prompt, then to rebuilt structure,
    so preview generation cannot hard-fail on a missing tailored_resume key.
    """
    cache_suffix = f"|regen{regeneration_attempt}" if regeneration_attempt else ""
    user_prompt = build_deep_tailor_rewrite_user_prompt(
        resume_facts=resume_facts_for_prompt(resume_facts),
        rebuilt_resume_json=json.dumps(rebuilt_resume, ensure_ascii=False, indent=2),
        strategy_json=json.dumps(strategy, ensure_ascii=False, indent=2),
        scores_json=json.dumps(scores, ensure_ascii=False, indent=2),
        ranked_requirements_json=json.dumps(
            ranked_requirements, ensure_ascii=False, indent=2
        ),
        inferred_json=json.dumps(
            [i.to_dict() for i in inferred], ensure_ascii=False, indent=2
        ),
        triage_json=json.dumps(triage, ensure_ascii=False, indent=2),
        evidence_map_json=json.dumps(evidence_map, ensure_ascii=False, indent=2),
        language=language,
        regeneration_attempt=regeneration_attempt,
    )
    cache_payload = (
        f"{language}|{strategy.get('job_family')}|"
        f"{resume_facts_for_prompt(resume_facts)[:2500]}{cache_suffix}"
    )

    raw: dict[str, Any] | None = None
    try:
        raw = call_stage_json(
            system_prompt=AGENT_2_SYSTEM,
            user_prompt=user_prompt,
            validate=_validate,
            use_cache=use_cache and regeneration_attempt == 0,
            cache_namespace=f"{MERGED_AGENT_2_PROMPT_VERSION}_strategy_content",
            cache_payload=cache_payload,
            temperature=0.25 if regeneration_attempt else 0.2,
            count_as_primary="strategy_content_selection",
        )
    except SchemaValidationError as composed_error:
        logger.warning(
            "merged Agent 2 schema failed (%s) — falling back to deep-tailor prompt",
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
                # Already counted the composed primary call; do not double-count
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

    # Keep LLM skill selection when present — overwriting with the rebuilder
    # list erased job-specific emphasis (always the same source skill set).
    # Fall back to the rebuilt grouping only when the model omitted skills.
    if not (resume_dict.get("skills") or []):
        if rebuilt_resume.get("skills"):
            resume_dict["skills"] = rebuilt_resume["skills"]
    if rebuilt_resume.get("experience"):
        # Merge: keep LLM bullet text but enforce score-based order per role
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
    }


def _merge_experience_order(tailored: dict[str, Any], rebuilt: dict[str, Any]) -> None:
    rebuilt_exp = rebuilt.get("experience") or []
    tailored_exp = tailored.get("experience") or []
    if not rebuilt_exp:
        return
    # Reorder tailored bullets to match rebuilt order using text matching
    for rb, tb in zip(rebuilt_exp, tailored_exp):
        if not isinstance(rb, dict) or not isinstance(tb, dict):
            continue
        rb_bullets = [str(b) for b in (rb.get("bullets") or [])]
        tb_bullets = [str(b) for b in (tb.get("bullets") or [])]
        ordered: list[str] = []
        used: set[int] = set()
        for rb_text in rb_bullets:
            rb_low = rb_text.lower()
            for i, tb_text in enumerate(tb_bullets):
                if i in used:
                    continue
                if rb_low in tb_text.lower() or tb_text.lower() in rb_low:
                    ordered.append(tb_text)
                    used.add(i)
                    break
        for i, tb_text in enumerate(tb_bullets):
            if i not in used:
                ordered.append(tb_text)
        tb["bullets"] = ordered if ordered else tb_bullets


def _merge_project_order(tailored: dict[str, Any], rebuilt: dict[str, Any]) -> None:
    rebuilt_names = [
        str(p.get("name") or "").lower()
        for p in (rebuilt.get("projects") or [])
        if isinstance(p, dict)
    ]
    tailored_projects = tailored.get("projects") or []
    if not rebuilt_names or not tailored_projects:
        tailored["projects"] = rebuilt.get("projects") or tailored_projects
        return
    by_name = {
        str(p.get("name") or "").lower(): p
        for p in tailored_projects
        if isinstance(p, dict)
    }
    ordered = []
    for name in rebuilt_names:
        if name in by_name:
            ordered.append(by_name[name])
    for p in tailored_projects:
        if p not in ordered:
            ordered.append(p)
    tailored["projects"] = ordered
