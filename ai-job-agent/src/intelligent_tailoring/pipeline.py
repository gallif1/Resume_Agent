"""Intelligent Resume Tailoring — staged pipeline orchestrator.

Stages (in order):
1. Resume extraction (deterministic, reuses existing parser/profile)
2. Job requirement extraction (LLM)
3. Skill/terminology normalization (ontology, deterministic)
4. Semantic inference (ontology + LLM, Strongly Inferred only)
5. Evidence mapping (deterministic)
6. Requirement ranking (deterministic)
7. Content triage (LLM)
8. Resume generation (LLM)
9. Claim validation (LLM assist + deterministic enforcement) — ALWAYS runs
10. ATS/relevance scoring (deterministic rubric)
11. Assemble structured result + change report

No tailored resume is returned without passing through the claim validator.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ai_client import is_ai_available
from intelligent_tailoring.cache import (
    content_hash,
    read_tailoring_cache,
    write_tailoring_cache,
)
from intelligent_tailoring.ontology import dedupe_skills, get_ontology
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    SchemaValidationError,
    TailoringResult,
    tailored_resume_to_legacy_cv,
    validate_tailoring_result,
)
from intelligent_tailoring.stages.ats_scoring import (
    rescore_after_tailoring,
    score_from_evidence_map,
)
from intelligent_tailoring.stages.claim_validation import run_claim_validation
from intelligent_tailoring.stages.content_triage import run_content_triage
from intelligent_tailoring.stages.evidence_mapping import build_evidence_map
from intelligent_tailoring.stages.job_requirement_extraction import (
    extract_job_requirements,
)
from intelligent_tailoring.stages.normalization import normalize_terms
from intelligent_tailoring.stages.requirement_ranking import rank_requirements
from intelligent_tailoring.stages.resume_extraction import extract_structured_resume
from intelligent_tailoring.services.job_analyzer import analyze_job
from intelligent_tailoring.services.resume_analyzer import (
    analyze_resume,
    resume_facts_to_baseline_resume,
)
from intelligent_tailoring.services.resume_rebuilder import rebuild_resume_structure
from intelligent_tailoring.services.resume_rewriter import rewrite_resume_with_strategy
from intelligent_tailoring.services.resume_scorer import score_resume_content
from intelligent_tailoring.services.resume_validator import (
    should_regenerate,
    validate_tailoring_depth,
)
from intelligent_tailoring.services.tailoring_reporter import build_tailoring_report
from intelligent_tailoring.services.tailoring_strategy_builder import build_tailoring_strategy
from intelligent_tailoring.stages.semantic_inference import run_semantic_inference
from match_tailor_service import (
    MatchTailorError,
    align_recommendation,
    build_honest_professional_title,
    enforce_honest_title_summary,
)

logger = logging.getLogger("intelligent_tailoring")

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")


class IntelligentTailorError(MatchTailorError):
    """Raised when the intelligent tailoring pipeline cannot complete."""


def detect_language(*texts: str, preferred: str | None = None) -> str:
    """Preserve an explicitly selected language; else detect he vs en."""
    if preferred in ("he", "en"):
        return preferred
    blob = "\n".join(t for t in texts if t)
    if not blob.strip():
        return "en"
    hebrew = len(_HEBREW_RE.findall(blob))
    latin = len(re.findall(r"[A-Za-z]", blob))
    if hebrew > latin * 0.3 and hebrew >= 20:
        return "he"
    return "en"


def run_intelligent_tailoring(
    *,
    cv_profile: dict[str, Any],
    job: dict[str, Any],
    use_cache: bool = True,
    source_documents: str | None = None,
    language: str | None = None,
    regenerate_section: str | None = None,
) -> dict[str, Any]:
    """Run the full staged pipeline and return a dual-schema result.

    Returns both the new TailoringResult fields and the legacy match_tailor
    keys so ``tailor_cv_service`` / existing UI keep working.
    """
    if not is_ai_available():
        raise IntelligentTailorError(
            "OPENAI_API_KEY is not configured — cannot tailor this resume",
            status_code=503,
        )

    # --- Stage 1: resume extraction ---
    resume_facts = extract_structured_resume(cv_profile, source_documents)
    display_skills = list(resume_facts.get("skills") or [])
    if resume_facts.get("sparse"):
        raise IntelligentTailorError(
            "Resume text is too short or sparse to extract meaningful structured data",
            status_code=400,
        )

    # JD snapshot (exact text used at generation time)
    from match_tailor_service import build_job_payload
    from job_analyzer import parse_stored_job_profile

    job_profile = parse_stored_job_profile(job.get("job_profile"))
    jd_snapshot = build_job_payload(job, job_profile)
    if len((jd_snapshot or "").strip()) < 40:
        raise IntelligentTailorError(
            "Job description is too short or sparse to extract meaningful requirements",
            status_code=400,
        )

    output_language = detect_language(
        resume_facts.get("raw_text") or "",
        jd_snapshot,
        preferred=language,
    )

    resume_text = str(resume_facts.get("raw_text") or "")

    # Pipeline-level cache keyed on (resume version, JD snapshot)
    if use_cache and not regenerate_section:
        cached = read_tailoring_cache(
            resume_text=resume_text,
            jd_text=jd_snapshot,
            language=output_language,
        )
        if cached is not None:
            cached["from_cache"] = True
            return _ensure_legacy_fields(cached, job=job, cv_profile=cv_profile)

    ontology = get_ontology()

    try:
        # --- Stage 2: job requirement extraction ---
        requirements = extract_job_requirements(
            job, use_cache=use_cache, jd_snapshot=jd_snapshot
        )

        # Language mismatch: preserve selected output language (do not silently translate)
        jd_language = str(requirements.get("language") or output_language)
        if jd_language != output_language:
            logger.info(
                "intelligent_tailoring: JD language=%s resume/output language=%s "
                "(preserving output language, not auto-translating)",
                jd_language,
                output_language,
            )

        # --- Stage 3: normalization ---
        normalized = normalize_terms(requirements, resume_facts, ontology=ontology)
        requirements = normalized["requirements"]
        resume_facts = {
            **resume_facts,
            "skills": normalized["resume_skills"],
            "display_skills": display_skills,
        }

        # --- Stage 4: semantic inference ---
        inferred = run_semantic_inference(
            resume_facts=resume_facts,
            requirements=requirements,
            language=output_language,
            use_cache=use_cache,
            ontology=ontology,
        )

        # --- Stage 5: evidence mapping ---
        evidence_map = build_evidence_map(
            resume_facts=resume_facts,
            requirements=requirements,
            inferred=inferred,
            ontology=ontology,
        )

        # --- Stage 6: requirement ranking ---
        ranked = rank_requirements(requirements, evidence_map)

        # Original (pre-tailor) match score from evidence map
        original_scoring = score_from_evidence_map(
            evidence_map, job_title=str(job.get("title") or "")
        )
        original_score = int(original_scoring["realistic_match_score"])

        # --- Stage 7: content triage ---
        triage = run_content_triage(
            resume_facts=resume_facts,
            ranked_requirements=ranked,
            language=output_language,
            use_cache=use_cache,
        )

        # --- Deep tailoring: strategy → score → rebuild → rewrite → validate ---
        job_analysis = analyze_job(
            job,
            use_cache=use_cache,
            jd_snapshot=jd_snapshot,
            requirements=requirements,
        )
        strategy = build_tailoring_strategy(
            job_analysis=job_analysis,
            resume_facts=resume_facts,
            evidence_map=evidence_map,
            ranked_requirements=ranked,
            language=output_language,
        )
        content_scores = score_resume_content(
            resume_facts=resume_facts,
            strategy=strategy,
            job_analysis=job_analysis,
            evidence_map=evidence_map,
        )
        rebuilt = rebuild_resume_structure(
            resume_facts=resume_facts,
            scores=content_scores,
            strategy=strategy,
        )
        baseline_resume = resume_facts_to_baseline_resume(resume_facts)

        generated: dict[str, Any] = {}
        validation_depth: dict[str, Any] = {}
        regeneration_attempt = 0
        while True:
            generated = rewrite_resume_with_strategy(
                resume_facts=resume_facts,
                rebuilt_resume=rebuilt,
                strategy=strategy,
                scores=content_scores,
                ranked_requirements=ranked,
                inferred=inferred,
                evidence_map=evidence_map,
                triage=triage,
                language=output_language,
                use_cache=use_cache,
                regeneration_attempt=regeneration_attempt,
            )
            validation_depth = validate_tailoring_depth(
                tailored_resume=generated["tailored_resume"],
                baseline_resume=baseline_resume,
                strategy=strategy,
            )
            if not should_regenerate(validation_depth, regeneration_attempt):
                break
            regeneration_attempt += 1
            logger.info(
                "intelligent_tailoring: similarity %.2f > threshold — regenerating (attempt %d)",
                validation_depth.get("overall_similarity") or 0,
                regeneration_attempt,
            )

        # Optional section regenerate: keep other sections from a prior generation
        # is handled at the API layer; here regenerate_section just annotates.
        if regenerate_section:
            generated["regenerate_section"] = regenerate_section

        # --- Stage 9: claim validation (mandatory) ---
        validation = run_claim_validation(
            original_resume_text=resume_text,
            tailored_resume=generated["tailored_resume"],
            evidence_map=evidence_map,
            change_log=generated.get("change_log") or [],
            inferred=inferred,
            job_requirements=requirements,
            use_cache=use_cache,
            run_llm_assist=True,
        )
        cleaned_resume = validation["cleaned_resume"]
        # Deduplicate skills after validation
        cleaned_resume["skills"] = dedupe_skills(cleaned_resume.get("skills") or [])

        # Honest title/summary enforcement (reuse existing helpers)
        hard_score = int(original_scoring.get("hard_score_pct") or 0)
        hard_reqs = list(original_scoring.get("hard_requirements") or [])
        cleaned_resume["summary"] = cleaned_resume.get("professional_summary") or ""
        title, summary, _flags = enforce_honest_title_summary(
            professional_title=str(cleaned_resume.get("professional_title") or ""),
            summary=str(cleaned_resume.get("summary") or ""),
            job_title=str(job.get("title") or ""),
            hard_score_pct=hard_score,
            hard_requirements=hard_reqs,
        )
        if not title:
            title = build_honest_professional_title(
                str(job.get("title") or ""),
                hard_reqs,
            )
        cleaned_resume["professional_title"] = title
        cleaned_resume["summary"] = summary
        cleaned_resume["professional_summary"] = summary

        # --- Stage 10: ATS scoring after tailoring ---
        tailored_scoring = rescore_after_tailoring(
            evidence_map=evidence_map,
            tailored_resume=cleaned_resume,
            original_resume_text=resume_text,
            job_title=str(job.get("title") or ""),
        )
        tailored_score = int(tailored_scoring["realistic_match_score"])

        tailoring_report = build_tailoring_report(
            strategy=strategy,
            scores=content_scores,
            validation=validation_depth,
            generated=generated,
            original_score=original_score,
            tailored_score=tailored_score,
            regeneration_attempts=regeneration_attempt,
        )

        matched = generated.get("matched_requirements") or [
            e["requirement"]
            for e in evidence_map
            if e.get("candidate_status") in ("MATCH", "PARTIAL")
            and e.get("importance") in ("hard", "soft")
        ]
        missing = generated.get("missing_requirements") or [
            e["requirement"]
            for e in evidence_map
            if e.get("candidate_status") == "MISSING"
            and e.get("importance") == "hard"
        ]

        removed = list(generated.get("removed_or_deprioritized_content") or [])
        for item in triage.get("triage") or []:
            if item.get("action") == "Remove" and item.get("original_text"):
                text = str(item["original_text"])
                if text not in removed:
                    removed.append(text)

        result_payload = {
            "tailored_resume": cleaned_resume,
            "matched_requirements": matched,
            "missing_requirements": missing,
            "inferred_competencies": validation.get("inferred_competencies")
            or [i.to_dict() for i in inferred],
            "removed_or_deprioritized_content": removed,
            "ats_keywords_added": list(generated.get("ats_keywords_added") or []),
            "change_log": validation.get("change_log")
            or generated.get("change_log")
            or [],
            "validation_warnings": validation.get("warnings") or [],
            "original_match_score": original_score,
            "tailored_match_score": tailored_score,
            "language": output_language,
            "evidence_map": evidence_map,
            "job_requirements": {
                k: v
                for k, v in requirements.items()
                if not str(k).startswith("_") and k != "jd_text"
            },
            "jd_snapshot": jd_snapshot,
            "jd_snapshot_hash": content_hash(jd_snapshot),
            "resume_hash": content_hash(resume_text),
            "from_cache": False,
            "pipeline_version": PIPELINE_VERSION,
            "claim_validator_passed": True,
            "rejected_statements": validation.get("rejected_statements") or [],
            "tailoring_strategy": strategy,
            "content_scores": content_scores,
            "tailoring_report": tailoring_report,
            "tailoring_validation": validation_depth,
        }

        # Strict schema validation of the assembled result
        validated = validate_tailoring_result(result_payload)
        result_payload.update(validated.to_dict())
        # Restore non-schema audit fields stripped by to_dict
        result_payload["jd_snapshot"] = jd_snapshot
        result_payload["jd_snapshot_hash"] = content_hash(jd_snapshot)
        result_payload["resume_hash"] = content_hash(resume_text)
        result_payload["claim_validator_passed"] = True
        result_payload["rejected_statements"] = validation.get("rejected_statements") or []
        result_payload["pipeline_version"] = PIPELINE_VERSION
        result_payload["tailoring_strategy"] = strategy
        result_payload["tailoring_report"] = tailoring_report
        result_payload["tailoring_validation"] = validation_depth

    except SchemaValidationError as exc:
        raise IntelligentTailorError(
            f"Tailoring failed schema validation: {exc}", status_code=502
        ) from exc
    except MatchTailorError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("intelligent_tailoring pipeline failed")
        raise IntelligentTailorError(
            f"Tailoring pipeline failed: {exc}", status_code=502
        ) from exc

    legacy = _ensure_legacy_fields(result_payload, job=job, cv_profile=cv_profile)

    if use_cache:
        write_tailoring_cache(
            legacy,
            resume_text=resume_text,
            jd_text=jd_snapshot,
            language=output_language,
        )

    return legacy


def _ensure_legacy_fields(
    result: dict[str, Any],
    *,
    job: dict[str, Any],
    cv_profile: dict[str, Any],  # noqa: ARG001
) -> dict[str, Any]:
    """Attach legacy match_tailor keys expected by tailor_cv_service / API."""
    tailored = result.get("tailored_resume") or {}
    legacy_cv = tailored_resume_to_legacy_cv(tailored)

    hard = []
    soft = []
    for entry in result.get("evidence_map") or []:
        if entry.get("importance") == "inferred":
            continue
        item = {
            "requirement": entry.get("requirement") or "",
            "candidate_status": entry.get("candidate_status") or "MISSING",
            "evidence_or_gap": entry.get("supporting_evidence") or "",
        }
        if entry.get("importance") == "hard":
            hard.append(item)
        elif entry.get("importance") == "soft":
            soft.append(item)

    score = int(result.get("tailored_match_score") or 0)
    original = int(result.get("original_match_score") or score)
    recommendation = align_recommendation(
        "APPLY_WITH_HONEST_FRAMING", score
    )

    missing_critical = [
        {"skill": m, "reason": "No supporting evidence in original resume"}
        for m in (result.get("missing_requirements") or [])
    ]
    transferable = [
        {
            "gap": inf.get("related_requirement") or "",
            "how_to_honestly_frame_existing_experience": inf.get("statement") or "",
        }
        for inf in (result.get("inferred_competencies") or [])
        if isinstance(inf, dict)
    ]

    result = {
        **result,
        "requirement_extraction": {
            "hard_requirements": hard,
            "soft_requirements": soft,
        },
        "scoring": {
            "hard_score_pct": 0,
            "soft_score_pct": 0,
            "hard_cap_applied": False,
            "realistic_match_score": score,
            "score_rationale": (
                f"Evidence-mapped honest score after intelligent tailoring "
                f"(before={original}, after={score})."
            ),
        },
        "key_matching_points": list(result.get("matched_requirements") or []),
        "missing_critical_skills": missing_critical,
        "transferable_skills_framing": transferable,
        "tailored_cv": legacy_cv,
        "recommendation": recommendation,
        "score_validation": {
            "model_reported_score": score,
            "recomputed_composite_score": score,
            "score_overridden": False,
            "cap": None,
            "dropped_unsupported_skills": list(result.get("rejected_statements") or []),
            "claim_validator_passed": bool(result.get("claim_validator_passed")),
        },
        "realistic_match_score": score,
    }
    # Fill scoring hard/soft from evidence when available
    from intelligent_tailoring.stages.ats_scoring import score_from_evidence_map

    if result.get("evidence_map"):
        computed = score_from_evidence_map(
            result["evidence_map"], job_title=str(job.get("title") or "")
        )
        result["scoring"]["hard_score_pct"] = computed.get("hard_score_pct") or 0
        result["scoring"]["soft_score_pct"] = computed.get("soft_score_pct") or 0
        result["scoring"]["hard_cap_applied"] = bool(computed.get("hard_cap_applied"))
        result["score_validation"]["cap"] = computed.get("cap")
    return result


def apply_change_decisions(
    result: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Accept/reject individual change_log items; keep document consistent.

    Rejected changes restore ``original_text`` into the tailored resume where the
    ``new_text`` appears (summary, skills, bullets). No orphaned references.
    """
    change_log = list(result.get("change_log") or [])
    decision_by_idx: dict[int, bool] = {}
    for d in decisions:
        try:
            idx = int(d.get("index"))
        except (TypeError, ValueError):
            continue
        decision_by_idx[idx] = bool(d.get("accepted"))

    resume = dict(result.get("tailored_resume") or result.get("tailored_cv") or {})
    summary = str(resume.get("professional_summary") or resume.get("summary") or "")
    skills = [str(s) for s in (resume.get("skills") or [])]
    experience = [dict(e) for e in (resume.get("experience") or []) if isinstance(e, dict)]
    projects = [dict(p) for p in (resume.get("projects") or []) if isinstance(p, dict)]

    updated_log = []
    for i, raw in enumerate(change_log):
        item = dict(raw) if isinstance(raw, dict) else {}
        if i in decision_by_idx:
            accepted = decision_by_idx[i]
            item["accepted"] = accepted
            if not accepted:
                new_text = str(item.get("new_text") or "").strip()
                original = str(item.get("original_text") or "").strip()
                if new_text:
                    if summary.strip() == new_text:
                        summary = original
                    skills = [
                        (original if s.strip() == new_text and original else s)
                        for s in skills
                        if not (s.strip() == new_text and not original)
                    ]
                    for entry in experience:
                        bullets = []
                        for b in entry.get("bullets") or []:
                            if str(b).strip() == new_text:
                                if original:
                                    bullets.append(original)
                                # else drop
                            else:
                                bullets.append(b)
                        entry["bullets"] = bullets
                    for entry in projects:
                        bullets = []
                        for b in entry.get("bullets") or []:
                            if str(b).strip() == new_text:
                                if original:
                                    bullets.append(original)
                            else:
                                bullets.append(b)
                        entry["bullets"] = bullets
                        if str(entry.get("description") or "").strip() == new_text:
                            entry["description"] = original
        updated_log.append(item)

    resume["professional_summary"] = summary
    resume["summary"] = summary
    resume["skills"] = skills
    resume["experience"] = experience
    resume["projects"] = projects

    updated = {
        **result,
        "change_log": updated_log,
        "tailored_resume": resume,
        "tailored_cv": tailored_resume_to_legacy_cv(resume),
    }
    return updated


def regenerate_section(
    *,
    cv_profile: dict[str, Any],
    job: dict[str, Any],
    section: str,
    previous_result: dict[str, Any] | None = None,
    use_cache: bool = False,
    source_documents: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Regenerate one section while preserving the rest of an approved draft."""
    section = (section or "").strip().lower()
    valid = {
        "professional_summary",
        "summary",
        "skills",
        "experience",
        "projects",
        "education",
        "certifications",
    }
    if section not in valid:
        raise IntelligentTailorError(
            f"Unknown section '{section}'. Expected one of: {sorted(valid)}",
            status_code=400,
        )

    fresh = run_intelligent_tailoring(
        cv_profile=cv_profile,
        job=job,
        use_cache=use_cache,
        source_documents=source_documents,
        language=language,
        regenerate_section=section,
    )
    if not previous_result:
        return fresh

    prev_resume = dict(
        previous_result.get("tailored_resume")
        or previous_result.get("tailored_cv")
        or {}
    )
    new_resume = dict(fresh.get("tailored_resume") or fresh.get("tailored_cv") or {})

    section_key = "professional_summary" if section == "summary" else section
    # Map aliases
    if section_key == "professional_summary":
        prev_resume["professional_summary"] = new_resume.get("professional_summary") or new_resume.get("summary") or ""
        prev_resume["summary"] = prev_resume["professional_summary"]
    else:
        prev_resume[section_key] = new_resume.get(section_key) or prev_resume.get(section_key)

    from intelligent_tailoring.schemas import InferredCompetency, normalize_inference_category

    resume_facts = extract_structured_resume(cv_profile, source_documents)
    inferred_objs: list[InferredCompetency] = []
    for raw in fresh.get("inferred_competencies") or []:
        if isinstance(raw, dict) and raw.get("statement"):
            inferred_objs.append(
                InferredCompetency(
                    statement=str(raw.get("statement") or ""),
                    supporting_evidence=str(raw.get("supporting_evidence") or ""),
                    reasoning=str(raw.get("reasoning") or ""),
                    confidence_score=float(raw.get("confidence_score") or 0),
                    related_requirement=str(raw.get("related_requirement") or ""),
                    ontology_rule_id=str(raw.get("ontology_rule_id") or ""),
                    inference_category=normalize_inference_category(
                        raw.get("inference_category") or "Strongly Inferred"
                    ),
                )
            )
    validation = run_claim_validation(
        original_resume_text=str(resume_facts.get("raw_text") or ""),
        tailored_resume=prev_resume,
        evidence_map=list(
            fresh.get("evidence_map") or previous_result.get("evidence_map") or []
        ),
        change_log=list(fresh.get("change_log") or []),
        inferred=inferred_objs,
        job_requirements=fresh.get("job_requirements") or {},
        use_cache=False,
        run_llm_assist=False,
    )
    cleaned = validation["cleaned_resume"]
    merged = {
        **fresh,
        "tailored_resume": cleaned,
        "tailored_cv": tailored_resume_to_legacy_cv(cleaned),
        "change_log": validation.get("change_log") or fresh.get("change_log"),
        "validation_warnings": validation.get("warnings") or [],
        "claim_validator_passed": True,
        "regenerated_section": section_key,
        "from_cache": False,
    }
    return _ensure_legacy_fields(merged, job=job, cv_profile=cv_profile)
