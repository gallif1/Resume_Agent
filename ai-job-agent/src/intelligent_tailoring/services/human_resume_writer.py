"""HumanResumeWriterService — elite resume writing polish (facts immutable)."""

from __future__ import annotations

import json
import logging
from typing import Any

from ai_client import is_ai_available
from intelligent_tailoring.llm_utils import call_stage_json
from intelligent_tailoring.prompts.human_writer_prompts import (
    HUMAN_RESUME_WRITER_SYSTEM,
    build_human_writer_user_prompt,
    sanitize_strategy_for_writer,
)
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    SchemaValidationError,
    validate_tailored_resume,
)
from intelligent_tailoring.writing.deterministic_polish import polish_resume_deterministic
from intelligent_tailoring.writing.fact_lock import enforce_fact_lock

logger = logging.getLogger("intelligent_tailoring.human_writer")


def _kb_facts_for_prompt(knowledge_base: Any) -> str:
    """Compact fact list for the writer — never include raw JD."""
    if knowledge_base is None:
        return "[]"
    facts: list[dict[str, Any]] = []
    raw_facts = getattr(knowledge_base, "facts", None)
    if raw_facts is None and isinstance(knowledge_base, dict):
        raw_facts = knowledge_base.get("facts") or []
    for fact in list(raw_facts or [])[:80]:
        if hasattr(fact, "to_dict"):
            d = fact.to_dict()
        elif isinstance(fact, dict):
            d = fact
        else:
            continue
        facts.append(
            {
                "type": d.get("fact_type") or d.get("type") or "",
                "value": d.get("value") or d.get("fact_value") or "",
                "source": (d.get("original_text") or d.get("source_text") or "")[:180],
                "org": d.get("organization") or "",
                "role": d.get("role") or "",
            }
        )
    return json.dumps(facts, ensure_ascii=False, indent=2)[:12000]


def _merge_partial_sections(
    baseline: dict[str, Any],
    polished: dict[str, Any],
    sections: list[str] | None,
) -> dict[str, Any]:
    """When only some sections were requested, keep others from baseline."""
    if not sections:
        return polished
    out = dict(baseline)
    wanted = {s.lower() for s in sections}
    if "summary" in wanted or "professional_summary" in wanted:
        summary = str(
            polished.get("professional_summary") or polished.get("summary") or ""
        )
        out["professional_summary"] = summary
        out["summary"] = summary
    if "skills" in wanted and polished.get("skills") is not None:
        out["skills"] = list(polished.get("skills") or [])
    for key in ("experience", "projects", "education", "certifications"):
        if key in wanted and polished.get(key) is not None:
            out[key] = polished.get(key)
    if "professional_title" in wanted and polished.get("professional_title"):
        out["professional_title"] = polished["professional_title"]
    return out


def _validate_writer_payload(data: dict[str, Any]) -> None:
    if "tailored_resume" not in data:
        raise SchemaValidationError("missing tailored_resume")
    validate_tailored_resume(data["tailored_resume"])


def _sync_summary_fields(resume: dict[str, Any]) -> dict[str, Any]:
    out = dict(resume)
    summary = str(out.get("professional_summary") or out.get("summary") or "")
    out["professional_summary"] = summary
    out["summary"] = summary
    return out


class HumanResumeWriterService:
    """Transform a validated resume into polished human-quality writing."""

    def write(
        self,
        *,
        validated_resume: dict[str, Any],
        strategy: dict[str, Any] | None = None,
        knowledge_base: Any = None,
        output_language: str = "en",
        review_feedback: dict[str, Any] | None = None,
        sections: list[str] | None = None,
        use_cache: bool = True,
        allow_llm: bool = True,
    ) -> dict[str, Any]:
        return write_human_resume(
            validated_resume=validated_resume,
            strategy=strategy,
            knowledge_base=knowledge_base,
            output_language=output_language,
            review_feedback=review_feedback,
            sections=sections,
            use_cache=use_cache,
            allow_llm=allow_llm,
        )


def write_human_resume(
    *,
    validated_resume: dict[str, Any],
    strategy: dict[str, Any] | None = None,
    knowledge_base: Any = None,
    output_language: str = "en",
    review_feedback: dict[str, Any] | None = None,
    sections: list[str] | None = None,
    use_cache: bool = True,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """Polish writing only. Returns resume + audit metadata."""
    baseline = _sync_summary_fields(dict(validated_resume or {}))
    # Always start from deterministic polish (safe, no inventions)
    deterministic = polish_resume_deterministic(baseline)
    locked = enforce_fact_lock(baseline, deterministic)
    working = _sync_summary_fields(locked["resume"])
    mode = "deterministic"
    writing_notes: list[str] = ["deterministic_polish"]
    llm_error: str | None = None

    if allow_llm and is_ai_available():
        try:
            safe_strategy = sanitize_strategy_for_writer(strategy or {})
            raw = call_stage_json(
                system_prompt=HUMAN_RESUME_WRITER_SYSTEM,
                user_prompt=build_human_writer_user_prompt(
                    validated_resume_json=json.dumps(
                        working, ensure_ascii=False, indent=2
                    ),
                    strategy_json=json.dumps(safe_strategy, ensure_ascii=False, indent=2)[
                        :8000
                    ],
                    knowledge_facts_json=_kb_facts_for_prompt(knowledge_base),
                    output_language=output_language or "en",
                    review_feedback_json=(
                        json.dumps(review_feedback, ensure_ascii=False, indent=2)[:4000]
                        if review_feedback
                        else None
                    ),
                    sections=sections,
                ),
                validate=_validate_writer_payload,
                use_cache=use_cache and not review_feedback,
                cache_namespace=f"{PIPELINE_VERSION}_human_writer",
                cache_payload=(
                    f"{output_language}|{json.dumps(working, sort_keys=True)[:2500]}|"
                    f"{','.join(sections or [])}|{(review_feedback or {}).get('summary_feedback', '')}"
                ),
                temperature=0.35,
            )
            polished = validate_tailored_resume(raw["tailored_resume"]).to_dict()
            polished = _merge_partial_sections(working, polished, sections)
            polished = _sync_summary_fields(polished)
            fact_result = enforce_fact_lock(baseline, polished)
            if fact_result["passed"] and not fact_result["reverted"]:
                working = _sync_summary_fields(fact_result["resume"])
                mode = "llm"
                writing_notes = [
                    str(n) for n in (raw.get("writing_notes") or []) if str(n).strip()
                ] or ["llm_human_writer"]
            else:
                # Fact drift — keep deterministic polish
                logger.warning(
                    "human_writer fact_lock rejected LLM polish: %s",
                    fact_result.get("violations")[:8],
                )
                writing_notes.append("llm_rejected_fact_lock")
                llm_error = "fact_lock:" + ",".join(
                    (fact_result.get("violations") or [])[:6]
                )
        except (SchemaValidationError, Exception) as exc:  # noqa: BLE001
            logger.warning("human_writer LLM polish failed: %s", exc)
            llm_error = str(exc)[:240]
            writing_notes.append("llm_failed_fallback_deterministic")

    # Final fact lock against original validated resume
    final_lock = enforce_fact_lock(baseline, working)
    final_resume = _sync_summary_fields(final_lock["resume"])

    return {
        "tailored_resume": final_resume,
        "mode": mode,
        "writing_notes": writing_notes,
        "sections_rewritten": list(sections or ["summary", "experience", "projects"]),
        "fact_lock": {
            "passed": final_lock["passed"],
            "reverted": final_lock["reverted"],
            "violations": final_lock.get("violations") or [],
        },
        "llm_error": llm_error,
    }
