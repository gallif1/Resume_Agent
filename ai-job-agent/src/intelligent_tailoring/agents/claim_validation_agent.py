"""Agent 7 — Claim Validation Agent.

Sentence-level validation. Decisions: Accept / Rewrite from evidence /
Regenerate / Reject. Never removes individual words mid-sentence.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    ClaimValidationInput,
    ClaimValidationItem,
    ClaimValidationResult,
)
from intelligent_tailoring.stages.claim_validation import run_claim_validation


def _map_decision(
    *,
    statement: str,
    rejected: set[str],
    rewritten: dict[str, str],
    warnings: list[dict[str, Any]],
) -> ClaimValidationItem:
    low = statement.strip().lower()
    if statement in rewritten or low in {k.lower() for k in rewritten}:
        new_text = rewritten.get(statement) or next(
            (v for k, v in rewritten.items() if k.lower() == low), ""
        )
        return ClaimValidationItem(
            statement=statement,
            decision="Rewrite from evidence",
            reason="Unsupported phrasing rewritten from evidence",
            rewritten_text=new_text,
        )
    if statement in rejected or low in {r.lower() for r in rejected}:
        # Check if warning suggests regenerate vs reject
        for w in warnings:
            if statement.lower() in str(w.get("statement") or "").lower():
                reason = str(w.get("reason") or "unsupported")
                if "corrupt" in reason.lower() or "regenerat" in reason.lower():
                    return ClaimValidationItem(
                        statement=statement,
                        decision="Regenerate",
                        reason=reason,
                    )
                return ClaimValidationItem(
                    statement=statement,
                    decision="Reject",
                    reason=reason,
                )
        return ClaimValidationItem(
            statement=statement,
            decision="Reject",
            reason="Unsupported claim",
        )
    return ClaimValidationItem(
        statement=statement,
        decision="Accept",
        reason="Supported by evidence",
    )


def _iter_statements(resume: dict[str, Any]) -> list[str]:
    statements: list[str] = []
    summary = str(resume.get("professional_summary") or resume.get("summary") or "").strip()
    if summary:
        statements.append(summary)
    for entry in resume.get("experience") or []:
        if isinstance(entry, dict):
            for b in entry.get("bullets") or []:
                text = str(b).strip()
                if text:
                    statements.append(text)
    for entry in resume.get("projects") or []:
        if isinstance(entry, dict):
            for b in entry.get("bullets") or []:
                text = str(b).strip()
                if text:
                    statements.append(text)
    return statements


class ClaimValidationAgent(Agent[ClaimValidationInput, ClaimValidationResult]):
    agent_id = "claim_validation"
    responsibility = "Validate every claim at sentence level against evidence"

    def run(
        self,
        payload: ClaimValidationInput,
        context: AgentContext | None = None,
    ) -> AgentResult[ClaimValidationResult]:
        context = context or AgentContext()
        requirements = (
            payload.job_profile.to_legacy_requirements()
            if payload.job_profile
            else {}
        )
        validation = run_claim_validation(
            original_resume_text=payload.original_resume_text,
            tailored_resume=payload.tailored_resume,
            evidence_map=payload.evidence_map.to_legacy_list(),
            change_log=payload.change_log or [],
            inferred=payload.inferred or [],
            job_requirements=requirements,
            use_cache=context.use_cache,
            run_llm_assist=False,
        )

        cleaned = dict(validation.get("cleaned_resume") or {})
        rejected = [str(x) for x in (validation.get("rejected_statements") or [])]
        warnings = [
            w for w in (validation.get("warnings") or []) if isinstance(w, dict)
        ]
        rewritten: dict[str, str] = {}
        for item in validation.get("rewritten_statements") or []:
            if isinstance(item, dict) and item.get("original") and item.get("rewritten"):
                rewritten[str(item["original"])] = str(item["rewritten"])

        rejected_set = set(rejected)
        decisions: list[ClaimValidationItem] = []
        for statement in _iter_statements(payload.tailored_resume):
            decisions.append(
                _map_decision(
                    statement=statement,
                    rejected=rejected_set,
                    rewritten=rewritten,
                    warnings=warnings,
                )
            )

        # Also record rejected statements that were stripped entirely
        seen = {d.statement for d in decisions}
        for statement in rejected:
            if statement not in seen:
                decisions.append(
                    ClaimValidationItem(
                        statement=statement,
                        decision="Reject",
                        reason="Unsupported claim removed at sentence level",
                    )
                )

        # Prefer explicit claim_validator signal when present
        if "passed" in validation:
            passed = bool(validation.get("passed"))
        elif rejected:
            # Rejects that remain in cleaned resume → fail
            blob = str(cleaned).lower()
            passed = not any(r.lower() in blob for r in rejected if len(r) > 12)
        else:
            passed = not any(d.decision == "Reject" for d in decisions)

        result = ClaimValidationResult(
            cleaned_resume=cleaned,
            decisions=decisions,
            rejected_statements=rejected,
            warnings=warnings,
            inferred_competencies=list(validation.get("inferred_competencies") or []),
            passed=passed,
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=result,
            metrics={
                "decision_count": len(decisions),
                "accepted": sum(1 for d in decisions if d.decision == "Accept"),
                "rewritten": sum(
                    1 for d in decisions if d.decision == "Rewrite from evidence"
                ),
                "rejected": sum(1 for d in decisions if d.decision == "Reject"),
                "regenerate": sum(1 for d in decisions if d.decision == "Regenerate"),
                "passed": passed,
            },
        )
