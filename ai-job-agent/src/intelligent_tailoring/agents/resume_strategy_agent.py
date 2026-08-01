"""Agent 5 — Resume Strategy Agent.

Builds the strongest truthful professional story for THIS job.
No writing. Company intelligence may influence prioritization only.
Success metric: interview probability — not keyword coverage.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    UNKNOWN,
    ResumeStrategy,
    ResumeStrategyInput,
)
from intelligent_tailoring.hiring_intent import classify_requirement_support_tier
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
    responsibility = (
        "Build the strongest truthful professional story for this job (no writing)"
    )

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
        # Carry hiring intent from Job Intelligence into strategy
        intent = dict(getattr(payload.job_profile, "hiring_intent", None) or {})
        if intent and "hiring_intent" not in job_analysis:
            job_analysis = dict(job_analysis)
            job_analysis["hiring_intent"] = intent

        company_priorities = _company_priorities(payload.company_profile)
        legacy = build_tailoring_strategy(
            job_analysis=job_analysis,
            resume_facts=payload.resume_facts,
            evidence_map=evidence_list,
            ranked_requirements=ranked,
            language=language,
            fact_scores=payload.fact_scores,
            hiring_intent=intent or None,
            company_priorities=company_priorities,
        )
        legacy = _apply_company_influence(legacy, company_priorities, evidence_list)

        coverage: dict[str, str] = {}
        for entry in evidence_list:
            req = str(entry.get("requirement") or "")
            if not req:
                continue
            strength = str(
                entry.get("evidence_strength") or entry.get("inference_category") or ""
            )
            coverage[req] = classify_requirement_support_tier(strength)

        # Prefer tiers already computed in highlight plan when present
        for req, tier in (legacy.get("requirement_coverage_tiers") or {}).items():
            if req:
                coverage[str(req)] = str(tier)

        safe_inferences = [
            str(entry.get("generated_statement") or entry.get("requirement") or "")
            for entry in evidence_list
            if entry.get("evidence_strength") == "Strong Inference"
            or entry.get("inference_category") == "Strongly Inferred"
            or coverage.get(str(entry.get("requirement") or ""))
            in ("Strong Supporting Evidence", "Transferable Evidence")
        ]
        safe_inferences = [s for s in safe_inferences if s][:20]

        forbidden = [
            str(entry.get("requirement") or "")
            for entry in evidence_list
            if coverage.get(str(entry.get("requirement") or "")) == "No Evidence"
            or entry.get("evidence_strength") in ("No Evidence",)
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
        narrative_themes = [str(t) for t in (legacy.get("narrative_themes") or []) if t]
        professional_story = str(legacy.get("professional_story") or "")

        # Genuine gaps — missing hard requirements / unsupported match types
        genuine_gaps: list[str] = []
        for entry in evidence_list:
            req = str(entry.get("requirement") or "").strip()
            if not req:
                continue
            tier = coverage.get(req, "")
            match_type = str(entry.get("match_type") or "")
            status = str(entry.get("candidate_status") or "")
            if (
                status == "MISSING"
                or match_type == "Unsupported"
                or tier in ("No Evidence", "Unsupported")
            ):
                if req not in genuine_gaps:
                    genuine_gaps.append(req)
        for gap in legacy.get("genuine_gaps") or []:
            if gap and str(gap) not in genuine_gaps:
                genuine_gaps.append(str(gap))
        genuine_gaps = genuine_gaps[:20]

        # Top reasons to interview — strongest evidenced signals only
        from intelligent_tailoring.interview_philosophy import (
            select_top_interview_reasons,
        )

        top_reasons = list(
            legacy.get("top_interview_reasons")
            or legacy.get("top_reasons_to_interview")
            or select_top_interview_reasons(
                highlight_plan=legacy.get("highlight_plan"),
                evidence_map=evidence_list,
                strategy=legacy,
            )
        )[:5]

        professional_narrative = str(
            legacy.get("professional_narrative") or professional_story or ""
        )
        if not professional_narrative and top_reasons:
            professional_narrative = (
                "Candidate interview case: "
                + "; ".join(top_reasons[:3])
                + (
                    f". Genuine gaps remain visible: {', '.join(genuine_gaps[:3])}."
                    if genuine_gaps
                    else "."
                )
            )

        # Always forbid seniority inflation and classic unsupported outcomes
        for phrase in (
            "over three years of expertise",
            "proven ability to lead projects from inception to deployment",
            "enhancing customer satisfaction",
            "supporting system scalability",
            "improving system reliability",
            "production-grade ownership",
            "TypeScript",
        ):
            # Only forbid TypeScript when it is a genuine gap
            if phrase == "TypeScript" and not any(
                "typescript" in g.lower() for g in genuine_gaps
            ):
                # Still forbid inventing it when absent from coverage as Explicit
                has_ts = any(
                    "typescript" in str(e.get("requirement") or "").lower()
                    and e.get("candidate_status") in ("MATCH", "PARTIAL")
                    for e in evidence_list
                )
                if has_ts:
                    continue
            if phrase not in forbidden:
                forbidden.append(phrase)

        one_page_budget = dict(legacy.get("one_page_budget") or {})
        if not one_page_budget:
            one_page_budget = {
                "summary_words_max": 70,
                "summary_words_min": 45,
                "max_experience_bullets_per_role": 4,
                "max_project_bullets": 4,
                "max_projects": 3,
                "max_skill_categories": 7,
                "prefer_strong_evidence": True,
            }
            legacy["one_page_budget"] = one_page_budget

        # Mirror new fields into legacy bag for downstream consumers
        legacy["professional_narrative"] = professional_narrative
        legacy["top_reasons_to_interview"] = top_reasons
        legacy["top_interview_reasons"] = top_reasons
        legacy["genuine_gaps"] = genuine_gaps
        legacy["forbidden_claims"] = forbidden

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
            narrative_themes=narrative_themes,
            professional_story=professional_story or professional_narrative,
            professional_narrative=professional_narrative,
            top_reasons_to_interview=top_reasons,
            facts_to_expand=important,
            facts_to_condense=low,
            facts_to_omit=hidden,
            genuine_gaps=genuine_gaps,
            one_page_budget=one_page_budget,
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
                "narrative_theme_count": len(narrative_themes),
                "genuine_gaps_count": len(genuine_gaps),
                "top_reasons_count": len(top_reasons),
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
