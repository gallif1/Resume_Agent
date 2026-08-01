"""Stage 9 — Claim validation (LLM assist + deterministic enforcement)."""

from __future__ import annotations

import json
from typing import Any

from intelligent_tailoring.claim_validator import validate_claims
from intelligent_tailoring.llm_utils import call_stage_json
from intelligent_tailoring.prompts.stage_prompts import (
    CLAIM_VALIDATION_LLM_SYSTEM,
    build_claim_validation_user_prompt,
)
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    InferredCompetency,
    SchemaValidationError,
    TailoredResume,
)


def _validate_llm(data: dict[str, Any]) -> None:
    if "validation_warnings" not in data or not isinstance(
        data["validation_warnings"], list
    ):
        raise SchemaValidationError("validation_warnings must be a list")


def run_claim_validation(
    *,
    original_resume_text: str,
    tailored_resume: dict[str, Any] | TailoredResume,
    evidence_map: list[dict[str, Any]],
    change_log: list[dict[str, Any]],
    inferred: list[InferredCompetency],
    job_requirements: dict[str, Any] | None = None,
    use_cache: bool = True,
    run_llm_assist: bool = True,
    rejected_registry: Any | None = None,
) -> dict[str, Any]:
    """Always enforce deterministically; optionally gather extra LLM warnings first."""
    llm_warnings: list[dict[str, Any]] = []
    resume_dict = (
        tailored_resume.to_dict()
        if isinstance(tailored_resume, TailoredResume)
        else tailored_resume
    )
    # Strip previously rejected claims before re-validating
    if rejected_registry is not None and hasattr(rejected_registry, "scrub_resume"):
        resume_dict = rejected_registry.scrub_resume(resume_dict)

    if run_llm_assist:
        try:
            raw = call_stage_json(
                system_prompt=CLAIM_VALIDATION_LLM_SYSTEM,
                user_prompt=build_claim_validation_user_prompt(
                    original_resume=original_resume_text,
                    tailored_resume_json=json.dumps(
                        resume_dict, ensure_ascii=False, indent=2
                    ),
                    evidence_map_json=json.dumps(
                        evidence_map, ensure_ascii=False, indent=2
                    ),
                ),
                validate=_validate_llm,
                use_cache=use_cache,
                cache_namespace=f"{PIPELINE_VERSION}_claim_llm",
                cache_payload=(
                    f"{original_resume_text[:2000]}|"
                    f"{json.dumps(resume_dict)[:2000]}"
                ),
            )
            for item in raw.get("validation_warnings") or []:
                if isinstance(item, dict) and item.get("statement"):
                    llm_warnings.append(item)
        except SchemaValidationError:
            llm_warnings = []

    # Deterministic enforcement — the safety net that must always run.
    result = validate_claims(
        original_resume_text=original_resume_text,
        tailored_resume=resume_dict,
        evidence_map=evidence_map,
        change_log=change_log,
        inferred_competencies=inferred,
        job_requirements=job_requirements,
        rejected_registry=rejected_registry,
    )

    # Merge LLM warnings that aren't already present
    existing = {w.statement for w in result.warnings}
    for item in llm_warnings:
        stmt = str(item.get("statement") or "").strip()
        if stmt and stmt not in existing:
            # Re-check deterministically; only keep if truly unsupported
            from intelligent_tailoring.claim_validator import (
                statement_supported_by_evidence,
            )

            ok, _ = statement_supported_by_evidence(
                stmt,
                source_text=original_resume_text,
                evidence_map=evidence_map,
                strongly_inferred=result.inferred_competencies,
            )
            if not ok:
                from intelligent_tailoring.schemas import ValidationWarning

                result.warnings.append(
                    ValidationWarning(
                        statement=stmt,
                        reason=str(item.get("reason") or "LLM flagged unsupported claim"),
                        inference_category=str(
                            item.get("inference_category") or "Unsupported"
                        ),
                    )
                )
                # Also strip from cleaned resume if still present
                _strip_statement_from_resume(result.cleaned_resume, stmt)
                result.rejected_statements.append(stmt)
                if rejected_registry is not None:
                    rejected_registry.add(
                        stmt,
                        reason=str(item.get("reason") or "llm_flagged"),
                        source_agent="claim_validation",
                    )

    if rejected_registry is not None:
        rejected_registry.extend(
            result.rejected_statements,
            reason="claim_validation",
            source_agent="claim_validation",
        )

    payload = result.to_dict()
    if rejected_registry is not None:
        payload["rejected_claims_registry"] = rejected_registry.to_dict()
    return payload


def _strip_statement_from_resume(resume: TailoredResume, statement: str) -> None:
    stmt = statement.strip()
    if not stmt:
        return
    if resume.professional_summary.strip() == stmt:
        resume.professional_summary = ""
    resume.skills = [s for s in resume.skills if s.strip() != stmt]
    for entry in resume.experience:
        entry["bullets"] = [
            b for b in (entry.get("bullets") or []) if str(b).strip() != stmt
        ]
    for entry in resume.projects:
        entry["bullets"] = [
            b for b in (entry.get("bullets") or []) if str(b).strip() != stmt
        ]
        if str(entry.get("description") or "").strip() == stmt:
            entry["description"] = ""
