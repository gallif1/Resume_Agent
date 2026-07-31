"""Agent 9 — Senior Recruiter Review.

Structured review feedback only. Does not modify facts.
"""

from __future__ import annotations

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    RecruiterReviewInput,
    RecruiterReviewOutput,
)
from intelligent_tailoring.services.senior_recruiter_review import review_resume
from intelligent_tailoring.writing.ai_detector import detect_ai_writing
from intelligent_tailoring.writing.style_validator import evaluate_writing_quality


class SeniorRecruiterReviewAgent(Agent[RecruiterReviewInput, RecruiterReviewOutput]):
    agent_id = "senior_recruiter_review"
    responsibility = "Provide structured senior-recruiter feedback without changing facts"

    def run(
        self,
        payload: RecruiterReviewInput,
        context: AgentContext | None = None,
    ) -> AgentResult[RecruiterReviewOutput]:
        context = context or AgentContext()
        raw = review_resume(
            resume=payload.resume,
            output_language=payload.output_language or context.language,
            use_cache=context.use_cache,
            allow_llm=True,
        )
        style = evaluate_writing_quality(payload.resume)
        ai = detect_ai_writing(payload.resume)

        interview_quality = int(raw.get("interview_quality") or style.get("overall_score") or 0)
        human = int(raw.get("human_believability") or ai.get("human_score") or 0)
        approved = bool(raw.get("approved"))
        issues = list(raw.get("issues") or [])
        sections = list(raw.get("sections_to_regenerate") or [])

        # Structured answers to the required recruiter questions
        sounds_robotic = (not bool(ai.get("passed", True))) or human < 65
        bullets_concise = int(style.get("dimensions", {}).get("conciseness") or 70) >= 65
        achievements_clear = int(
            style.get("dimensions", {}).get("scanning")
            or style.get("dimensions", {}).get("readability")
            or 70
        ) >= 65
        communicates_value = interview_quality >= 65
        would_interview = approved or (interview_quality >= 70 and human >= 65)

        output = RecruiterReviewOutput(
            would_interview=would_interview,
            communicates_value=communicates_value,
            sounds_robotic=sounds_robotic,
            bullets_concise=bullets_concise,
            achievements_clear=achievements_clear,
            sections_to_strengthen=sections
            or list((style.get("weak_dimensions") or {}).keys()),
            approved=approved,
            human_believability=human,
            interview_quality=interview_quality,
            issues=issues,
            summary_feedback=str(raw.get("summary_feedback") or ""),
            sections_to_regenerate=sections,
            raw_review=dict(raw),
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=output,
            metrics={
                "would_interview": would_interview,
                "human_believability": human,
                "interview_quality": interview_quality,
                "approved": approved,
            },
        )
