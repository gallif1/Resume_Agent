"""Intelligent Resume Tailoring — four merged LLM-agent pipeline.

Universal, profession-agnostic evidence-based flow:

Merged Agent 1 — Candidate & Opportunity Intelligence
  (Resume Knowledge + Job Intelligence + Company Intelligence + Evidence Mapping)

Merged Agent 2 — Strategy & Content Selection
  (Resume Strategy + Resume Tailoring; triage rules composed into the rewrite)

Merged Agent 3 — Human Writing & Credibility Review
  (Claim Validation + Human Resume Writer + Senior Recruiter Review)

Merged Agent 4 — Final Hiring, ATS & One-Page Review
  (Hiring Manager Simulation + Final Quality + ATS + One-page enforcement)

Legacy specialist modules remain as internal helpers. Maximum four primary
LLM calls under normal conditions. Deterministic work stays in code.
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
from intelligent_tailoring.agents.base import AgentContext
from intelligent_tailoring.agents.claim_validation_agent import ClaimValidationAgent
from intelligent_tailoring.agents.company_intelligence_agent import (
    CompanyIntelligenceAgent,
)
from intelligent_tailoring.agents.hiring_manager_agent import (
    HiringManagerSimulationAgent,
)
from intelligent_tailoring.agents.job_intelligence_agent import JobIntelligenceAgent
from intelligent_tailoring.agents.orchestrator import attach_quality_intelligence
from intelligent_tailoring.agents.resume_tailoring_agent import ResumeTailoringAgent
from intelligent_tailoring.agents.schemas import (
    ClaimValidationInput,
    CompanyIntelligenceInput,
    HiringManagerInput,
    JobIntelligenceInput,
    TailoringAgentInput,
)
from intelligent_tailoring.knowledge_base import score_facts_for_job
from intelligent_tailoring.ontology import dedupe_skills, get_ontology
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    SchemaValidationError,
    tailored_resume_to_legacy_cv,
    validate_tailoring_result,
)
from intelligent_tailoring.stages.ats_scoring import (
    rescore_after_tailoring,
    score_from_evidence_map,
)
from intelligent_tailoring.stages.claim_validation import run_claim_validation
from intelligent_tailoring.stages.content_triage import run_content_triage
from intelligent_tailoring.stages.intelligence_bundle import (
    knowledge_base_compact_summary,
    run_intelligence_bundle_llm,
)
from intelligent_tailoring.stages.merged_writing import run_merged_writing_review
from intelligent_tailoring.stages.normalization import normalize_terms
from intelligent_tailoring.stages.requirement_ranking import rank_requirements
from intelligent_tailoring.stages.resume_extraction import extract_structured_resume
from intelligent_tailoring.services.job_analyzer import analyze_job
from intelligent_tailoring.services.missed_evidence import (
    enrich_strategy_with_missed_evidence,
    find_missed_evidence,
)
from intelligent_tailoring.services.quality import (
    evaluate_tailoring_quality,
    should_regenerate_for_quality,
)
from intelligent_tailoring.services.resume_analyzer import resume_facts_to_baseline_resume
from intelligent_tailoring.services.resume_rebuilder import rebuild_resume_structure
from intelligent_tailoring.services.resume_scorer import score_resume_content
from intelligent_tailoring.services.resume_validator import (
    should_regenerate,
    validate_tailoring_depth,
)
from intelligent_tailoring.services.tailoring_reporter import build_tailoring_report
from intelligent_tailoring.llm_utils import begin_llm_metrics, get_llm_metrics
from intelligent_tailoring.gate_severity import classify_quality_gates
from intelligent_tailoring.component_cache import (
    get_cached_company_profile,
    get_cached_job_profile,
    set_cached_company_profile,
    set_cached_job_profile,
    get_cached_knowledge,
    set_cached_knowledge,
)
from intelligent_tailoring.progress import ProgressReporter
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
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Run the multi-agent pipeline and return a dual-schema result.

    Returns both TailoringResult fields and legacy match_tailor keys so
    ``tailor_cv_service`` / existing UI keep working.
    """
    return run_intelligent_tailoring_agents(
        cv_profile=cv_profile,
        job=job,
        use_cache=use_cache,
        source_documents=source_documents,
        language=language,
        regenerate_section=regenerate_section,
        progress_callback=progress_callback,
    )


def run_intelligent_tailoring_agents(
    *,
    cv_profile: dict[str, Any],
    job: dict[str, Any],
    use_cache: bool = True,
    source_documents: str | None = None,
    language: str | None = None,
    regenerate_section: str | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Production four-agent implementation."""
    import time as _time

    progress = ProgressReporter(progress_callback)
    begin_llm_metrics()
    pipeline_started = _time.perf_counter()
    if not is_ai_available():
        raise IntelligentTailorError(
            "OPENAI_API_KEY is not configured — cannot tailor this resume",
            status_code=503,
        )

    # JD snapshot (exact text used at generation time)
    from match_tailor_service import build_job_payload
    from job_analyzer import parse_stored_job_profile

    stored_job_profile = parse_stored_job_profile(job.get("job_profile"))
    jd_snapshot = build_job_payload(job, stored_job_profile)
    if len((jd_snapshot or "").strip()) < 40:
        raise IntelligentTailorError(
            "Job description is too short or sparse to extract meaningful requirements",
            status_code=400,
        )

    # --- Agents 1–5: knowledge, job, company, evidence, strategy (initial) ---
    # Knowledge runs first for language detection / cache keys.
    from intelligent_tailoring.agents.resume_knowledge_agent import ResumeKnowledgeAgent
    from intelligent_tailoring.agents.schemas import ResumeKnowledgeInput

    progress.started(
        "candidate_opportunity_intelligence",
        "Reading candidate profile…",
        agent_id="resume_knowledge",
    )
    # Deterministic knowledge extraction (never an LLM call). Component cache
    # records reuse across jobs so later agents send compact KB summaries only.
    _resume_probe = str(
        (cv_profile or {}).get("raw_text") or source_documents or ""
    )
    _kb_hash = content_hash(_resume_probe)
    cached_kb_payload = get_cached_knowledge(_kb_hash) if use_cache else None
    knowledge_result = ResumeKnowledgeAgent().run(
        ResumeKnowledgeInput(
            cv_profile=cv_profile,
            source_documents=source_documents,
            target_output_language=language,
        ),
        AgentContext(use_cache=use_cache, language=language or "en"),
    )
    knowledge_cache_hit = bool(cached_kb_payload)
    if use_cache and not knowledge_cache_hit:
        set_cached_knowledge(
            knowledge_result.output.content_hash or _kb_hash,
            {
                "resume_facts": knowledge_result.output.resume_facts,
                "content_hash": knowledge_result.output.content_hash,
                "coverage": knowledge_result.output.coverage,
                "fact_count": knowledge_result.output.fact_count,
            },
        )
    knowledge_result.metrics = {
        **knowledge_result.metrics,
        "component_cache_hit": knowledge_cache_hit,
    }
    knowledge = knowledge_result.output
    kb = knowledge.knowledge_base
    resume_facts = dict(knowledge.resume_facts)
    display_skills = list(
        resume_facts.get("display_skills") or resume_facts.get("skills") or []
    )

    if "resume_sparse" in knowledge_result.warnings or resume_facts.get("sparse"):
        raise IntelligentTailorError(
            "Resume text is too short or sparse to extract meaningful structured data",
            status_code=400,
        )

    output_language = detect_language(
        kb.raw_text or resume_facts.get("raw_text") or "",
        jd_snapshot,
        preferred=language or kb.target_output_language,
    )
    kb.target_output_language = output_language
    resume_text = str(kb.raw_text or resume_facts.get("raw_text") or "")
    progress.completed(
        "resume_knowledge",
        f"Candidate evidence extracted ({len(kb.facts)} facts).",
        agent_id="resume_knowledge",
        fact_count=len(kb.facts),
    )
    try:
        from intelligent_tailoring.canonical_resume import log_stage_inventory

        log_stage_inventory(
            generation_id=str(job.get("id") or "gen"),
            stage="resume_knowledge",
            resume_facts=resume_facts,
            extra={"fact_count": len(kb.facts)},
        )
    except Exception:
        pass

    if use_cache and not regenerate_section:
        cached = read_tailoring_cache(
            resume_text=f"{kb.content_hash}|{resume_text}",
            jd_text=jd_snapshot,
            language=output_language,
        )
        if cached is not None:
            cached["from_cache"] = True
            progress.decision(
                "resume_knowledge",
                {
                    "action": "cache",
                    "text": "Reusing a recent tailored draft for this job (cache hit).",
                    "target": "pipeline",
                    "reason": "identical resume + job inputs",
                },
            )
            progress.completed("final_polish", "Cached resume ready.", agent_id="final_polish")
            return _ensure_legacy_fields(cached, job=job, cv_profile=cv_profile)

    ontology = get_ontology()
    agent_trace: list[dict[str, Any]] = []
    agent_timings_ms: dict[str, int] = {}
    agent_trace.append(
        {
            "agent_id": knowledge_result.agent_id,
            "metrics": knowledge_result.metrics,
            "warnings": knowledge_result.warnings,
        }
    )

    try:
        # ---- Merged Agent 1: Candidate & Opportunity Intelligence ----
        # Deterministic company prep + one LLM call for job+inference.
        progress.started(
            "candidate_opportunity_intelligence",
            "Analyzing job requirements and mapping evidence…",
            agent_id="job_intelligence",
        )
        jd_hash = content_hash(jd_snapshot)
        job_title = str(job.get("title") or "")
        job_company = str(job.get("company") or "")
        cached_job = (
            get_cached_job_profile(job_company, job_title, jd_hash)
            if use_cache
            else None
        )
        kb_summary = knowledge_base_compact_summary(kb)
        if cached_job and cached_job.get("raw_requirements"):
            bundle = {
                "job_requirements": cached_job["raw_requirements"],
                "inferred_competencies": [],
                "primary_llm_calls": 0,
                "_from_cache": True,
            }
            # Still need inference for this resume — run ontology-only path via
            # intelligence bundle with cache on the job half by passing requirements.
            from intelligent_tailoring.stages.semantic_inference import (
                _from_ontology_hits,
                _dedupe_competencies,
            )

            bundle["inferred_competencies"] = _dedupe_competencies(
                _from_ontology_hits(
                    str(resume_facts.get("raw_text") or ""),
                    cached_job["raw_requirements"],
                    ontology,
                    language=output_language,
                )
            )
            job_cache_hit = True
        else:
            bundle = run_intelligence_bundle_llm(
                job=job,
                resume_facts=resume_facts,
                knowledge_base_summary=kb_summary,
                verified_company_metadata=json.dumps(
                    {
                        "company": job_company,
                        "title": job_title,
                        "location": job.get("location"),
                    },
                    ensure_ascii=False,
                ),
                language=output_language,
                use_cache=use_cache,
                ontology=ontology,
                jd_snapshot=jd_snapshot,
            )
            job_cache_hit = bool(bundle.get("_from_cache"))
            if use_cache and bundle.get("job_requirements"):
                set_cached_job_profile(
                    job_company,
                    job_title,
                    jd_hash,
                    {"raw_requirements": bundle["job_requirements"]},
                )

        job_intel = JobIntelligenceAgent().run(
            JobIntelligenceInput(
                job=job,
                jd_snapshot=jd_snapshot,
                existing_requirements=bundle.get("job_requirements"),
            ),
            AgentContext(use_cache=use_cache, language=output_language),
        )
        job_profile_obj = job_intel.output
        agent_trace.append(
            {
                "agent_id": "candidate_opportunity_intelligence",
                "legacy_agent_id": job_intel.agent_id,
                "metrics": {
                    **job_intel.metrics,
                    "job_cache_hit": job_cache_hit,
                    "knowledge_cache_hit": knowledge_cache_hit,
                },
            }
        )

        # Company Intelligence — deterministic; cache by company metadata hash
        company_meta_hash = content_hash(
            f"{job_company}|{job.get('location')}|{(jd_snapshot or '')[:800]}"
        )
        cached_company = (
            get_cached_company_profile(job_company or "unknown", company_meta_hash)
            if use_cache
            else None
        )
        progress.started(
            "candidate_opportunity_intelligence",
            "Reviewing company context…",
            agent_id="company_intelligence",
        )
        company_intel = CompanyIntelligenceAgent().run(
            CompanyIntelligenceInput(
                job=job,
                job_profile=job_profile_obj,
                jd_snapshot=jd_snapshot,
            ),
            AgentContext(use_cache=use_cache, language=output_language),
        )
        company_profile_obj = company_intel.output
        if use_cache and not cached_company:
            set_cached_company_profile(
                job_company or "unknown",
                company_meta_hash,
                company_profile_obj.to_dict()
                if hasattr(company_profile_obj, "to_dict")
                else {},
            )
        agent_trace.append(
            {
                "agent_id": company_intel.agent_id,
                "metrics": {
                    **company_intel.metrics,
                    "company_cache_hit": bool(cached_company),
                },
            }
        )

        requirements = job_profile_obj.to_legacy_requirements()

        jd_language = str(requirements.get("language") or output_language)
        if jd_language != output_language:
            logger.info(
                "intelligent_tailoring: JD language=%s resume/output language=%s "
                "(preserving output language, not auto-translating)",
                jd_language,
                output_language,
            )

        # Normalization (deterministic tool)
        normalized = normalize_terms(requirements, resume_facts, ontology=ontology)
        requirements = normalized["requirements"]
        resume_facts = {
            **resume_facts,
            "skills": normalized["resume_skills"],
            "display_skills": display_skills,
        }
        # Keep job profile raw requirements in sync after normalization
        job_profile_obj.raw_requirements = requirements

        # Evidence Mapping — uses inferred competencies from Agent 1 bundle
        progress.started(
            "candidate_opportunity_intelligence",
            "Mapping evidence to requirements…",
            agent_id="evidence_mapping",
        )
        inferred = list(bundle.get("inferred_competencies") or [])
        from intelligent_tailoring.agents.evidence_mapping_agent import (
            EvidenceMappingAgent,
        )
        from intelligent_tailoring.agents.schemas import EvidenceMappingInput

        evidence_agent_result = EvidenceMappingAgent().run(
            EvidenceMappingInput(
                resume_facts=resume_facts,
                job_profile=job_profile_obj,
                inferred=inferred,
                knowledge_base=kb,
            ),
            AgentContext(
                use_cache=use_cache,
                language=output_language,
                metadata={"inference_completed": True},
            ),
        )
        evidence_map_obj = evidence_agent_result.output
        evidence_map = evidence_map_obj.to_legacy_list()
        # Merge bundle genuine gaps / forbidden claims into strategy later
        bundle_gaps = list(bundle.get("genuine_gaps") or [])
        bundle_forbidden = list(bundle.get("forbidden_claims") or [])
        agent_trace.append(
            {
                "agent_id": evidence_agent_result.agent_id,
                "metrics": evidence_agent_result.metrics,
            }
        )
        matched_n = sum(
            1
            for e in evidence_map
            if e.get("candidate_status") in ("MATCH", "PARTIAL")
        )
        progress.completed(
            "candidate_opportunity_intelligence",
            f"Opportunity intelligence ready ({matched_n} supported requirements).",
            agent_id="candidate_opportunity_intelligence",
        )

        # Requirement ranking + deterministic triage (LLM triage folded into Agent 2)
        ranked = rank_requirements(requirements, evidence_map)
        original_scoring = score_from_evidence_map(
            evidence_map, job_title=str(job.get("title") or "")
        )
        original_score = int(original_scoring["realistic_match_score"])

        triage = run_content_triage(
            resume_facts=resume_facts,
            ranked_requirements=ranked,
            language=output_language,
            use_cache=use_cache,
            allow_llm=False,
        )

        job_analysis = analyze_job(
            job,
            use_cache=use_cache,
            jd_snapshot=jd_snapshot,
            requirements=requirements,
        )
        fact_scores = score_facts_for_job(kb, job_requirements=requirements)

        from intelligent_tailoring.agents.resume_strategy_agent import (
            ResumeStrategyAgent,
        )
        from intelligent_tailoring.agents.schemas import ResumeStrategyInput

        progress.started(
            "resume_strategy",
            "Selecting the strongest reasons to interview…",
            agent_id="resume_strategy",
        )
        strategy_result = ResumeStrategyAgent().run(
            ResumeStrategyInput(
                job_profile=job_profile_obj,
                company_profile=company_profile_obj,
                evidence_map=evidence_map_obj,
                resume_facts=resume_facts,
                ranked_requirements=ranked,
                fact_scores=fact_scores,
                job_analysis=job_analysis,
                language=output_language,
            ),
            AgentContext(use_cache=use_cache, language=output_language),
        )
        strategy_obj = strategy_result.output
        strategy = strategy_obj.to_legacy()
        agent_trace.append(
            {
                "agent_id": strategy_result.agent_id,
                "metrics": strategy_result.metrics,
            }
        )

        missed = find_missed_evidence(
            kb=kb,
            job_requirements=requirements,
            evidence_map=evidence_map,
            fact_scores=fact_scores,
        )
        strategy = enrich_strategy_with_missed_evidence(strategy, missed)
        if bundle_gaps:
            strategy["genuine_gaps"] = list(
                dict.fromkeys(
                    list(strategy.get("genuine_gaps") or []) + bundle_gaps
                )
            )
        if bundle_forbidden:
            strategy["forbidden_claims"] = list(
                dict.fromkeys(
                    list(strategy.get("forbidden_claims") or []) + bundle_forbidden
                )
            )
        strategy_obj.legacy_strategy = strategy

        # Promote overlooked fact bullets into experience/projects when missing
        resume_facts = _inject_missed_facts(resume_facts, kb, missed)

        # Maximize evidence utilization before content scoring / rewrite
        from intelligent_tailoring.services.evidence_amplifier import (
            apply_evidence_amplification,
        )

        resume_facts, evidence_enrichment = apply_evidence_amplification(
            resume_facts=resume_facts,
            evidence_map=evidence_map,
            strategy=strategy,
            kb=kb,
            resume_text=resume_text,
        )
        strategy.update(
            {
                "evidence_inventory": evidence_enrichment.get("evidence_inventory"),
                "highlight_plan": evidence_enrichment.get("highlight_plan"),
                "must_highlight_in_summary": evidence_enrichment.get(
                    "must_highlight_in_summary"
                ),
                "propagate_terms": evidence_enrichment.get("propagate_terms"),
                "top_interview_reasons": evidence_enrichment.get(
                    "top_interview_reasons"
                ),
            }
        )
        strategy_obj.legacy_strategy = strategy
        for reason in list(strategy.get("top_interview_reasons") or [])[:3]:
            progress.decision(
                "resume_strategy",
                {
                    "action": "emphasize",
                    "text": (
                        f"Highlighting {reason} because it is among the strongest "
                        "evidenced reasons to interview"
                    ),
                    "target": str(reason),
                    "reason": "top interview evidence",
                },
            )
        for unsupported in list(
            (strategy.get("highlight_plan") or {}).get("unsupported_hard") or []
        )[:3]:
            progress.decision(
                "resume_strategy",
                {
                    "action": "omit",
                    "text": (
                        f"Not mentioning {unsupported} because no reliable "
                        "supporting evidence exists"
                    ),
                    "target": str(unsupported),
                    "reason": "unsupported hard requirement",
                },
            )
        progress.completed(
            "resume_strategy",
            "Resume strategy locked around strongest interview evidence.",
            agent_id="resume_strategy",
        )

        content_scores = score_resume_content(
            resume_facts=resume_facts,
            strategy=strategy,
            job_analysis=job_analysis,
            evidence_map=evidence_map,
        )
        content_scores["fact_scores"] = fact_scores
        rebuilt = rebuild_resume_structure(
            resume_facts=resume_facts,
            scores=content_scores,
            strategy=strategy,
        )
        baseline_resume = resume_facts_to_baseline_resume(resume_facts)

        generated: dict[str, Any] = {}
        validation_depth: dict[str, Any] = {}
        quality_report: dict[str, Any] = {}
        validation: dict[str, Any] = {}
        cleaned_resume: dict[str, Any] = {}
        scope_result: dict[str, Any] = {"violations": [], "passed": True}
        deterministic_log: list[dict[str, Any]] = []
        quality_gates: dict[str, Any] = {"passed": True, "failures": []}
        writing_stage: dict[str, Any] = {
            "passed": True,
            "export_ready": True,
            "facts_unchanged": True,
            "review_cycles": 0,
            "quality_gate_failures": [],
        }
        regeneration_attempt = 0
        max_gate_attempts = 1
        # Shared RejectedClaims registry — rejected claims cannot return later
        from intelligent_tailoring.rejected_claims import RejectedClaimsRegistry

        rejected_claims = RejectedClaimsRegistry(max_revision_cycles=3)
        # Seed with strategy forbidden claims that are full phrases
        for phrase in strategy.get("forbidden_claims") or []:
            text = str(phrase).strip()
            if len(text) >= 12:
                rejected_claims.add(
                    text,
                    reason="strategy_forbidden",
                    source_agent="resume_strategy",
                )

        from intelligent_tailoring.scope_validator import validate_resume_tech_scope
        from intelligent_tailoring.change_log import build_deterministic_change_log
        from intelligent_tailoring.quality_gates import evaluate_quality_gates

        previous_generated: dict[str, Any] | None = None
        tailoring_agent = ResumeTailoringAgent()
        claim_agent = ClaimValidationAgent()
        progress.started(
            "resume_tailoring",
            "Building the tailored resume narrative…",
            agent_id="resume_tailoring",
        )
        while True:
            try:
                # Agent 6 — content selection (never invents facts / wording polish)
                knowledge.resume_facts = resume_facts
                strategy_obj.legacy_strategy = strategy
                tailor_result = tailoring_agent.run(
                    TailoringAgentInput(
                        knowledge=knowledge,
                        job_profile=job_profile_obj,
                        company_profile=company_profile_obj,
                        evidence_map=evidence_map_obj,
                        strategy=strategy_obj,
                        ranked_requirements=ranked,
                        inferred=inferred,
                        triage=triage,
                        rebuilt_resume=rebuilt,
                        content_scores=content_scores,
                        language=output_language,
                        regeneration_attempt=regeneration_attempt,
                    ),
                    AgentContext(use_cache=use_cache, language=output_language),
                )
                structure = tailor_result.output
                generated = dict(structure.raw_generation or {})
                generated["tailored_resume"] = structure.as_resume_dict()
                generated["matched_requirements"] = structure.matched_requirements
                generated["missing_requirements"] = structure.missing_requirements
                generated["change_log"] = structure.change_log
                generated["ats_keywords_added"] = structure.ats_keywords_added
                generated["removed_or_deprioritized_content"] = (
                    structure.removed_or_deprioritized_content
                )
            except SchemaValidationError:
                # Controlled regen failed — keep the last valid rewrite if any.
                if previous_generated is not None and regeneration_attempt > 0:
                    logger.warning(
                        "intelligent_tailoring: regen rewrite failed; keeping prior generation"
                    )
                    generated = previous_generated
                    break
                raise
            previous_generated = generated
            validation_depth = validate_tailoring_depth(
                tailored_resume=generated["tailored_resume"],
                baseline_resume=baseline_resume,
                strategy=strategy,
            )
            # Depth/quality regen decisions use the raw rewrite result (pre-claim),
            # matching prior pipeline behaviour and avoiding regen loops caused solely
            # by claim stripping of unsupported phrases.
            preclaim_quality = evaluate_tailoring_quality(
                tailored_resume=generated["tailored_resume"],
                baseline_resume=baseline_resume,
                strategy=strategy,
                evidence_map=evidence_map,
                missed_evidence=missed,
                fact_scores=fact_scores,
                unsupported_claim_count=0,
                change_log=generated.get("change_log") or [],
            )
            needs_depth = should_regenerate(validation_depth, regeneration_attempt)
            needs_quality = should_regenerate_for_quality(
                preclaim_quality, regeneration_attempt
            )
            if needs_depth or needs_quality:
                if regeneration_attempt >= max_gate_attempts:
                    # Fall through to claim/scope validation of the best attempt
                    pass
                else:
                    regeneration_attempt += 1
                    logger.info(
                        "intelligent_tailoring: regenerating pre-claim (attempt %d) "
                        "depth=%s quality=%s warnings=%s",
                        regeneration_attempt,
                        needs_depth,
                        needs_quality,
                        preclaim_quality.get("warnings"),
                    )
                    strategy = enrich_strategy_with_missed_evidence(strategy, missed)
                    continue

            # Agent 7 — Claim validation (sentence-level)
            progress.completed(
                "resume_tailoring",
                "Tailored structure drafted from evidenced content.",
                agent_id="resume_tailoring",
            )
            progress.started(
                "claim_validation",
                "Validating every claim against evidence…",
                agent_id="claim_validation",
            )
            claim_result = claim_agent.run(
                ClaimValidationInput(
                    original_resume_text=resume_text,
                    tailored_resume=generated["tailored_resume"],
                    evidence_map=evidence_map_obj,
                    change_log=generated.get("change_log") or [],
                    inferred=inferred,
                    job_profile=job_profile_obj,
                ),
                AgentContext(
                    use_cache=use_cache,
                    language=output_language,
                    metadata={"rejected_claims": rejected_claims},
                ),
            )
            validation = {
                "cleaned_resume": claim_result.output.cleaned_resume,
                "warnings": claim_result.output.warnings,
                "rejected_statements": claim_result.output.rejected_statements,
                "inferred_competencies": claim_result.output.inferred_competencies,
                "passed": claim_result.output.passed,
                "decisions": [d.to_dict() for d in claim_result.output.decisions],
            }
            cleaned_resume = validation["cleaned_resume"]
            cleaned_resume["skills"] = dedupe_skills(cleaned_resume.get("skills") or [])

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

            scope_result = validate_resume_tech_scope(
                cleaned_resume,
                facts=[f.to_dict() for f in kb.facts],
                original_roles=list(resume_facts.get("experience_roles") or []),
                original_projects=list(resume_facts.get("projects") or []),
            )
            cleaned_resume = scope_result["cleaned_resume"]
            cleaned_resume["summary"] = str(
                cleaned_resume.get("professional_summary")
                or cleaned_resume.get("summary")
                or ""
            )
            cleaned_resume["professional_summary"] = cleaned_resume["summary"]

            if scope_result.get("violations"):
                for v in scope_result["violations"]:
                    validation.setdefault("warnings", []).append(
                        {
                            "statement": v.get("text") or "",
                            "reason": v.get("reason") or "scope_violation",
                            "inference_category": "Unsupported",
                        }
                    )
                    rejected = validation.setdefault("rejected_statements", [])
                    if v.get("text") and v["text"] not in rejected:
                        rejected.append(v["text"])

            from intelligent_tailoring.summary_builder import build_professional_summary
            from intelligent_tailoring.linguistic_integrity import (
                validate_resume_linguistics,
            )
            from intelligent_tailoring.skill_taxonomy import normalize_skill_lines

            # Structured summary — never keep corrupted keyword-soup text
            summary_result = build_professional_summary(
                strategy=strategy,
                resume_facts=resume_facts,
                resume_text=resume_text,
                output_language=output_language,
                existing_summary=str(
                    cleaned_resume.get("professional_summary")
                    or cleaned_resume.get("summary")
                    or ""
                ),
            )
            if summary_result.get("summary"):
                cleaned_resume["professional_summary"] = summary_result["summary"]
                cleaned_resume["summary"] = summary_result["summary"]
            else:
                cleaned_resume["professional_summary"] = ""
                cleaned_resume["summary"] = ""

            # Deterministic skill categories (override LLM / family heuristics)
            cleaned_resume["skills"] = normalize_skill_lines(
                list(cleaned_resume.get("skills") or []),
                emphasize=list(
                    strategy.get("propagate_terms")
                    or strategy.get("skills_to_emphasize")
                    or []
                ),
                job_family=str(strategy.get("job_family") or ""),
                category_order=list(strategy.get("skill_category_order") or []),
            )

            linguistic = validate_resume_linguistics(cleaned_resume)
            if linguistic.get("regeneration_required"):
                validation.setdefault("warnings", []).append(
                    {
                        "statement": ",".join(linguistic.get("invalid_claim_ids") or [])[:200],
                        "reason": "linguistic_integrity:"
                        + ",".join((linguistic.get("detected_patterns") or [])[:6]),
                        "inference_category": "Unsupported",
                    }
                )

            deterministic_log = build_deterministic_change_log(
                baseline_resume=baseline_resume,
                final_resume=cleaned_resume,
                evidence_map=evidence_map,
            )
            generated["change_log"] = deterministic_log

            unsupported_count = len(validation.get("rejected_statements") or [])
            rejected_claims.extend(
                validation.get("rejected_statements") or [],
                reason="claim_validation",
                source_agent="claim_validation",
            )
            # Truthfulness uses the real unsupported count; regen is still gated
            # separately so cleaned-away claims do not force rewrite loops.
            quality_report = evaluate_tailoring_quality(
                tailored_resume=cleaned_resume,
                baseline_resume=baseline_resume,
                strategy=strategy,
                evidence_map=evidence_map,
                missed_evidence=missed,
                fact_scores=fact_scores,
                unsupported_claim_count=unsupported_count,
                change_log=deterministic_log,
            )
            # Do not regenerate solely because unsupported claims were stripped
            quality_report["regeneration_required"] = False
            quality_gates = evaluate_quality_gates(
                tailored_resume=cleaned_resume,
                original_resume_text=resume_text,
                facts=[f.to_dict() for f in kb.facts],
                change_log=deterministic_log,
                original_roles=list(resume_facts.get("experience_roles") or []),
                original_projects=list(resume_facts.get("projects") or []),
                require_summary=True,
                rejected_statements=validation.get("rejected_statements") or [],
            )
            quality_gates["linguistic_integrity"] = linguistic
            if not linguistic.get("passed"):
                quality_gates["passed"] = False
                for pattern in linguistic.get("detected_patterns") or []:
                    failure = f"linguistic_integrity:{pattern}"
                    if failure not in quality_gates.setdefault("failures", []):
                        quality_gates["failures"].append(failure)
            # Record rejected count for reporting without driving regen loops
            quality_report["unsupported_claim_count"] = unsupported_count
            if unsupported_count:
                quality_report.setdefault("warnings", []).append(
                    f"{unsupported_count} unsupported claims stripped during validation"
                )

            hard_gate_failures = [
                f
                for f in (quality_gates.get("failures") or [])
                if any(
                    str(f).startswith(prefix)
                    for prefix in (
                        "unsupported_impact",
                        "unsupported_entity",
                        "cross_entry_tech",
                        "unknown_skill",
                        "missing_professional_summary",
                        "raw_llm_reasoning",
                        "linguistic_integrity",
                    )
                )
            ]
            needs_gates = (
                bool(hard_gate_failures) and regeneration_attempt < max_gate_attempts
            )
            if not needs_gates:
                break
            regeneration_attempt += 1
            logger.info(
                "intelligent_tailoring: regenerating after gates (attempt %d) "
                "hard_failures=%s",
                regeneration_attempt,
                hard_gate_failures[:5],
            )
            strategy = enrich_strategy_with_missed_evidence(strategy, missed)

        if regenerate_section:
            generated["regenerate_section"] = regenerate_section

        # Soft-fail critical gates at generation time — preserve resume for preview.
        # Download/export still blocked via assert_safe_to_export.
        quality_gates = classify_quality_gates(quality_gates)
        hard_failures = list(quality_gates.get("critical_failures") or [])
        if hard_failures:
            logger.warning(
                "intelligent_tailoring: critical gates after regen (preview allowed): %s",
                hard_failures[:8],
            )
            quality_gates["preview_allowed"] = True
            quality_gates["review_mode"] = True
            quality_gates["download_blocked"] = True

        claim_passed = (
            bool(quality_gates.get("passed_critical", quality_gates.get("passed")))
            and not any(
                str(w.get("inference_category") or "") == "Unsupported"
                and "still present" in str(w.get("reason") or "").lower()
                for w in (validation.get("warnings") or [])
                if isinstance(w, dict)
            )
        )

        # ---- Merged Agent 3: Human Writing & Credibility Review ----
        progress.completed(
            "claim_validation",
            "Claim validation complete — only evidenced statements remain.",
            agent_id="claim_validation",
        )
        progress.started(
            "human_writing_credibility",
            "Writing and validating your resume…",
            agent_id="human_writing_credibility",
        )
        strategy_obj.legacy_strategy = strategy
        evidence_compact = json.dumps(
            [
                {
                    "requirement": e.get("requirement"),
                    "status": e.get("candidate_status"),
                    "strength": e.get("evidence_strength"),
                    "evidence": str(e.get("supporting_evidence") or "")[:120],
                }
                for e in (evidence_map or [])[:40]
            ],
            ensure_ascii=False,
        )
        writing_stage = run_merged_writing_review(
            validated_resume=cleaned_resume,
            strategy=strategy,
            knowledge_base=kb,
            output_language=output_language,
            use_cache=use_cache,
            allow_llm=True,
            rejected_claims=list(validation.get("rejected_statements") or []),
            evidence_compact=evidence_compact,
            highlight_plan=strategy.get("highlight_plan"),
            evidence_inventory=strategy.get("evidence_inventory"),
            max_repair_passes=2,
        )
        polished = writing_stage.get("tailored_resume") or cleaned_resume
        if not writing_stage.get("facts_unchanged", True):
            logger.warning(
                "human_writing: fact lock failed — keeping claim-validated resume"
            )
            polished = cleaned_resume
        cleaned_resume = polished
        cleaned_resume["summary"] = str(
            cleaned_resume.get("professional_summary")
            or cleaned_resume.get("summary")
            or ""
        )
        cleaned_resume["professional_summary"] = cleaned_resume["summary"]

        # Build recruiter review from merged Agent 3 output + deterministic scans
        # (no additional LLM call — review was composed into Agent 3).
        from intelligent_tailoring.agents.schemas import RecruiterReviewOutput
        from intelligent_tailoring.services.senior_recruiter_review import review_resume
        from intelligent_tailoring.writing.ai_detector import detect_ai_writing
        from intelligent_tailoring.writing.style_validator import evaluate_writing_quality

        recruiter_dict = dict(writing_stage.get("recruiter_review") or {})
        if not recruiter_dict:
            recruiter_dict = review_resume(
                resume=cleaned_resume,
                output_language=output_language,
                use_cache=use_cache,
                allow_llm=False,
            )
        style_scan = evaluate_writing_quality(cleaned_resume)
        ai_scan = detect_ai_writing(cleaned_resume)
        interview_quality = int(
            recruiter_dict.get("interview_quality")
            or style_scan.get("overall_score")
            or 0
        )
        human = int(
            recruiter_dict.get("human_believability") or ai_scan.get("human_score") or 0
        )
        approved = bool(recruiter_dict.get("approved", True))
        if "would_interview" in recruiter_dict:
            would_interview = bool(recruiter_dict.get("would_interview"))
        else:
            would_interview = approved or (interview_quality >= 70 and human >= 65)
        sections = list(recruiter_dict.get("sections_to_regenerate") or [])
        recruiter_review_obj = RecruiterReviewOutput(
            would_interview=would_interview,
            communicates_value=interview_quality >= 65,
            sounds_robotic=(not bool(ai_scan.get("passed", True))) or human < 65,
            bullets_concise=int(
                style_scan.get("dimensions", {}).get("conciseness") or 70
            )
            >= 65,
            achievements_clear=int(
                style_scan.get("dimensions", {}).get("scanning")
                or style_scan.get("dimensions", {}).get("readability")
                or 70
            )
            >= 65,
            sections_to_strengthen=list(
                recruiter_dict.get("sections_to_strengthen") or sections
            ),
            approved=approved,
            human_believability=human,
            interview_quality=interview_quality,
            issues=list(recruiter_dict.get("issues") or []),
            summary_feedback=str(recruiter_dict.get("summary_feedback") or ""),
            sections_to_regenerate=sections,
            raw_review=dict(recruiter_dict),
            interview_recommendation=(
                "interview"
                if would_interview and approved
                else ("maybe_interview" if would_interview else "do_not_interview")
            ),
            weak_sections=list(sections),
        )
        recruiter_dict = recruiter_review_obj.to_dict()
        agent_trace.append(
            {
                "agent_id": "human_writing_credibility",
                "metrics": {
                    "mode": writing_stage.get("mode"),
                    "repair_passes": writing_stage.get("repair_passes"),
                    "primary_llm_calls": writing_stage.get("primary_llm_calls"),
                },
            }
        )
        progress.completed(
            "human_writing_credibility",
            (
                "Writing validated — recruiter would interview."
                if getattr(recruiter_review_obj, "would_interview", False)
                or getattr(recruiter_review_obj, "approved", False)
                else "Writing complete — review mode flags remain."
            ),
            agent_id="human_writing_credibility",
        )

        progress.started(
            "hiring_manager",
            "Challenging role fit as a hiring manager…",
            agent_id="hiring_manager_simulation",
        )
        hm_result = HiringManagerSimulationAgent().run(
            HiringManagerInput(
                resume=cleaned_resume,
                job_profile=job_profile_obj,
                company_profile=company_profile_obj,
                evidence_map=evidence_map_obj,
                strategy=strategy_obj,
            ),
            AgentContext(use_cache=use_cache, language=output_language),
        )
        hiring_manager_obj = hm_result.output
        agent_trace.append(
            {"agent_id": hm_result.agent_id, "metrics": hm_result.metrics}
        )
        for tip in list(hiring_manager_obj.actionable_feedback or [])[:3]:
            progress.decision(
                "hiring_manager",
                {
                    "action": "challenge",
                    "text": str(tip)[:180],
                    "target": "resume",
                    "reason": "hiring manager challenge",
                },
            )
        progress.completed(
            "hiring_manager",
            "Hiring manager review complete.",
            agent_id="hiring_manager_simulation",
        )

        # Agent 4 may request at most ONE targeted Agent 3 section repair
        hm_dict = hiring_manager_obj.to_dict()
        quality_score = writing_stage.get("quality_score") or {}
        quality_dims = dict(quality_score.get("dimensions") or {})
        needs_hm_refine = (
            int(hm_dict.get("overall_fit") or 0) < 70
            or int(quality_score.get("overall_score") or 100) < 74
            or int(quality_dims.get("interview_probability") or 100) < 70
            or int(quality_dims.get("twenty_second_screen") or 100) < 70
            or bool(hm_dict.get("weakest_sections"))
        ) and (
            not recruiter_review_obj.approved
            or int(hm_dict.get("overall_fit") or 0) < 75
            or int(quality_score.get("overall_score") or 100) < 74
        )
        if needs_hm_refine and rejected_claims.begin_revision("hiring_manager_review"):
            weak_sections = list(hm_dict.get("weakest_sections") or [])[:3]
            logger.info(
                "intelligent_tailoring: Agent4→Agent3 targeted refine sections=%s",
                weak_sections,
            )
            from intelligent_tailoring.services.human_resume_writer import (
                write_human_resume,
            )

            refine_stage = write_human_resume(
                validated_resume=rejected_claims.scrub_resume(cleaned_resume),
                strategy=strategy,
                knowledge_base=kb,
                output_language=output_language,
                hiring_manager_feedback=hm_dict,
                sections=weak_sections or None,
                use_cache=False,
                allow_llm=True,
            )
            if refine_stage.get("facts_unchanged", True) and refine_stage.get(
                "tailored_resume"
            ):
                cleaned_resume = rejected_claims.scrub_resume(
                    refine_stage["tailored_resume"]
                )
                cleaned_resume["summary"] = str(
                    cleaned_resume.get("professional_summary")
                    or cleaned_resume.get("summary")
                    or ""
                )
                cleaned_resume["professional_summary"] = cleaned_resume["summary"]
                writing_stage = {
                    **writing_stage,
                    "hm_refine_pass": True,
                    "prior_quality_score": quality_score,
                    "targeted_retry": True,
                }
                # Re-score HM after refine (deterministic; no new primary LLM agent)
                hm_result = HiringManagerSimulationAgent().run(
                    HiringManagerInput(
                        resume=cleaned_resume,
                        job_profile=job_profile_obj,
                        company_profile=company_profile_obj,
                        evidence_map=evidence_map_obj,
                        strategy=strategy_obj,
                    ),
                    AgentContext(use_cache=False, language=output_language),
                )
                hiring_manager_obj = hm_result.output
                agent_trace.append(
                    {
                        "agent_id": hm_result.agent_id,
                        "metrics": {
                            **hm_result.metrics,
                            "pass": "post_refine_targeted",
                        },
                    }
                )

        # --- Premium polish: weave evidenced tech, then enforce one page ---
        from intelligent_tailoring.services.tech_weaver import weave_resume_technologies
        from intelligent_tailoring.services.one_page_compressor import (
            compress_until_likely_fit,
            compress_resume_to_one_page,
            estimate_page_pressure,
        )
        from intelligent_tailoring.services.page_count import (
            allow_multi_page_requested,
            assert_one_page,
        )
        from intelligent_tailoring.skill_taxonomy import normalize_skill_lines

        progress.started(
            "final_polish",
            "Preparing the final one-page resume…",
            agent_id="final_polish",
        )
        prior_quality_gates = dict(quality_gates or {})
        allow_multi = allow_multi_page_requested(job, cv_profile)
        cleaned_resume = weave_resume_technologies(cleaned_resume)
        one_page_meta: dict[str, Any] = {"enabled": not allow_multi, "ok": True}
        if not allow_multi:
            cleaned_resume = compress_until_likely_fit(
                cleaned_resume, strategy=strategy
            )
            ok_page, page_reason = assert_one_page(
                resume=cleaned_resume, allow_multi_page=False
            )
            if not ok_page:
                cleaned_resume = compress_resume_to_one_page(
                    cleaned_resume, strategy=strategy, aggressive=True
                )
                ok_page, page_reason = assert_one_page(
                    resume=cleaned_resume, allow_multi_page=False
                )
            one_page_meta = {
                "enabled": True,
                "ok": ok_page,
                "reason": page_reason,
                "estimate": estimate_page_pressure(cleaned_resume),
                "compressed": True,
            }
            cleaned_resume.pop("_one_page", None)
        cleaned_resume["summary"] = str(
            cleaned_resume.get("professional_summary")
            or cleaned_resume.get("summary")
            or ""
        )
        cleaned_resume["professional_summary"] = cleaned_resume["summary"]
        cleaned_resume["skills"] = normalize_skill_lines(
            list(cleaned_resume.get("skills") or []),
            emphasize=list(
                strategy.get("propagate_terms")
                or strategy.get("skills_to_emphasize")
                or []
            ),
            job_family=str(strategy.get("job_family") or ""),
            category_order=list(strategy.get("skill_category_order") or []),
        )

        # Preservation-first repair: refill empty shells / underfilled sections
        # from verified source facts before final gates and render.
        from intelligent_tailoring.canonical_resume import (
            build_source_coverage_report,
            completeness_failures as canonical_completeness_failures,
            drop_empty_shell_entries,
            estimate_content_density,
            inventory_from_facts,
            log_stage_inventory,
            restore_missing_content_from_source,
        )

        generation_id = str(
            (cv_profile or {}).get("id")
            or job.get("id")
            or "gen"
        )
        source_inv = inventory_from_facts(resume_facts)
        log_stage_inventory(
            generation_id=str(generation_id),
            stage="pre_preserve_repair",
            resume=cleaned_resume,
            extra={"source": source_inv},
        )
        cleaned_resume = restore_missing_content_from_source(
            cleaned_resume,
            resume_facts=resume_facts,
        )
        density = estimate_content_density(cleaned_resume)
        if density.get("underfilled"):
            # Pull additional high-value source content rather than leaving
            # half a page empty.
            cleaned_resume = restore_missing_content_from_source(
                cleaned_resume,
                resume_facts=resume_facts,
                max_roles=3,
                max_projects=2,
                min_bullets_per_role=2,
                min_bullets_per_project=2,
            )
            # Re-fit to one page without stripping restored substance to shells
            if not allow_multi:
                cleaned_resume = compress_resume_to_one_page(
                    cleaned_resume, strategy=strategy, aggressive=False
                )
                cleaned_resume = restore_missing_content_from_source(
                    cleaned_resume,
                    resume_facts=resume_facts,
                    min_bullets_per_role=1,
                    min_bullets_per_project=1,
                )
        cleaned_resume = drop_empty_shell_entries(cleaned_resume)
        cleaned_resume["skills"] = normalize_skill_lines(
            list(cleaned_resume.get("skills") or []),
            emphasize=list(
                strategy.get("propagate_terms")
                or strategy.get("skills_to_emphasize")
                or []
            ),
            job_family=str(strategy.get("job_family") or ""),
            category_order=list(strategy.get("skill_category_order") or []),
        )
        cleaned_resume["summary"] = str(
            cleaned_resume.get("professional_summary")
            or cleaned_resume.get("summary")
            or ""
        )
        cleaned_resume["professional_summary"] = cleaned_resume["summary"]
        coverage_report = build_source_coverage_report(
            source_facts=list(kb.facts) if kb is not None else [],
            tailored_resume=cleaned_resume,
            omission_decisions=list(strategy.get("omission_decisions") or []),
        )
        strategy["source_coverage_report"] = coverage_report
        log_stage_inventory(
            generation_id=str(generation_id),
            stage="post_preserve_repair",
            resume=cleaned_resume,
            warnings=canonical_completeness_failures(
                cleaned_resume,
                source_inventory=source_inv,
                coverage=coverage_report,
            ),
            extra={"density": density, "coverage_score": coverage_report.get("coverage_score")},
        )

        # Final 20-second interview simulation after compress/weave
        from intelligent_tailoring.writing.resume_quality_score import (
            evaluate_resume_quality,
        )

        post_polish_quality = evaluate_resume_quality(
            cleaned_resume,
            strategy=strategy,
            highlight_plan=strategy.get("highlight_plan"),
            evidence_inventory=strategy.get("evidence_inventory"),
            recruiter_review=recruiter_review_obj.to_dict(),
            hiring_manager=hiring_manager_obj.to_dict(),
            threshold=74,
        )
        writing_stage["quality_score"] = post_polish_quality
        writing_stage["post_polish_quality"] = True
        post_dims = dict(post_polish_quality.get("dimensions") or {})
        interview_prob = int(post_dims.get("interview_probability") or 0)
        screen_20s = int(post_dims.get("twenty_second_screen") or 0)
        if (
            interview_prob < 70
            or screen_20s < 70
            or not post_polish_quality.get("passed")
        ) and post_polish_quality.get("weak_sections"):
            progress.decision(
                "final_polish",
                {
                    "action": "rewrite",
                    "text": (
                        "20-second screen / interview probability below bar — "
                        "refining "
                        + ", ".join(post_polish_quality["weak_sections"][:3])
                    ),
                    "target": ",".join(post_polish_quality["weak_sections"][:3]),
                    "reason": "interview_probability",
                },
            )
            # Only one Agent-4-driven revise is allowed; skip if HM refine already ran
            if not writing_stage.get("hm_refine_pass"):
                from intelligent_tailoring.services.human_resume_writer import (
                    write_human_resume as _write_human_resume,
                )

                final_refine = _write_human_resume(
                    validated_resume=cleaned_resume,
                    strategy=strategy,
                    knowledge_base=kb,
                    output_language=output_language,
                    sections=list(post_polish_quality.get("weak_sections") or [])[:3]
                    or None,
                    use_cache=False,
                    allow_llm=True,
                )
                if final_refine.get("facts_unchanged", True) and final_refine.get(
                    "tailored_resume"
                ):
                    cleaned_resume = final_refine["tailored_resume"]
                    if not allow_multi:
                        cleaned_resume = compress_resume_to_one_page(
                            cleaned_resume, strategy=strategy, aggressive=False
                        )
                    cleaned_resume["summary"] = str(
                        cleaned_resume.get("professional_summary")
                        or cleaned_resume.get("summary")
                        or ""
                    )
                    cleaned_resume["professional_summary"] = cleaned_resume["summary"]
                    cleaned_resume = weave_resume_technologies(cleaned_resume)
                    post_polish_quality = evaluate_resume_quality(
                        cleaned_resume,
                        strategy=strategy,
                        highlight_plan=strategy.get("highlight_plan"),
                        evidence_inventory=strategy.get("evidence_inventory"),
                        recruiter_review=recruiter_review_obj.to_dict(),
                        hiring_manager=hiring_manager_obj.to_dict(),
                        threshold=74,
                    )
                    writing_stage["quality_score"] = post_polish_quality
                    writing_stage["post_polish_refine_pass"] = True
                    post_dims = dict(post_polish_quality.get("dimensions") or {})
                    interview_prob = int(post_dims.get("interview_probability") or 0)
                    screen_20s = int(post_dims.get("twenty_second_screen") or 0)

        # Rebuild deterministic change log against the polished wording
        deterministic_log = build_deterministic_change_log(
            baseline_resume=baseline_resume,
            final_resume=cleaned_resume,
            evidence_map=evidence_map,
        )
        generated["change_log"] = deterministic_log

        # Neutralize unsupported impact wording before final gates / export
        from intelligent_tailoring.scope_validator import (
            sanitize_resume_unsupported_impact,
        )

        cleaned_resume, impact_fixes = sanitize_resume_unsupported_impact(
            cleaned_resume, source_text=resume_text
        )
        if impact_fixes:
            progress.decision(
                "final_polish",
                {
                    "action": "rewrite",
                    "text": (
                        f"Neutralized {len(impact_fixes)} unsupported impact "
                        "phrases so the resume stays truthful and exportable"
                    ),
                    "target": "experience,projects",
                    "reason": "unsupported_impact",
                },
            )
            cleaned_resume["summary"] = str(
                cleaned_resume.get("professional_summary")
                or cleaned_resume.get("summary")
                or ""
            )
            cleaned_resume["professional_summary"] = cleaned_resume["summary"]
            deterministic_log = build_deterministic_change_log(
                baseline_resume=baseline_resume,
                final_resume=cleaned_resume,
                evidence_map=evidence_map,
            )
            generated["change_log"] = deterministic_log

        # Scrub any resurrected rejected claims before final summary
        cleaned_resume = rejected_claims.scrub_resume(cleaned_resume)

        # Summary is written LAST from the strongest validated evidence present
        from intelligent_tailoring.summary_builder import build_professional_summary
        from intelligent_tailoring.professional_narrative import (
            evaluate_professional_narrative,
        )

        final_summary = build_professional_summary(
            strategy=strategy,
            resume_facts=resume_facts,
            resume_text=resume_text,
            output_language=output_language,
            existing_summary=str(
                cleaned_resume.get("professional_summary")
                or cleaned_resume.get("summary")
                or ""
            ),
        )
        if final_summary.get("summary"):
            # Never reintroduce a previously rejected summary
            if not rejected_claims.contains(final_summary["summary"]):
                cleaned_resume["professional_summary"] = final_summary["summary"]
                cleaned_resume["summary"] = final_summary["summary"]

        narrative_test = evaluate_professional_narrative(
            cleaned_resume,
            strategy=strategy,
            genuine_gaps=list(
                strategy.get("genuine_gaps")
                or getattr(strategy_obj, "genuine_gaps", [])
                or []
            ),
            top_reasons=list(
                strategy.get("top_reasons_to_interview")
                or strategy.get("top_interview_reasons")
                or getattr(strategy_obj, "top_reasons_to_interview", [])
                or []
            ),
        )
        if (
            not narrative_test.get("passed")
            and narrative_test.get("sections_to_regenerate")
            and rejected_claims.can_revise()
        ):
            # Targeted section regen via writer — summary only when unclear
            rejected_claims.begin_revision("professional_narrative_test")
            narrative_feedback = {
                "sections_to_regenerate": narrative_test["sections_to_regenerate"],
                "summary_feedback": (
                    "Clarify who the candidate is, what they can do, and why "
                    "they are relevant — using only validated evidence."
                ),
                "would_interview": False,
                "approved": False,
            }
            from intelligent_tailoring.services.human_resume_writer import (
                write_human_resume as _write_narrative,
            )

            narrative_refine = _write_narrative(
                validated_resume=cleaned_resume,
                strategy=strategy,
                knowledge_base=kb,
                output_language=output_language,
                review_feedback=narrative_feedback,
                sections=list(narrative_test.get("sections_to_regenerate") or [])[:2]
                or None,
                use_cache=False,
                allow_llm=True,
            )
            if narrative_refine.get("facts_unchanged", True) and narrative_refine.get(
                "tailored_resume"
            ):
                cleaned_resume = rejected_claims.scrub_resume(
                    narrative_refine["tailored_resume"]
                )
            # Rebuild summary again after narrative refine
            final_summary = build_professional_summary(
                strategy=strategy,
                resume_facts=resume_facts,
                resume_text=resume_text,
                output_language=output_language,
                existing_summary=str(
                    cleaned_resume.get("professional_summary")
                    or cleaned_resume.get("summary")
                    or ""
                ),
            )
            if final_summary.get("summary") and not rejected_claims.contains(
                final_summary["summary"]
            ):
                cleaned_resume["professional_summary"] = final_summary["summary"]
                cleaned_resume["summary"] = final_summary["summary"]
            narrative_test = evaluate_professional_narrative(
                cleaned_resume,
                strategy=strategy,
                genuine_gaps=list(strategy.get("genuine_gaps") or []),
                top_reasons=list(
                    strategy.get("top_reasons_to_interview")
                    or strategy.get("top_interview_reasons")
                    or []
                ),
            )

        # Final export gates (includes one-page when required)
        quality_gates = evaluate_quality_gates(
            tailored_resume=cleaned_resume,
            original_resume_text=resume_text,
            facts=[f.to_dict() for f in kb.facts],
            change_log=deterministic_log,
            original_roles=list(resume_facts.get("experience_roles") or []),
            original_projects=list(resume_facts.get("projects") or []),
            require_summary=True,
            rejected_statements=validation.get("rejected_statements") or [],
            require_one_page=not allow_multi,
        )
        if prior_quality_gates.get("linguistic_integrity"):
            quality_gates["linguistic_integrity"] = prior_quality_gates[
                "linguistic_integrity"
            ]
            if not (prior_quality_gates.get("linguistic_integrity") or {}).get("passed", True):
                quality_gates["passed"] = False
                for pattern in (
                    (prior_quality_gates.get("linguistic_integrity") or {}).get(
                        "detected_patterns"
                    )
                    or []
                ):
                    failure = f"linguistic_integrity:{pattern}"
                    if failure not in quality_gates.setdefault("failures", []):
                        quality_gates["failures"].append(failure)
        quality_gates["one_page_enforcement"] = one_page_meta

        quality_gates["writing_quality"] = {
            "passed": bool(writing_stage.get("passed")),
            "export_ready": bool(writing_stage.get("export_ready")),
            "facts_unchanged": bool(writing_stage.get("facts_unchanged")),
            "review_cycles": writing_stage.get("review_cycles"),
            "grammar_score": (writing_stage.get("grammar") or {}).get("score"),
            "style_score": (writing_stage.get("style") or {}).get("overall_score"),
            "ai_risk": (writing_stage.get("ai_detector") or {}).get("ai_risk"),
            "resume_quality_score": (writing_stage.get("quality_score") or {}).get(
                "overall_score"
            ),
            "quality_dimensions": (writing_stage.get("quality_score") or {}).get(
                "dimensions"
            ),
            "interview_probability": interview_prob,
            "twenty_second_screen": screen_20s,
            "failures": list(writing_stage.get("quality_gate_failures") or []),
        }
        quality_gates["interview_simulation"] = {
            "interview_probability": interview_prob,
            "twenty_second_screen": screen_20s,
            "passed": interview_prob >= 65 and screen_20s >= 65,
            "notes": (post_polish_quality.get("notes") or {}).get(
                "twenty_second_screen", []
            )[:4],
        }
        if interview_prob < 55 or screen_20s < 55:
            # Hard-block only when interview signal is critically weak
            failure = f"interview_probability:{interview_prob}"
            if failure not in quality_gates.setdefault("failures", []):
                quality_gates["failures"].append(failure)
            quality_gates["passed"] = False
        elif interview_prob < 70 or screen_20s < 70:
            soft = f"interview_probability_soft:{interview_prob}"
            if soft not in quality_gates.setdefault("failures", []):
                quality_gates["failures"].append(soft)
        if writing_stage.get("quality_gate_failures"):
            for failure in writing_stage["quality_gate_failures"]:
                key = f"writing_quality:{failure}"
                if key not in quality_gates.setdefault("failures", []):
                    quality_gates["failures"].append(key)
            # Hard-block only on factual drift or severe grammar/ATS layout issues
            severe = [
                f
                for f in writing_stage["quality_gate_failures"]
                if str(f).startswith(("facts_changed", "grammar:", "ats:"))
            ]
            if severe:
                quality_gates["passed"] = False

        # Soft-fail writing gates — preserve resume for preview/review mode
        quality_gates = classify_quality_gates(quality_gates)
        writing_hard_failures = list(quality_gates.get("critical_failures") or [])
        if writing_hard_failures:
            logger.warning(
                "intelligent_tailoring: writing/export critical gates "
                "(preview allowed, download blocked): %s",
                writing_hard_failures[:8],
            )
            quality_gates["preview_allowed"] = True
            quality_gates["review_mode"] = True
            quality_gates["download_blocked"] = True

        # --- Stage 11: ATS scoring after writing polish (final validated resume) ---
        from intelligent_tailoring.interview_philosophy import (
            select_top_interview_reasons,
        )

        improved_because = list(
            strategy.get("top_interview_reasons")
            or select_top_interview_reasons(
                highlight_plan=strategy.get("highlight_plan"),
                evidence_map=evidence_map,
                strategy=strategy,
            )
        )
        tailored_scoring = rescore_after_tailoring(
            evidence_map=evidence_map,
            tailored_resume=cleaned_resume,
            original_resume_text=resume_text,
            job_title=str(job.get("title") or ""),
            original_score=original_score,
            improved_because=improved_because,
        )
        tailored_score = int(tailored_scoring["realistic_match_score"])
        score_breakdown = dict(tailored_scoring.get("score_breakdown") or {})

        # FinalScoreBreakdown — always from the final validated resume
        from intelligent_tailoring.agents.schemas import FinalScoreBreakdown

        genuine_gaps_final = list(
            strategy.get("genuine_gaps")
            or getattr(hiring_manager_obj, "genuine_gaps", [])
            or score_breakdown.get("still_missing")
            or []
        )
        unsupported_final = max(
            len(validation.get("rejected_statements") or []),
            rejected_claims.to_dict().get("count", 0),
        )
        # Truthfulness: 100 minus penalty per unsupported claim (floor 0)
        truthfulness = max(0, 100 - unsupported_final * 15)
        # Seniority fit must NOT be inflated by tailoring polish
        hm_seniority = int(getattr(hiring_manager_obj, "seniority_fit", 0) or 0)
        seniority_fit_score = hm_seniority or int(
            score_breakdown.get("seniority_fit") or 0
        )
        # If job expects 3+ years and candidate lacks them, keep the gap visible
        years_gap = any(
            re.search(r"\b(3\+|three\s+years?)\b", str(g), re.I)
            for g in genuine_gaps_final
        )
        if years_gap and seniority_fit_score > 55:
            seniority_fit_score = min(seniority_fit_score, 55)

        writing_quality_score = int(
            (writing_stage.get("quality_score") or {}).get("overall_score")
            or score_breakdown.get("role_relevance")
            or 0
        )
        final_score = FinalScoreBreakdown(
            original_resume_score=float(original_score or 0),
            tailored_resume_score=float(tailored_score),
            score_delta=float(tailored_score) - float(original_score or 0),
            requirement_coverage=float(
                score_breakdown.get("requirements_coverage") or 0
            ),
            evidence_strength=float(score_breakdown.get("evidence_strength") or 0),
            keyword_alignment=float(
                score_breakdown.get("ats_keyword_alignment") or 0
            ),
            seniority_fit=float(seniority_fit_score),
            writing_quality=float(writing_quality_score),
            truthfulness_score=float(truthfulness),
            one_page_passed=bool(one_page_meta.get("ok", True)),
            unsupported_claim_count=int(unsupported_final),
            genuine_gaps=[str(g) for g in genuine_gaps_final[:20]],
        )
        score_breakdown.update(final_score.to_dict())
        score_breakdown["scored_from"] = "final_validated_tailored_resume"
        score_breakdown["professional_narrative"] = narrative_test
        score_breakdown["rejected_claims"] = rejected_claims.to_dict()
        tailored_scoring["score_breakdown"] = score_breakdown
        quality_report["truthfulness_score"] = truthfulness / 100.0
        quality_report["unsupported_claim_count"] = unsupported_final
        quality_report["genuine_gaps"] = list(final_score.genuine_gaps)

        tailoring_report = build_tailoring_report(
            strategy=strategy,
            scores=content_scores,
            validation=validation_depth,
            generated=generated,
            original_score=original_score,
            tailored_score=tailored_score,
            regeneration_attempts=regeneration_attempt,
        )
        tailoring_report["quality"] = quality_report
        tailoring_report["quality_gates"] = quality_gates
        tailoring_report["extraction_coverage"] = (
            kb.coverage.to_dict() if kb.coverage else {}
        )
        tailoring_report["missed_evidence"] = {
            "additional_count": len(missed.get("additional_relevant_facts_found") or []),
            "uncovered": list(missed.get("facts_still_uncovered") or [])[:10],
        }
        if quality_report.get("overall_tailoring_score") is not None:
            tailoring_report["tailoring_score"] = quality_report["overall_tailoring_score"]
            tailoring_report["tailoring_quality"] = (
                "excellent"
                if quality_report["overall_tailoring_score"] >= 80
                else "good"
                if quality_report["overall_tailoring_score"] >= 65
                else "moderate"
                if quality_report["overall_tailoring_score"] >= 50
                else "weak"
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

        logger.info(
            "intelligent_tailoring: stage=assembled facts=%s coverage=%.2f "
            "summary_len=%s projects=%s skills=%s change_log=%s scope_violations=%s "
            "gates_passed=%s rejected=%s",
            len(kb.facts),
            float((kb.coverage.extraction_coverage_score if kb.coverage else 0) or 0),
            len(str(cleaned_resume.get("professional_summary") or "")),
            len(cleaned_resume.get("projects") or []),
            len(cleaned_resume.get("skills") or []),
            len(deterministic_log),
            len(scope_result.get("violations") or []),
            quality_gates.get("passed"),
            len(validation.get("rejected_statements") or []),
        )

        from intelligent_tailoring.services.decision_log import build_decision_log
        from intelligent_tailoring.interview_philosophy import build_generation_report

        decision_log = build_decision_log(
            strategy=strategy,
            evidence_map=evidence_map,
            highlight_plan=strategy.get("highlight_plan"),
            removed=removed,
            change_log=deterministic_log,
            recruiter_review=recruiter_review_obj.to_dict(),
            hiring_manager=hiring_manager_obj.to_dict(),
            one_page=one_page_meta,
            writing_report=writing_stage,
        )
        for item in decision_log[:8]:
            # Already emitted many live; keep final log complete without spamming
            pass
        progress.completed(
            "final_polish",
            "Final resume ready — optimized for interview probability.",
            agent_id="final_polish",
        )

        result_payload = {
            "tailored_resume": cleaned_resume,
            "matched_requirements": matched,
            "missing_requirements": missing,
            "inferred_competencies": validation.get("inferred_competencies")
            or [i.to_dict() for i in inferred],
            "removed_or_deprioritized_content": removed,
            "ats_keywords_added": list(generated.get("ats_keywords_added") or []),
            "change_log": deterministic_log,
            "validation_warnings": validation.get("warnings") or [],
            "original_match_score": original_score,
            "tailored_match_score": tailored_score,
            "score_breakdown": score_breakdown,
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
            "claim_validator_passed": claim_passed,
            "rejected_statements": validation.get("rejected_statements") or [],
            "quality_gates": quality_gates,
            "tailoring_strategy": strategy,
            "content_scores": content_scores,
            "tailoring_report": tailoring_report,
            "tailoring_validation": validation_depth,
            "knowledge_base_summary": {
                "fact_count": len(kb.facts),
                "content_hash": kb.content_hash,
                "parser_version": kb.parser_version,
                "coverage": kb.coverage.to_dict() if kb.coverage else {},
                "source_language": kb.source_language,
            },
            "missed_evidence_report": missed,
            "quality_report": quality_report,
            "extraction_coverage": kb.coverage.to_dict() if kb.coverage else {},
            "writing_report": {
                "stage": "human_resume_writer",
                "passed": bool(writing_stage.get("passed")),
                "export_ready": bool(writing_stage.get("export_ready")),
                "facts_unchanged": bool(writing_stage.get("facts_unchanged")),
                "review_cycles": writing_stage.get("review_cycles"),
                "writer_mode": (writing_stage.get("writer") or {}).get("mode"),
                "grammar": writing_stage.get("grammar"),
                "style": {
                    "passed": (writing_stage.get("style") or {}).get("passed"),
                    "overall_score": (writing_stage.get("style") or {}).get(
                        "overall_score"
                    ),
                    "dimensions": (writing_stage.get("style") or {}).get("dimensions"),
                    "weak_dimensions": (writing_stage.get("style") or {}).get(
                        "weak_dimensions"
                    ),
                },
                "ai_detector": writing_stage.get("ai_detector"),
                "ats_validation": writing_stage.get("ats_validation"),
                "quality_score": writing_stage.get("quality_score"),
                "hm_refine_pass": bool(writing_stage.get("hm_refine_pass")),
                "quality_gate_failures": writing_stage.get("quality_gate_failures")
                or [],
            },
            "resume_quality_score": writing_stage.get("quality_score"),
            "job_profile": {
                k: v
                for k, v in job_profile_obj.to_dict().items()
                if k not in ("jd_text", "raw_requirements")
            },
            "company_profile": company_profile_obj.to_dict(),
            "resume_strategy": {
                k: v
                for k, v in strategy_obj.to_dict().items()
                if k != "legacy_strategy"
            },
            "recruiter_review": recruiter_review_obj.to_dict(),
            "hiring_manager_feedback": hiring_manager_obj.to_dict(),
            "claim_decisions": validation.get("decisions") or [],
            "agent_trace": agent_trace,
            "agent_timings_ms": agent_timings_ms,
            "architecture": "four_agent_v2_0",
            "one_page": one_page_meta,
            "decision_log": decision_log,
            "top_interview_reasons": list(
                strategy.get("top_interview_reasons") or []
            ),
        }
        result_payload["generation_report"] = build_generation_report(
            result=result_payload
        )

        # Strict schema validation of the assembled result
        validated = validate_tailoring_result(result_payload)
        result_payload.update(validated.to_dict())
        # Restore non-schema audit fields stripped by to_dict
        result_payload["jd_snapshot"] = jd_snapshot
        result_payload["jd_snapshot_hash"] = content_hash(jd_snapshot)
        result_payload["resume_hash"] = content_hash(resume_text)
        result_payload["claim_validator_passed"] = claim_passed
        result_payload["rejected_statements"] = validation.get("rejected_statements") or []
        result_payload["quality_gates"] = quality_gates
        result_payload["pipeline_version"] = PIPELINE_VERSION
        result_payload["tailoring_strategy"] = strategy
        result_payload["tailoring_report"] = tailoring_report
        result_payload["tailoring_validation"] = validation_depth
        result_payload["quality_report"] = quality_report
        result_payload["missed_evidence_report"] = missed
        result_payload["extraction_coverage"] = (
            kb.coverage.to_dict() if kb.coverage else {}
        )
        result_payload["knowledge_base_summary"] = result_payload.get(
            "knowledge_base_summary"
        )
        result_payload["writing_report"] = result_payload.get("writing_report")
        # Preserve multi-agent audit fields after schema round-trip
        result_payload["job_profile"] = {
            k: v
            for k, v in job_profile_obj.to_dict().items()
            if k not in ("jd_text", "raw_requirements")
        }
        result_payload["company_profile"] = company_profile_obj.to_dict()
        result_payload["resume_strategy"] = {
            k: v
            for k, v in strategy_obj.to_dict().items()
            if k != "legacy_strategy"
        }
        result_payload["recruiter_review"] = recruiter_review_obj.to_dict()
        result_payload["hiring_manager_feedback"] = hiring_manager_obj.to_dict()
        result_payload["claim_decisions"] = validation.get("decisions") or []
        result_payload["agent_trace"] = agent_trace
        result_payload["agent_timings_ms"] = agent_timings_ms
        result_payload["architecture"] = "four_agent_v2_0"
        result_payload["one_page"] = one_page_meta
        result_payload["decision_log"] = decision_log
        result_payload["top_interview_reasons"] = list(
            strategy.get("top_interview_reasons") or []
        )
        result_payload["score_breakdown"] = score_breakdown
        result_payload["generation_report"] = build_generation_report(
            result=result_payload
        )
        result_payload["resume_quality_score"] = writing_stage.get("quality_score")
        result_payload["writing_report"] = result_payload.get("writing_report")
        # Preserve structured change_log fields after schema round-trip
        result_payload["change_log"] = deterministic_log

        llm_metrics = get_llm_metrics()
        result_payload["pipeline_metrics"] = {
            **llm_metrics,
            "duration_ms": int((_time.perf_counter() - pipeline_started) * 1000),
            "merged_agents": 4,
            "knowledge_cache_hit": knowledge_cache_hit,
            "job_cache_hit": bool(locals().get("job_cache_hit")),
            "primary_llm_call_cap": 4,
        }
        result_payload["quality_gates"] = classify_quality_gates(
            result_payload.get("quality_gates") or quality_gates
        )

        attach_quality_intelligence(
            result=result_payload,
            job_profile=job_profile_obj,
            recruiter=recruiter_review_obj,
            hiring_manager=hiring_manager_obj,
            strategy=strategy_obj,
            agent_timings_ms=agent_timings_ms,
        )

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
            resume_text=f"{kb.content_hash}|{resume_text}",
            jd_text=jd_snapshot,
            language=output_language,
        )

    return legacy


def _inject_missed_facts(
    resume_facts: dict[str, Any],
    kb: Any,
    missed: dict[str, Any],
) -> dict[str, Any]:
    """Ensure overlooked KB facts appear in experience/projects for generation."""
    extra = missed.get("additional_relevant_facts_found") or []
    if not extra:
        return resume_facts
    facts = dict(resume_facts)
    roles = [dict(r) for r in (facts.get("experience_roles") or []) if isinstance(r, dict)]
    projects = [dict(p) for p in (facts.get("projects") or []) if isinstance(p, dict)]

    existing_bullets = set()
    for r in roles:
        for b in r.get("bullets") or []:
            existing_bullets.add(str(b).strip().lower())
    for p in projects:
        for b in p.get("bullets") or []:
            existing_bullets.add(str(b).strip().lower())

    for item in extra:
        text = str(item.get("text") or "").strip()
        if not text or text.lower() in existing_bullets:
            continue
        section = str(item.get("source_section") or "")
        entry_id = str(item.get("source_entry_id") or "")
        fact = kb.fact_by_id(str(item.get("fact_id") or ""))
        if fact and not entry_id:
            entry_id = str(getattr(fact, "source_entry_id", "") or "")
            section = section or str(getattr(fact, "source_section", "") or "")

        placed = False
        if entry_id.startswith("project_") or section == "projects":
            # Prefer exact source_entry_id index
            idx = None
            if entry_id.startswith("project_"):
                try:
                    idx = int(entry_id.split("_", 1)[1])
                except (TypeError, ValueError, IndexError):
                    idx = None
            if idx is not None and 0 <= idx < len(projects):
                projects[idx].setdefault("bullets", []).append(text)
                placed = True
            elif projects and fact and getattr(fact, "organization", None):
                for p in projects:
                    if str(p.get("name") or "") == fact.organization:
                        p.setdefault("bullets", []).append(text)
                        placed = True
                        break
        if not placed and (entry_id.startswith("role_") or section in ("experience", "roles")):
            idx = None
            if entry_id.startswith("role_"):
                try:
                    idx = int(entry_id.split("_", 1)[1])
                except (TypeError, ValueError, IndexError):
                    idx = None
            if idx is not None and 0 <= idx < len(roles):
                roles[idx].setdefault("bullets", []).append(text)
                placed = True
            elif roles and fact and getattr(fact, "organization", None):
                for r in roles:
                    if str(r.get("company") or "") == fact.organization:
                        r.setdefault("bullets", []).append(text)
                        placed = True
                        break
        if not placed:
            # Do not dump into projects[0]/roles[0] — skip rather than leak across entries
            logger.info(
                "intelligent_tailoring: skipped missed fact without matching entry id=%s",
                entry_id or item.get("fact_id"),
            )
            continue
        existing_bullets.add(text.lower())

    facts["experience_roles"] = roles
    facts["projects"] = projects
    return facts


def _safe_summary_from_strategy(
    *,
    strategy: dict[str, Any],
    resume_facts: dict[str, Any],
    resume_text: str,
) -> str:
    """Build a short factual summary from evidenced skills — never invent impact."""
    from intelligent_tailoring.scope_validator import has_unsupported_impact

    emphasize = [
        str(s).strip()
        for s in (strategy.get("skills_to_emphasize") or [])[:6]
        if str(s).strip()
    ]
    if not emphasize:
        skills = resume_facts.get("skills") or {}
        if isinstance(skills, dict):
            for key in ("frameworks", "languages", "cloud", "other"):
                emphasize.extend(str(x) for x in (skills.get(key) or [])[:2])
        elif isinstance(skills, list):
            emphasize = [str(s) for s in skills[:5]]
    source_l = resume_text.lower()
    evidenced = [s for s in emphasize if s.lower() in source_l]
    if not evidenced:
        # Fall back to any short skill tokens present in the resume facts list
        skills = resume_facts.get("skills") or []
        if isinstance(skills, list):
            evidenced = [str(s) for s in skills[:4] if str(s).strip()]
        elif isinstance(skills, dict):
            for key in ("frameworks", "languages", "cloud", "other"):
                evidenced.extend(str(x) for x in (skills.get(key) or [])[:2])
            evidenced = evidenced[:5]
    evidenced = evidenced[:5]
    if not evidenced:
        return ""
    title = str(
        strategy.get("target_title")
        or strategy.get("honest_title")
        or strategy.get("job_family")
        or ""
    ).strip()
    # Avoid starting with a Capitalized adjective that entity checks may flag
    if title:
        summary = f"Candidate for {title} roles with experience in {', '.join(evidenced)}."
    else:
        summary = f"Candidate with experience in {', '.join(evidenced)}."
    if has_unsupported_impact(summary, resume_text):
        return ""
    return summary


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
        "claim_validator_passed": not bool(validation.get("rejected_statements")),
        "regenerated_section": section_key,
        "from_cache": False,
    }
    return _ensure_legacy_fields(merged, job=job, cv_profile=cv_profile)
