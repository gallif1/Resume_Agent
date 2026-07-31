"""Agent 5 — Resume Strategy Agent.

Creates the entire resume strategy before any writing begins.
No writing. Company intelligence may influence prioritization only.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    UNKNOWN,
    ResumeStrategy,
    ResumeStrategyInput,
)
from intelligent_tailoring.services.job_analyzer import analyze_job
from intelligent_tailoring.services.tailoring_strategy_builder import (
    build_tailoring_strategy,
)
from intelligent_tailoring.stages.requirement_ranking import rank_requirements


def _company_priorities(company_profile: Any) -> list[str]:
    if company_profile is None:
        return []
    priorities: list[str] = []
    for trait in getattr(company_profile, "preferred_candidate_traits", []) or []:
        if trait and trait not in priorities:
            priorities.append(str(trait))
    for item in getattr(company_profile, "business_priorities", []) or []:
        if item and item not in priorities:
            priorities.append(str(item))
    culture = str(getattr(company_profile, "engineering_culture", "") or "")
    if culture and culture != UNKNOWN and culture not in priorities:
        priorities.append(culture)
    comm = str(getattr(company_profile, "communication_style", "") or "")
    if comm and comm != UNKNOWN and comm not in priorities:
        priorities.append(comm)
    return priorities[:10]


def _apply_company_influence(
    legacy: dict[str, Any],
    company_priorities: list[str],
    evidence_map_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reorder emphasis using company signals without inventing facts."""
    if not company_priorities:
        return legacy
    skills = list(legacy.get("skills_to_emphasize") or [])
    blob_priorities = " ".join(company_priorities).lower()
    boosted = [
        s
        for s in skills
        if any(tok in str(s).lower() for tok in blob_priorities.split() if len(tok) > 3)
    ]
    remainder = [s for s in skills if s not in boosted]
    legacy["skills_to_emphasize"] = (boosted + remainder)[:16]
    legacy["company_influenced_priorities"] = company_priorities

    # Prefer evidence that aligns with company communication/customer focus
    if any(
        x in blob_priorities
        for x in ("customer", "empathy", "stakeholder", "service")
    ):
        focus = str(legacy.get("summary_focus") or "")
        if "customer" not in focus.lower() and "client" not in focus.lower():
            matched = [
                e["requirement"]
                for e in evidence_map_list
                if e.get("candidate_status") in ("MATCH", "PARTIAL")
                and any(
                    w in str(e.get("requirement") or "").lower()
                    for w in ("customer", "client", "patient", "guest", "support")
                )
            ]
            if matched:
                legacy["summary_focus"] = (
                    (focus + " Highlight customer/client impact from evidenced work.").strip()
                )
    return legacy


class ResumeStrategyAgent(Agent[ResumeStrategyInput, ResumeStrategy]):
    agent_id = "resume_strategy"
    responsibility = "Decide resume strategy (order, focus, coverage) without writing"

    def run(
        self,
        payload: ResumeStrategyInput,
        context: AgentContext | None = None,
    ) -> AgentResult[ResumeStrategy]:
        context = context or AgentContext()
        language = payload.language or context.language
        requirements = payload.job_profile.to_legacy_requirements()
        evidence_list = payload.evidence_map.to_legacy_list()

        ranked = payload.ranked_requirements or rank_requirements(
            requirements, evidence_list
        )
        job_analysis = payload.job_analysis
        if job_analysis is None:
            job_analysis = analyze_job(
                {
                    "title": payload.job_profile.title,
                    "company": payload.job_profile.company,
                },
                use_cache=context.use_cache,
                jd_snapshot=payload.job_profile.jd_text,
                requirements=requirements,
            )

        legacy = build_tailoring_strategy(
            job_analysis=job_analysis,
            resume_facts=payload.resume_facts,
            evidence_map=evidence_list,
            ranked_requirements=ranked,
            language=language,
            fact_scores=payload.fact_scores,
        )

        company_priorities = _company_priorities(payload.company_profile)
        legacy = _apply_company_influence(legacy, company_priorities, evidence_list)

        coverage: dict[str, str] = {}
        for entry in evidence_list:
            req = str(entry.get("requirement") or "")
            if not req:
                continue
            strength = str(entry.get("evidence_strength") or entry.get("inference_category") or "")
            coverage[req] = strength

        safe_inferences = [
            str(entry.get("generated_statement") or entry.get("requirement") or "")
            for entry in evidence_list
            if entry.get("evidence_strength") == "Strong Inference"
            or entry.get("inference_category") == "Strongly Inferred"
        ]
        safe_inferences = [s for s in safe_inferences if s][:20]

        forbidden = [
            str(entry.get("requirement") or "")
            for entry in evidence_list
            if entry.get("evidence_strength") in ("No Evidence", "Weak Inference")
            or entry.get("candidate_status") == "MISSING"
        ]
        # Also fold explicit forbidden wording from evidence map
        for entry in evidence_list:
            for phrase in entry.get("forbidden_wording") or []:
                if phrase and phrase not in forbidden:
                    forbidden.append(str(phrase))
        forbidden = forbidden[:40]

        important = list(legacy.get("facts_to_expand") or [])[:20]
        low = list(legacy.get("facts_to_condense") or [])[:20]
        hidden = list(legacy.get("facts_to_omit") or [])[:20]

        strategy = ResumeStrategy(
            summary_focus=str(legacy.get("summary_focus") or ""),
            project_order=[str(x) for x in (legacy.get("project_priority") or [])],
            experience_order=[str(x) for x in (legacy.get("experience_order") or [])],
            skills_priority=[str(x) for x in (legacy.get("skills_to_emphasize") or [])],
            section_order=[str(x) for x in (legacy.get("section_order") or [])],
            important_evidence=important,
            low_priority_evidence=low,
            hidden_evidence=hidden,
            safe_inferences=safe_inferences,
            forbidden_claims=forbidden,
            requirement_coverage=coverage,
            company_influenced_priorities=company_priorities,
            legacy_strategy=legacy,
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=strategy,
            metrics={
                "skills_priority_count": len(strategy.skills_priority),
                "important_evidence_count": len(strategy.important_evidence),
                "forbidden_claims_count": len(strategy.forbidden_claims),
                "company_influence_count": len(company_priorities),
                "coverage_count": len(coverage),
            },
        )


def run_resume_strategy(payload: ResumeStrategyInput, **kwargs: Any) -> AgentResult[ResumeStrategy]:
    return ResumeStrategyAgent().run(
        payload,
        AgentContext(
            use_cache=bool(kwargs.get("use_cache", True)),
            language=str(kwargs.get("language") or payload.language or "en"),
        ),
    )
