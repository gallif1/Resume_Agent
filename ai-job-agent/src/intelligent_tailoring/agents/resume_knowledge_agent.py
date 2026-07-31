"""Agent 1 — Resume Knowledge Agent.

Responsibility: parse and structure candidate information into canonical facts.
Never generates text. Never tailors. Never infers.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    ResumeKnowledgeInput,
    ResumeKnowledgeOutput,
)
from intelligent_tailoring.knowledge_base import (
    build_knowledge_base,
    knowledge_base_to_resume_facts,
)
from intelligent_tailoring.stages.resume_extraction import extract_structured_resume


class ResumeKnowledgeAgent(Agent[ResumeKnowledgeInput, ResumeKnowledgeOutput]):
    agent_id = "resume_knowledge"
    responsibility = "Parse and structure candidate information into ResumeKnowledgeBase"

    def run(
        self,
        payload: ResumeKnowledgeInput,
        context: AgentContext | None = None,
    ) -> AgentResult[ResumeKnowledgeOutput]:
        context = context or AgentContext()
        language = payload.target_output_language or context.language

        kb = build_knowledge_base(
            payload.cv_profile,
            payload.source_documents,
            target_output_language=language,
        )
        resume_facts = knowledge_base_to_resume_facts(kb)
        classic = extract_structured_resume(
            payload.cv_profile, payload.source_documents
        )
        # Merge classic extraction only for structural fields KB may miss —
        # still facts only, never generated prose.
        if not resume_facts.get("experience_roles") and classic.get("experience_roles"):
            resume_facts["experience_roles"] = classic["experience_roles"]
        if not resume_facts.get("projects") and classic.get("projects"):
            resume_facts["projects"] = classic["projects"]

        warnings: list[str] = []
        sparse = bool(resume_facts.get("sparse")) or (
            kb.coverage
            and kb.coverage.extracted_fact_count == 0
            and classic.get("sparse")
        )
        if sparse:
            warnings.append("resume_sparse")

        coverage = kb.coverage.to_dict() if kb.coverage else {}
        output = ResumeKnowledgeOutput(
            knowledge_base=kb,
            resume_facts=resume_facts,
            content_hash=str(kb.content_hash or ""),
            source_language=str(kb.source_language or "en"),
            fact_count=len(kb.facts),
            coverage=coverage,
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=output,
            warnings=warnings,
            metrics={
                "fact_count": len(kb.facts),
                "coverage_score": float(
                    (kb.coverage.extraction_coverage_score if kb.coverage else 0) or 0
                ),
                "sparse": bool(sparse),
            },
        )


def run_resume_knowledge(
    cv_profile: dict[str, Any],
    source_documents: str | None = None,
    *,
    language: str | None = None,
    use_cache: bool = True,
) -> AgentResult[ResumeKnowledgeOutput]:
    return ResumeKnowledgeAgent().run(
        ResumeKnowledgeInput(
            cv_profile=cv_profile,
            source_documents=source_documents,
            target_output_language=language,
        ),
        AgentContext(use_cache=use_cache, language=language or "en"),
    )
