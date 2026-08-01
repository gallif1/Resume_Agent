"""Merged Agent 1 LLM stage — job analysis + semantic inference in one call.

Resume knowledge and company intelligence remain deterministic modules.
When a JobProfile is already cached, this stage is skipped for the job half.
"""

from __future__ import annotations

import json
from typing import Any

from ai_client import truncate_text
from config import OPENAI_JOB_MAX_CHARS
from intelligent_tailoring.llm_utils import call_stage_json, record_primary_llm_call
from intelligent_tailoring.ontology import SkillOntology, get_ontology
from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_1_SYSTEM,
    MERGED_AGENT_1_PROMPT_VERSION,
    build_agent_1_user_prompt,
)
from intelligent_tailoring.schemas import (
    InferredCompetency,
    SchemaValidationError,
    normalize_inference_category,
)
from intelligent_tailoring.stages.job_requirement_extraction import (
    validate_requirements,
)
from intelligent_tailoring.stages.resume_extraction import resume_facts_for_prompt
from intelligent_tailoring.stages.semantic_inference import (
    MIN_CONFIDENCE,
    _dedupe_competencies,
    _from_ontology_hits,
)
from match_tailor_service import build_job_payload
from job_analyzer import parse_stored_job_profile


def _validate_bundle(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise SchemaValidationError("intelligence bundle must be an object")
    if "job_requirements" not in data and "inferred_competencies" not in data:
        raise SchemaValidationError(
            "intelligence bundle missing job_requirements and inferred_competencies"
        )


def run_intelligence_bundle_llm(
    *,
    job: dict[str, Any],
    resume_facts: dict[str, Any],
    knowledge_base_summary: str = "",
    verified_company_metadata: str = "",
    language: str = "en",
    use_cache: bool = True,
    ontology: SkillOntology | None = None,
    jd_snapshot: str | None = None,
) -> dict[str, Any]:
    """One primary LLM call returning job requirements + inferred competencies."""
    ontology = ontology or get_ontology()
    job_profile = parse_stored_job_profile(job.get("job_profile"))
    jd_text = jd_snapshot or build_job_payload(job, job_profile)
    jd_text = truncate_text(jd_text, OPENAI_JOB_MAX_CHARS)
    title = str(job.get("title") or "")
    company = str(job.get("company") or "")
    resume_text = str(resume_facts.get("raw_text") or "")

    if len(jd_text.strip()) < 40:
        return {
            "job_requirements": {
                "required_skills": [],
                "preferred_skills": [],
                "responsibilities": [],
                "tools_technologies": [],
                "industry_terminology": [],
                "seniority_level": "",
                "soft_skills": [],
                "education_certifications": [],
                "ats_keywords": [],
                "hard_requirements": [],
                "soft_requirements": [],
                "language": language or "en",
                "sparse": True,
                "jd_text": jd_text,
            },
            "inferred_competencies": [],
            "company_cues": {"verified_facts_only": True},
            "genuine_gaps": [],
            "safe_inferences": [],
            "forbidden_claims": [],
            "requirement_priorities": [],
            "primary_llm_calls": 0,
            "skipped_sparse_jd": True,
        }

    record_primary_llm_call("candidate_opportunity_intelligence")
    raw = call_stage_json(
        system_prompt=AGENT_1_SYSTEM,
        user_prompt=build_agent_1_user_prompt(
            job_title=title,
            company=company,
            jd_text=jd_text,
            resume_facts_compact=resume_facts_for_prompt(resume_facts)[:3500],
            knowledge_base_summary=(knowledge_base_summary or "")[:2000],
            ontology_summary=ontology.to_prompt_summary(),
            verified_company_metadata=verified_company_metadata,
        ),
        validate=_validate_bundle,
        use_cache=use_cache,
        cache_namespace=f"{MERGED_AGENT_1_PROMPT_VERSION}_intel_bundle",
        cache_payload=(
            f"{language}|{title}|{jd_text[:2500]}|"
            f"{resume_text[:2000]}|{knowledge_base_summary[:500]}"
        ),
    )

    req_raw = raw.get("job_requirements") or raw
    # If model returned flat requirements at top level, accept that too
    try:
        requirements = validate_requirements(
            req_raw if isinstance(req_raw, dict) else {}
        )
    except SchemaValidationError:
        requirements = validate_requirements(
            {k: raw.get(k) for k in (
                "required_skills", "preferred_skills", "responsibilities",
                "tools_technologies", "industry_terminology", "seniority_level",
                "soft_skills", "education_certifications", "ats_keywords",
                "hard_requirements", "soft_requirements", "language",
            )}
        )
    requirements["sparse"] = False
    requirements["jd_text"] = jd_text

    deterministic = _from_ontology_hits(
        resume_text, requirements, ontology, language=language
    )
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
                related_requirement=str(item.get("related_requirement") or ""),
                ontology_rule_id=str(item.get("ontology_rule_id") or ""),
                inference_category="Strongly Inferred",
            )
        )

    return {
        "job_requirements": requirements,
        "inferred_competencies": _dedupe_competencies(merged),
        "company_cues": raw.get("company_cues")
        if isinstance(raw.get("company_cues"), dict)
        else {"verified_facts_only": True},
        "genuine_gaps": [
            str(x) for x in (raw.get("genuine_gaps") or []) if str(x).strip()
        ],
        "safe_inferences": [
            str(x) for x in (raw.get("safe_inferences") or []) if str(x).strip()
        ],
        "forbidden_claims": [
            str(x) for x in (raw.get("forbidden_claims") or []) if str(x).strip()
        ],
        "requirement_priorities": [
            str(x) for x in (raw.get("requirement_priorities") or []) if str(x).strip()
        ],
        "primary_llm_calls": 1,
        "_from_cache": bool(raw.get("_from_cache")),
        "raw_bundle": {
            k: v
            for k, v in raw.items()
            if k
            in (
                "genuine_gaps",
                "safe_inferences",
                "forbidden_claims",
                "requirement_priorities",
                "company_cues",
            )
        },
    }


def knowledge_base_compact_summary(kb: Any) -> str:
    """Compact JSON for later agents — avoid resending full raw PDF text."""
    if kb is None:
        return ""
    if hasattr(kb, "to_dict"):
        data = kb.to_dict()
    elif isinstance(kb, dict):
        data = kb
    else:
        return ""
    facts = data.get("facts") or []
    compact_facts = []
    for f in facts[:80]:
        if isinstance(f, dict):
            compact_facts.append(
                {
                    "id": f.get("fact_id") or f.get("id"),
                    "type": f.get("fact_type") or f.get("type"),
                    "section": f.get("source_section"),
                    "entry": f.get("source_entry_id"),
                    "text": str(f.get("original_text") or f.get("normalized_value") or "")[
                        :180
                    ],
                    "skills": (f.get("explicit_skills") or [])[:8],
                }
            )
    payload = {
        "content_hash": data.get("content_hash"),
        "fact_count": len(facts),
        "facts": compact_facts,
        "coverage": data.get("extraction_coverage") or data.get("coverage") or {},
    }
    return json.dumps(payload, ensure_ascii=False)
