"""Merged Agent 1 LLM stage — job analysis + semantic inference in one call.

Resume knowledge and company intelligence remain deterministic modules.
When a JobProfile is already cached, this stage is skipped for the job half.

Smaller models sometimes return flat requirement fields (or alternate wrapper
keys) instead of ``{job_requirements, inferred_competencies}``. We normalize
those shapes and fall back to a deterministic JD/ontology path so generation
never hard-fails on Agent 1 schema drift.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ai_client import truncate_text
from config import OPENAI_JOB_MAX_CHARS, OPENAI_MODEL
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

logger = logging.getLogger("intelligent_tailoring.intelligence_bundle")

_REQUIREMENT_SIGNAL_KEYS = frozenset(
    {
        "required_skills",
        "preferred_skills",
        "responsibilities",
        "tools_technologies",
        "industry_terminology",
        "soft_skills",
        "education_certifications",
        "ats_keywords",
        "hard_requirements",
        "soft_requirements",
        "seniority_level",
    }
)
_WRAPPER_KEYS = (
    "result",
    "data",
    "output",
    "intelligence",
    "bundle",
    "payload",
    "analysis",
)
_REQ_ALIAS_KEYS = (
    "job_requirements",
    "requirements",
    "job_requirement",
    "extracted_requirements",
    "jd_requirements",
)
_INFER_ALIAS_KEYS = (
    "inferred_competencies",
    "competencies",
    "inferences",
    "inferred",
    "semantic_inferences",
)


def _looks_like_requirements(data: dict[str, Any]) -> bool:
    return bool(_REQUIREMENT_SIGNAL_KEYS & set(data.keys()))


def _requirement_slice(data: dict[str, Any]) -> dict[str, Any]:
    return {k: data[k] for k in _REQUIREMENT_SIGNAL_KEYS if k in data}


def normalize_intelligence_raw(data: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce common LLM schema variants into the Agent 1 canonical shape."""
    if not isinstance(data, dict):
        return {}
    out = dict(data)

    for wrap in _WRAPPER_KEYS:
        inner = out.get(wrap)
        if not isinstance(inner, dict):
            continue
        if (
            any(k in inner for k in _REQ_ALIAS_KEYS)
            or any(k in inner for k in _INFER_ALIAS_KEYS)
            or _looks_like_requirements(inner)
        ):
            merged = {**out, **inner}
            merged.pop(wrap, None)
            out = merged
            break

    if "job_requirements" not in out or not isinstance(out.get("job_requirements"), dict):
        for alt in _REQ_ALIAS_KEYS:
            if alt == "job_requirements":
                continue
            candidate = out.get(alt)
            if isinstance(candidate, dict):
                out["job_requirements"] = candidate
                break
        if not isinstance(out.get("job_requirements"), dict) and _looks_like_requirements(
            out
        ):
            out["job_requirements"] = _requirement_slice(out)

    if "inferred_competencies" not in out:
        for alt in _INFER_ALIAS_KEYS:
            if alt == "inferred_competencies":
                continue
            candidate = out.get(alt)
            if isinstance(candidate, list):
                out["inferred_competencies"] = candidate
                break
    if not isinstance(out.get("inferred_competencies"), list):
        # Accept empty when requirements are present — ontology fills gaps later.
        if isinstance(out.get("job_requirements"), dict) or _looks_like_requirements(out):
            out["inferred_competencies"] = []

    return out


def _validate_bundle(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise SchemaValidationError("intelligence bundle must be an object")
    normalized = normalize_intelligence_raw(data)
    has_requirements = isinstance(normalized.get("job_requirements"), dict) or (
        _looks_like_requirements(normalized)
    )
    has_inferences = isinstance(normalized.get("inferred_competencies"), list)
    if not has_requirements and not has_inferences:
        raise SchemaValidationError(
            "intelligence bundle missing job_requirements and inferred_competencies"
        )
    # Mutate in place so callers of call_stage_json receive the normalized shape.
    data.clear()
    data.update(normalized)


def _skills_from_jd_text(jd_text: str, ontology: SkillOntology) -> list[str]:
    """Cheap deterministic skill cues from JD text via ontology + tech lexicon."""
    from intelligent_tailoring.scope_validator import extract_tech_mentions

    text = (jd_text or "").lower()
    if not text.strip():
        return []
    found: list[str] = []

    # Lexicon hits (React, TypeScript, …) — most reliable for software JDs.
    for tech in sorted(extract_tech_mentions(jd_text), key=lambda s: (-len(s), s)):
        label = str(tech).strip()
        if label and label not in found:
            found.append(label)

    sources: list[str] = []
    for rel in ontology.relationships or []:
        for src in rel.sources or ():
            if str(src).strip():
                sources.append(str(src).strip())
        target = str(rel.target or "").strip()
        if target:
            sources.append(target)
        for extra in rel.also_implies or ():
            if str(extra).strip():
                sources.append(str(extra).strip())
    for key in ontology._index.keys():
        if key:
            sources.append(key)

    sources = sorted(dict.fromkeys(sources), key=lambda s: (-len(s), s.lower()))
    for term in sources:
        t = term.strip()
        if len(t) < 2:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])"
        try:
            matched = bool(re.search(pattern, text))
        except re.error:
            matched = t.lower() in text
        if matched and t not in found:
            found.append(t)
        if len(found) >= 24:
            break
    return found


def _responsibilities_from_jd(jd_text: str) -> list[str]:
    lines: list[str] = []
    for raw in (jd_text or "").splitlines():
        line = raw.strip().lstrip("•*-–—").strip()
        if len(line) < 20 or len(line) > 220:
            continue
        if line not in lines:
            lines.append(line)
        if len(lines) >= 10:
            break
    if lines:
        return lines
    # Fallback: split long single-block JDs into sentence-like chunks.
    compact = re.sub(r"\s+", " ", (jd_text or "").strip())
    chunks = re.split(r"(?<=[.!;])\s+", compact)
    for chunk in chunks:
        text = chunk.strip()
        if 20 <= len(text) <= 220 and text not in lines:
            lines.append(text)
        if len(lines) >= 8:
            break
    return lines


def _deterministic_intelligence_bundle(
    *,
    jd_text: str,
    resume_text: str,
    language: str,
    ontology: SkillOntology,
    reason: str,
) -> dict[str, Any]:
    skills = _skills_from_jd_text(jd_text, ontology)
    responsibilities = _responsibilities_from_jd(jd_text)
    requirements = validate_requirements(
        {
            "required_skills": skills,
            "preferred_skills": [],
            "responsibilities": responsibilities,
            "tools_technologies": skills[:16],
            "industry_terminology": [],
            "seniority_level": "",
            "soft_skills": [],
            "education_certifications": [],
            "ats_keywords": skills[:20],
            "hard_requirements": skills,
            "soft_requirements": [],
            "language": language or "en",
        }
    )
    requirements["sparse"] = not bool(skills or responsibilities)
    requirements["jd_text"] = jd_text
    inferred = _from_ontology_hits(
        resume_text, requirements, ontology, language=language
    )
    logger.warning(
        "intelligence_bundle: using deterministic fallback (%s) skills=%d inferred=%d",
        reason,
        len(skills),
        len(inferred),
    )
    return {
        "job_requirements": requirements,
        "inferred_competencies": _dedupe_competencies(inferred),
        "company_cues": {"verified_facts_only": True},
        "genuine_gaps": [],
        "safe_inferences": [],
        "forbidden_claims": [],
        "requirement_priorities": list(skills[:12]),
        "primary_llm_calls": 0,
        "_from_cache": False,
        "_deterministic_fallback": True,
        "fallback_reason": reason,
    }


def _empty_sparse_bundle(*, jd_text: str, language: str) -> dict[str, Any]:
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


def _finalize_bundle(
    *,
    raw: dict[str, Any],
    jd_text: str,
    resume_text: str,
    language: str,
    ontology: SkillOntology,
) -> dict[str, Any]:
    raw = normalize_intelligence_raw(raw)
    req_raw = raw.get("job_requirements") or raw
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
    allow_llm: bool = True,
) -> dict[str, Any]:
    """Job requirements + inferred competencies.

    When ``allow_llm`` is False (single smart-agent mode), uses the deterministic
    JD/ontology path only — the GPT-5 smart agent handles deep understanding
    during the rewrite call.
    """
    ontology = ontology or get_ontology()
    job_profile = parse_stored_job_profile(job.get("job_profile"))
    jd_text = jd_snapshot or build_job_payload(job, job_profile)
    jd_text = truncate_text(jd_text, OPENAI_JOB_MAX_CHARS)
    title = str(job.get("title") or "")
    company = str(job.get("company") or "")
    resume_text = str(resume_facts.get("raw_text") or "")

    if len(jd_text.strip()) < 40:
        return _empty_sparse_bundle(jd_text=jd_text, language=language)

    if not allow_llm:
        return _deterministic_intelligence_bundle(
            jd_text=jd_text,
            resume_text=resume_text,
            language=language,
            ontology=ontology,
            reason="single_smart_agent_deterministic_prep",
        )

    record_primary_llm_call("candidate_opportunity_intelligence")
    # Legacy multi-agent path: planning uses OPENAI_MODEL; writing uses
    # OPENAI_TAILOR_MODEL (gpt-5) in the smart-agent rewrite.
    try:
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
                f"{OPENAI_MODEL}|{language}|{title}|{jd_text[:2500]}|"
                f"{resume_text[:2000]}|{knowledge_base_summary[:500]}"
            ),
            model=OPENAI_MODEL,
        )
    except SchemaValidationError as exc:
        logger.warning(
            "intelligence_bundle LLM schema failed after retry — "
            "continuing with deterministic JD/ontology path: %s",
            exc,
        )
        return _deterministic_intelligence_bundle(
            jd_text=jd_text,
            resume_text=resume_text,
            language=language,
            ontology=ontology,
            reason=str(exc)[:240],
        )

    return _finalize_bundle(
        raw=raw,
        jd_text=jd_text,
        resume_text=resume_text,
        language=language,
        ontology=ontology,
    )


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
