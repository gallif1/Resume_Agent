"""Agent 6 — Resume Tailoring Agent.

Generate resume structure using ONLY structured agent outputs.
Never invents facts. Never optimizes wording — content selection only.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    TailoredStructure,
    TailoringAgentInput,
)
from intelligent_tailoring.stages.single_resume_generation import (
    generate_resume_single_agent,
)


class ResumeTailoringAgent(Agent[TailoringAgentInput, TailoredStructure]):
    agent_id = "resume_generation_agent"
    responsibility = (
        "Single Resume Generation Agent — select content and write final prose"
    )

    def run(
        self,
        payload: TailoringAgentInput,
        context: AgentContext | None = None,
    ) -> AgentResult[TailoredStructure]:
        context = context or AgentContext()
        language = payload.language or context.language
        strategy = payload.strategy.to_legacy()
        evidence_list = payload.evidence_map.to_legacy_list()

        # Guard: drop any strategy keywords that would introduce forbidden claims
        forbidden = {
            str(x).lower()
            for x in (payload.strategy.forbidden_claims or [])
            if str(x).strip()
        }
        if strategy.get("keywords_to_insert"):
            strategy["keywords_to_insert"] = [
                kw
                for kw in strategy["keywords_to_insert"]
                if str(kw).lower() not in forbidden
            ]

        kb_summary = ""
        try:
            from intelligent_tailoring.stages.intelligence_bundle import (
                knowledge_base_compact_summary,
            )

            kb_summary = knowledge_base_compact_summary(
                getattr(payload.knowledge, "knowledge_base", None)
            )
        except Exception:
            kb_summary = ""

        generated = generate_resume_single_agent(
            resume_facts=payload.knowledge.resume_facts,
            rebuilt_resume=payload.rebuilt_resume or {},
            strategy=strategy,
            scores=payload.content_scores or {},
            ranked_requirements=payload.ranked_requirements or [],
            inferred=payload.inferred or [],
            evidence_map=evidence_list,
            language=language,
            use_cache=context.use_cache,
            regeneration_attempt=payload.regeneration_attempt,
            knowledge_base_summary=kb_summary,
        )

        resume = dict(generated.get("tailored_resume") or {})
        # Strip only No Evidence claims from skills.
        # Transferable / Weak Inference with supporting text may still surface.
        no_evidence_reqs = {
            m.requirement.lower()
            for m in payload.evidence_map.mappings
            if m.evidence_strength == "No Evidence"
            or (
                m.candidate_status == "MISSING"
                and not str(m.supporting_evidence or "").strip()
            )
        }
        skills = []
        for skill in resume.get("skills") or []:
            text = str(skill).strip()
            if not text:
                continue
            # Keep category headers and evidenced skills; drop pure unsupported claims
            if ":" in text:
                skills.append(text)
                continue
            if text.lower() in no_evidence_reqs:
                continue
            skills.append(text)
        resume["skills"] = skills

        matched = list(generated.get("matched_requirements") or [])
        if not matched:
            matched = [
                m.requirement
                for m in payload.evidence_map.mappings
                if m.candidate_status in ("MATCH", "PARTIAL")
                and m.importance in ("hard", "soft")
            ]
        missing = list(generated.get("missing_requirements") or [])
        if not missing:
            missing = [
                m.requirement
                for m in payload.evidence_map.mappings
                if m.candidate_status == "MISSING" and m.importance == "hard"
            ]

        from intelligent_tailoring.content_deduper import dedupe_resume_content
        from intelligent_tailoring.education_normalize import normalize_education_list

        resume = dedupe_resume_content(resume)
        resume["education"] = normalize_education_list(resume.get("education") or [])

        structure = TailoredStructure(
            professional_title=str(resume.get("professional_title") or ""),
            professional_summary=str(
                resume.get("professional_summary") or resume.get("summary") or ""
            ),
            skills=list(resume.get("skills") or []),
            experience=[e for e in (resume.get("experience") or []) if isinstance(e, dict)],
            projects=[p for p in (resume.get("projects") or []) if isinstance(p, dict)],
            education=[e for e in (resume.get("education") or []) if isinstance(e, dict)],
            certifications=list(resume.get("certifications") or []),
            matched_requirements=matched,
            missing_requirements=missing,
            change_log=list(generated.get("change_log") or []),
            ats_keywords_added=list(generated.get("ats_keywords_added") or []),
            removed_or_deprioritized_content=list(
                generated.get("removed_or_deprioritized_content") or []
            ),
            raw_generation=generated,
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=structure,
            metrics={
                "experience_count": len(structure.experience),
                "project_count": len(structure.projects),
                "skill_count": len(structure.skills),
                "matched_count": len(structure.matched_requirements),
                "missing_count": len(structure.missing_requirements),
                "primary_llm_calls": 1,
            },
        )
