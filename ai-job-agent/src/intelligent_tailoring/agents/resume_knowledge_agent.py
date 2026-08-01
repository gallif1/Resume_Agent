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
        # Merge classic extraction for structural fields KB may miss —
        # prefer the richer of the two (more bullets / project content).
        # Still facts only, never generated prose.
        def _bullet_total(roles: list) -> int:
            total = 0
            for r in roles or []:
                if isinstance(r, dict):
                    total += len([b for b in (r.get("bullets") or []) if str(b).strip()])
            return total

        def _project_total(projects: list) -> int:
            total = 0
            for p in projects or []:
                if isinstance(p, dict):
                    total += len([b for b in (p.get("bullets") or []) if str(b).strip()])
                    if str(p.get("description") or "").strip():
                        total += 1
            return total

        classic_roles = classic.get("experience_roles") or []
        kb_roles = resume_facts.get("experience_roles") or []
        if _bullet_total(classic_roles) > _bullet_total(kb_roles):
            resume_facts["experience_roles"] = classic_roles
        elif not kb_roles and classic_roles:
            resume_facts["experience_roles"] = classic_roles

        classic_projects = classic.get("projects") or []
        kb_projects = resume_facts.get("projects") or []
        if _project_total(classic_projects) > _project_total(kb_projects):
            resume_facts["projects"] = classic_projects
        elif not kb_projects and classic_projects:
            resume_facts["projects"] = classic_projects

        if classic.get("skills") and (
            len(classic.get("skills") or [])
            > len(resume_facts.get("skills") or [])
        ):
            resume_facts["skills"] = list(classic.get("skills") or [])
            resume_facts["display_skills"] = list(classic.get("skills") or [])
        resume_facts["extraction_meta"] = classic.get("extraction_meta") or {}

        warnings: list[str] = []
        sparse = bool(resume_facts.get("sparse")) or (
            kb.coverage
            and kb.coverage.extracted_fact_count == 0
            and classic.get("sparse")
        )
        if sparse:
            warnings.append("resume_sparse")

        coverage = kb.coverage.to_dict() if kb.coverage else {}
        # Extraction coverage for high-value signals that must not disappear later
        high_value_signals = (
            "FastAPI", "SQLAlchemy", "PostgreSQL", "WebSockets", "AWS", "EC2",
            "RDS", "S3", "CI/CD", "pytest", "integration", "Generative AI",
            "React", "React Native", "Angular", "HTML", "CSS", "Node.js",
            "Laravel", "REST", "SQLite", "Firebase", "algorithms",
            "data structures", "tutoring", "debugging", "Cursor", "ChatGPT",
            "Claude", "Copilot",
        )
        blob = (kb.raw_text or "") + " " + " ".join(
            f.original_text for f in kb.facts
        )
        blob_l = blob.lower()
        present = [s for s in high_value_signals if s.lower() in blob_l]
        missing_from_facts: list[str] = []
        fact_blob = " ".join(f.original_text for f in kb.facts).lower()
        for signal in present:
            if signal.lower() not in fact_blob:
                missing_from_facts.append(signal)
        coverage["high_value_signals_present"] = present
        coverage["high_value_signals_missing_from_facts"] = missing_from_facts
        # Context-type distribution for provenance audits
        ctx_counts: dict[str, int] = {}
        for fact in kb.facts:
            key = str(getattr(fact, "context_type", "") or "other")
            ctx_counts[key] = ctx_counts.get(key, 0) + 1
        coverage["context_type_counts"] = ctx_counts
        if missing_from_facts:
            warnings.append(
                "high_value_signals_under_extracted:"
                + ",".join(missing_from_facts[:8])
            )

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
                "high_value_signal_count": len(present),
                "context_types": len(ctx_counts),
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
