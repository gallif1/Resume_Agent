"""Multi-agent orchestrator — Resume Intelligence Platform.

Wires specialist agents with structured objects. Public entry remains
``run_intelligent_tailoring`` in ``pipeline.py`` for API compatibility;
that function delegates here.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from intelligent_tailoring.agents.base import AgentContext
from intelligent_tailoring.agents.claim_validation_agent import ClaimValidationAgent
from intelligent_tailoring.agents.company_intelligence_agent import (
    CompanyIntelligenceAgent,
)
from intelligent_tailoring.agents.evidence_mapping_agent import EvidenceMappingAgent
from intelligent_tailoring.agents.hiring_manager_agent import (
    HiringManagerSimulationAgent,
)
from intelligent_tailoring.agents.human_writer_agent import HumanResumeWriterAgent
from intelligent_tailoring.agents.job_intelligence_agent import JobIntelligenceAgent
from intelligent_tailoring.agents.quality_intelligence import (
    build_metrics_from_pipeline,
    record_generation_metrics,
)
from intelligent_tailoring.agents.resume_knowledge_agent import ResumeKnowledgeAgent
from intelligent_tailoring.agents.resume_strategy_agent import ResumeStrategyAgent
from intelligent_tailoring.agents.resume_tailoring_agent import ResumeTailoringAgent
from intelligent_tailoring.agents.schemas import (
    ClaimValidationInput,
    CompanyIntelligenceInput,
    EvidenceMappingInput,
    HiringManagerInput,
    HumanWriterInput,
    JobIntelligenceInput,
    RecruiterReviewInput,
    ResumeKnowledgeInput,
    ResumeStrategyInput,
    TailoringAgentInput,
)
from intelligent_tailoring.agents.senior_recruiter_agent import (
    SeniorRecruiterReviewAgent,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION as PIPELINE_VERSION

logger = logging.getLogger("intelligent_tailoring.orchestrator")

# Avoid circular imports: pipeline imports attach_quality_intelligence from here.

# Single Resume Generation Agent (+ deterministic prepare / final stages).
AGENT_CATALOG: tuple[tuple[str, str], ...] = (
    (
        "prepare_evidence",
        "Parse resume/JD, normalize facts, collect supporting evidence (code)",
    ),
    (
        "resume_generation_agent",
        "ONE LLM call — tailor, write, and self-validate the full resume",
    ),
    (
        "final_hiring_ats_page",
        "Hiring-manager fit, ATS score, one-page enforcement (code)",
    ),
)

# Legacy specialist catalog kept for audits / tests
LEGACY_AGENT_CATALOG: tuple[tuple[str, str], ...] = (
    ("resume_knowledge", "Parse candidate facts into ResumeKnowledgeBase"),
    ("job_intelligence", "Extract structured JobProfile from the JD"),
    ("company_intelligence", "Extract CompanyProfile without fabrication"),
    ("evidence_mapping", "Map requirements to evidence with wording constraints"),
    ("resume_strategy", "Decide strategy before any writing"),
    ("resume_tailoring", "Select content structure from evidence + strategy"),
    ("claim_validation", "Sentence-level claim validation"),
    ("human_resume_writer", "Premium wording-only rewrite"),
    ("senior_recruiter_review", "Structured recruiter feedback"),
    ("hiring_manager_simulation", "Job-specific hiring manager feedback"),
)


def _timed(agent_id: str, timings: dict[str, int], fn: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    try:
        return fn()
    finally:
        timings[agent_id] = int((time.perf_counter() - started) * 1000)


def run_multi_agent_pipeline(
    *,
    cv_profile: dict[str, Any],
    job: dict[str, Any],
    use_cache: bool = True,
    source_documents: str | None = None,
    language: str | None = None,
    regenerate_section: str | None = None,
) -> dict[str, Any]:
    """Run the full multi-agent resume intelligence pipeline.

    Delegates to the production pipeline implementation which now executes
    specialist agents and attaches ``agent_trace`` / hiring-manager feedback.
    """
    # Import locally to avoid circular import at module load
    from intelligent_tailoring.pipeline import run_intelligent_tailoring_agents

    return run_intelligent_tailoring_agents(
        cv_profile=cv_profile,
        job=job,
        use_cache=use_cache,
        source_documents=source_documents,
        language=language,
        regenerate_section=regenerate_section,
    )


def build_agent_instances() -> dict[str, Any]:
    """Factory for independently testable agent instances."""
    return {
        "resume_knowledge": ResumeKnowledgeAgent(),
        "job_intelligence": JobIntelligenceAgent(),
        "company_intelligence": CompanyIntelligenceAgent(),
        "evidence_mapping": EvidenceMappingAgent(),
        "resume_strategy": ResumeStrategyAgent(),
        "resume_tailoring": ResumeTailoringAgent(),
        "claim_validation": ClaimValidationAgent(),
        "human_resume_writer": HumanResumeWriterAgent(),
        "senior_recruiter_review": SeniorRecruiterReviewAgent(),
        "hiring_manager_simulation": HiringManagerSimulationAgent(),
    }


def run_agent_phase_bundle(
    *,
    cv_profile: dict[str, Any],
    job: dict[str, Any],
    source_documents: str | None = None,
    jd_snapshot: str,
    language: str = "en",
    use_cache: bool = True,
    inferred: list[Any] | None = None,
    ranked_requirements: list[dict[str, Any]] | None = None,
    fact_scores: list[dict[str, Any]] | None = None,
    job_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run agents 1–5 and return structured outputs for the deep-tailor loop.

    Used by the production pipeline to keep stage boundaries explicit.
    """
    ctx = AgentContext(use_cache=use_cache, language=language)
    timings: dict[str, int] = {}
    agents = build_agent_instances()
    trace: list[dict[str, Any]] = []

    knowledge_result = _timed(
        "resume_knowledge",
        timings,
        lambda: agents["resume_knowledge"].run(
            ResumeKnowledgeInput(
                cv_profile=cv_profile,
                source_documents=source_documents,
                target_output_language=language,
            ),
            ctx,
        ),
    )
    trace.append(knowledge_result.to_dict())

    job_result = _timed(
        "job_intelligence",
        timings,
        lambda: agents["job_intelligence"].run(
            JobIntelligenceInput(job=job, jd_snapshot=jd_snapshot),
            ctx,
        ),
    )
    trace.append(
        {
            "agent_id": job_result.agent_id,
            "output": {
                k: v
                for k, v in job_result.output.to_dict().items()
                if k not in ("jd_text", "raw_requirements")
            },
            "warnings": job_result.warnings,
            "metrics": job_result.metrics,
        }
    )

    company_result = _timed(
        "company_intelligence",
        timings,
        lambda: agents["company_intelligence"].run(
            CompanyIntelligenceInput(
                job=job,
                job_profile=job_result.output,
                jd_snapshot=jd_snapshot,
            ),
            ctx,
        ),
    )
    trace.append(company_result.to_dict())

    evidence_result = _timed(
        "evidence_mapping",
        timings,
        lambda: agents["evidence_mapping"].run(
            EvidenceMappingInput(
                resume_facts=knowledge_result.output.resume_facts,
                job_profile=job_result.output,
                inferred=inferred or [],
                knowledge_base=knowledge_result.output.knowledge_base,
            ),
            ctx,
        ),
    )
    trace.append(
        {
            "agent_id": evidence_result.agent_id,
            "output": {
                "mapping_count": len(evidence_result.output.mappings),
                "metrics": evidence_result.metrics,
            },
            "warnings": evidence_result.warnings,
            "metrics": evidence_result.metrics,
        }
    )

    strategy_result = _timed(
        "resume_strategy",
        timings,
        lambda: agents["resume_strategy"].run(
            ResumeStrategyInput(
                job_profile=job_result.output,
                company_profile=company_result.output,
                evidence_map=evidence_result.output,
                resume_facts=knowledge_result.output.resume_facts,
                ranked_requirements=ranked_requirements or [],
                fact_scores=fact_scores,
                job_analysis=job_analysis,
                language=language,
            ),
            ctx,
        ),
    )
    trace.append(
        {
            "agent_id": strategy_result.agent_id,
            "output": {
                k: v
                for k, v in strategy_result.output.to_dict().items()
                if k != "legacy_strategy"
            },
            "warnings": strategy_result.warnings,
            "metrics": strategy_result.metrics,
        }
    )

    return {
        "knowledge": knowledge_result.output,
        "job_profile": job_result.output,
        "company_profile": company_result.output,
        "evidence_map": evidence_result.output,
        "strategy": strategy_result.output,
        "agent_trace": trace,
        "agent_timings_ms": timings,
        "pipeline_version": PIPELINE_VERSION,
    }


def run_post_write_agents(
    *,
    resume: dict[str, Any],
    job_profile: Any,
    company_profile: Any,
    evidence_map: Any,
    strategy: Any,
    knowledge_base: Any = None,
    language: str = "en",
    use_cache: bool = True,
) -> dict[str, Any]:
    """Run Human Writer (optional polish already done), Recruiter + HM agents."""
    ctx = AgentContext(use_cache=use_cache, language=language)
    agents = build_agent_instances()
    timings: dict[str, int] = {}
    trace: list[dict[str, Any]] = []

    # Writer is typically already applied by writing_pipeline; expose agent view.
    writer_result = _timed(
        "human_resume_writer",
        timings,
        lambda: agents["human_resume_writer"].run(
            HumanWriterInput(
                validated_resume=resume,
                strategy=strategy,
                knowledge_base=knowledge_base,
                output_language=language,
            ),
            ctx,
        ),
    )
    # Prefer already-polished resume if writer would only passthrough
    polished = writer_result.output.tailored_resume or resume
    trace.append(
        {
            "agent_id": writer_result.agent_id,
            "output": {
                "mode": writer_result.output.mode,
                "facts_unchanged": writer_result.output.facts_unchanged,
            },
            "metrics": writer_result.metrics,
        }
    )

    recruiter_result = _timed(
        "senior_recruiter_review",
        timings,
        lambda: agents["senior_recruiter_review"].run(
            RecruiterReviewInput(resume=polished, output_language=language),
            ctx,
        ),
    )
    trace.append(recruiter_result.to_dict())

    hm_result = _timed(
        "hiring_manager_simulation",
        timings,
        lambda: agents["hiring_manager_simulation"].run(
            HiringManagerInput(
                resume=polished,
                job_profile=job_profile,
                company_profile=company_profile,
                evidence_map=evidence_map,
                strategy=strategy,
            ),
            ctx,
        ),
    )
    trace.append(hm_result.to_dict())

    return {
        "resume": polished,
        "writer": writer_result.output,
        "recruiter_review": recruiter_result.output,
        "hiring_manager_feedback": hm_result.output,
        "agent_trace": trace,
        "agent_timings_ms": timings,
    }


def attach_quality_intelligence(
    *,
    result: dict[str, Any],
    job_profile: Any,
    recruiter: Any,
    hiring_manager: Any,
    strategy: Any,
    agent_timings_ms: dict[str, int],
) -> dict[str, Any]:
    """Record anonymous metrics and attach aggregate-safe summary on result."""
    hard = [
        m
        for m in (getattr(result.get("evidence_map_obj", None), "mappings", None) or [])
    ]
    # evidence_map on result is legacy list
    evidence_list = list(result.get("evidence_map") or [])
    hard_list = [e for e in evidence_list if e.get("importance") == "hard"]
    matched = [
        e
        for e in hard_list
        if e.get("candidate_status") in ("MATCH", "PARTIAL")
    ]
    coverage = (len(matched) / len(hard_list)) if hard_list else 0.0

    recruiter_dict = recruiter.to_dict() if hasattr(recruiter, "to_dict") else dict(recruiter or {})
    hm_dict = (
        hiring_manager.to_dict()
        if hasattr(hiring_manager, "to_dict")
        else dict(hiring_manager or {})
    )
    strategy_dict = strategy.to_legacy() if hasattr(strategy, "to_legacy") else dict(strategy or {})

    metrics = build_metrics_from_pipeline(
        pipeline_version=PIPELINE_VERSION,
        job_family=str(getattr(job_profile, "job_family", None) or "general"),
        industry=str(getattr(job_profile, "industry", None) or "general"),
        language=str(result.get("language") or "en"),
        hiring_manager=hm_dict,
        recruiter=recruiter_dict,
        strategy=strategy_dict,
        resume=result.get("tailored_resume") or {},
        evidence_coverage=coverage,
        agent_timings_ms=agent_timings_ms,
    )
    recorded = record_generation_metrics(metrics)
    result["quality_intelligence"] = {
        "recorded": recorded,
        "metrics": metrics.to_dict(),
    }
    # silence unused
    _ = hard
    return result


# Re-export input types for tests
__all__ = [
    "AGENT_CATALOG",
    "LEGACY_AGENT_CATALOG",
    "PIPELINE_VERSION",
    "attach_quality_intelligence",
    "build_agent_instances",
    "run_agent_phase_bundle",
    "run_multi_agent_pipeline",
    "run_post_write_agents",
    "ClaimValidationInput",
    "TailoringAgentInput",
]
