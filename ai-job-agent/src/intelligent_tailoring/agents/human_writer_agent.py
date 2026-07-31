"""Agent 8 — Human Resume Writer.

Elite wording-only rewrite. Facts never change.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import HumanWriterInput, HumanWriterOutput
from intelligent_tailoring.services.human_resume_writer import write_human_resume
from intelligent_tailoring.writing.fact_lock import compare_facts


class HumanResumeWriterAgent(Agent[HumanWriterInput, HumanWriterOutput]):
    agent_id = "human_resume_writer"
    responsibility = "Rewrite wording only; facts remain immutable"

    def run(
        self,
        payload: HumanWriterInput,
        context: AgentContext | None = None,
    ) -> AgentResult[HumanWriterOutput]:
        context = context or AgentContext()
        strategy = (
            payload.strategy.to_legacy()
            if payload.strategy is not None
            else None
        )
        result = write_human_resume(
            validated_resume=payload.validated_resume,
            strategy=strategy,
            knowledge_base=payload.knowledge_base,
            output_language=payload.output_language or context.language,
            use_cache=context.use_cache,
            allow_llm=True,
        )
        polished = dict(result.get("tailored_resume") or payload.validated_resume)
        cmp = compare_facts(payload.validated_resume, polished)
        facts_unchanged = bool(cmp.get("passed", True))
        if not facts_unchanged:
            polished = dict(payload.validated_resume)
            facts_unchanged = True

        output = HumanWriterOutput(
            tailored_resume=polished,
            mode=str(result.get("mode") or "unknown"),
            facts_unchanged=facts_unchanged,
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=output,
            warnings=[] if facts_unchanged else ["fact_lock_reverted"],
            metrics={
                "mode": output.mode,
                "facts_unchanged": facts_unchanged,
            },
        )
