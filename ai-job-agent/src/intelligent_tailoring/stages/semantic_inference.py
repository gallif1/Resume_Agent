"""Stage 4 — Semantic inference via ontology + LLM (Strongly Inferred only)."""

from __future__ import annotations

import json
from typing import Any

from intelligent_tailoring.llm_utils import call_stage_json
from intelligent_tailoring.ontology import SkillOntology, get_ontology
from intelligent_tailoring.prompts.stage_prompts import (
    SEMANTIC_INFERENCE_SYSTEM,
    build_semantic_inference_user_prompt,
)
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    InferredCompetency,
    SchemaValidationError,
    normalize_inference_category,
)
from intelligent_tailoring.stages.job_requirement_extraction import requirements_json
from intelligent_tailoring.stages.resume_extraction import resume_facts_for_prompt

MIN_CONFIDENCE = 0.8


def _validate_inference_payload(data: dict[str, Any]) -> None:
    if "inferred_competencies" not in data:
        raise SchemaValidationError("missing inferred_competencies")
    if not isinstance(data["inferred_competencies"], list):
        raise SchemaValidationError("inferred_competencies must be a list")


def _from_ontology_hits(
    resume_text: str,
    requirements: dict[str, Any],
    ontology: SkillOntology,
    *,
    language: str,
) -> list[InferredCompetency]:
    req_terms = []
    for key in (
        "hard_requirements",
        "required_skills",
        "preferred_skills",
        "tools_technologies",
        "soft_requirements",
        "responsibilities",
        "ats_keywords",
    ):
        req_terms.extend(str(x) for x in (requirements.get(key) or []))

    hits = ontology.infer_from_resume_text(
        resume_text, min_confidence=MIN_CONFIDENCE, language=language
    )
    results: list[InferredCompetency] = []
    req_blob = " ".join(req_terms).lower()
    for hit in hits:
        # Prefer competencies relevant to the JD when possible; still keep high-confidence
        # transferable skills that the ontology surfaced from the resume.
        related = ""
        target_l = hit.relation.target.lower()
        for term in req_terms:
            if term.lower() in target_l or target_l in term.lower() or term.lower() in (
                hit.inferred_competency.lower()
            ):
                related = term
                break
        if not related:
            for extra in hit.relation.also_implies:
                if extra.lower() in req_blob:
                    related = extra
                    break
        results.append(
            InferredCompetency(
                statement=hit.inferred_competency,
                supporting_evidence=hit.resume_evidence,
                reasoning=(
                    f"Ontology rule {hit.relation.id} ({hit.relation.relation}): "
                    f"'{hit.matched_source}' → '{hit.relation.target}'"
                ),
                confidence_score=hit.confidence,
                related_requirement=related or hit.relation.target,
                ontology_rule_id=hit.relation.id,
                inference_category="Strongly Inferred",
            )
        )
    return results


def run_semantic_inference(
    *,
    resume_facts: dict[str, Any],
    requirements: dict[str, Any],
    language: str = "en",
    use_cache: bool = True,
    ontology: SkillOntology | None = None,
) -> list[InferredCompetency]:
    ontology = ontology or get_ontology()
    resume_text = str(resume_facts.get("raw_text") or "")
    deterministic = _from_ontology_hits(
        resume_text, requirements, ontology, language=language
    )

    # LLM pass to catch Strongly Inferred items not in the static ontology.
    try:
        raw = call_stage_json(
            system_prompt=SEMANTIC_INFERENCE_SYSTEM,
            user_prompt=build_semantic_inference_user_prompt(
                resume_facts=resume_facts_for_prompt(resume_facts),
                job_requirements_json=requirements_json(requirements),
                ontology_summary=ontology.to_prompt_summary(),
            ),
            validate=_validate_inference_payload,
            use_cache=use_cache,
            cache_namespace=f"{PIPELINE_VERSION}_inference",
            cache_payload=(
                f"{language}|{resume_text[:2500]}|{requirements_json(requirements)[:2000]}"
            ),
        )
    except SchemaValidationError:
        return _dedupe_competencies(deterministic)

    merged = list(deterministic)
    for item in raw.get("inferred_competencies") or []:
        if not isinstance(item, dict):
            continue
        cat = normalize_inference_category(
            item.get("inference_category") or "Strongly Inferred"
        )
        if cat != "Strongly Inferred":
            continue
        conf = float(item.get("confidence_score") or 0)
        if conf < MIN_CONFIDENCE:
            continue
        statement = str(item.get("statement") or "").strip()
        evidence = str(item.get("supporting_evidence") or "").strip()
        reasoning = str(item.get("reasoning") or item.get("reason") or "").strip()
        if not statement or not evidence or not reasoning:
            continue
        # Evidence must appear in the resume (deterministic gate before acceptance).
        if evidence.lower() not in resume_text.lower() and not any(
            tok.lower() in resume_text.lower()
            for tok in evidence.split()
            if len(tok) > 3
        ):
            continue
        merged.append(
            InferredCompetency(
                statement=statement,
                supporting_evidence=evidence,
                reasoning=reasoning,
                confidence_score=conf,
                related_requirement=str(
                    item.get("related_requirement")
                    or item.get("related_job_requirement")
                    or ""
                ),
                ontology_rule_id=str(item.get("ontology_rule_id") or ""),
                inference_category="Strongly Inferred",
            )
        )
    return _dedupe_competencies(merged)


def _dedupe_competencies(items: list[InferredCompetency]) -> list[InferredCompetency]:
    seen: set[str] = set()
    result: list[InferredCompetency] = []
    for item in items:
        key = item.statement.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def inferred_to_json(items: list[InferredCompetency]) -> str:
    return json.dumps([i.to_dict() for i in items], ensure_ascii=False, indent=2)
