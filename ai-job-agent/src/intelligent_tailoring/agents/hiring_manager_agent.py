"""Agent 10 — Hiring Manager Simulation.

Role: Hiring Manager for THIS specific job.
Returns actionable feedback only — never modifies the resume.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    UNKNOWN,
    HiringManagerFeedback,
    HiringManagerInput,
)
from intelligent_tailoring.writing.style_validator import evaluate_writing_quality


def _clamp_score(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _section_scores(resume: dict[str, Any], evidence_map: Any) -> dict[str, int]:
    scores: dict[str, int] = {}
    summary = str(resume.get("professional_summary") or resume.get("summary") or "").strip()
    scores["summary"] = 75 if 40 <= len(summary.split()) <= 90 else (55 if summary else 20)

    experience = resume.get("experience") or []
    bullets = 0
    for entry in experience:
        if isinstance(entry, dict):
            bullets += len(entry.get("bullets") or [])
    scores["experience"] = _clamp_score(40 + min(bullets, 12) * 4)

    projects = resume.get("projects") or []
    if projects:
        pbullets = sum(
            len(p.get("bullets") or []) for p in projects if isinstance(p, dict)
        )
        scores["projects"] = _clamp_score(45 + min(pbullets, 8) * 5)
    else:
        scores["projects"] = 50  # optional section

    skills = resume.get("skills") or []
    scores["skills"] = _clamp_score(35 + min(len(skills), 12) * 5)

    # Evidence coverage boosts experience/skills
    mappings = getattr(evidence_map, "mappings", []) or []
    covered = sum(
        1
        for m in mappings
        if getattr(m, "candidate_status", "") in ("MATCH", "PARTIAL")
        and getattr(m, "importance", "") == "hard"
    )
    hard_total = sum(1 for m in mappings if getattr(m, "importance", "") == "hard") or 1
    coverage = covered / hard_total
    scores["experience"] = _clamp_score(scores["experience"] * (0.7 + 0.3 * coverage))
    scores["skills"] = _clamp_score(scores["skills"] * (0.7 + 0.3 * coverage))
    return scores


class HiringManagerSimulationAgent(Agent[HiringManagerInput, HiringManagerFeedback]):
    agent_id = "hiring_manager_simulation"
    responsibility = "Simulate hiring-manager evaluation for this specific job"

    def run(
        self,
        payload: HiringManagerInput,
        context: AgentContext | None = None,
    ) -> AgentResult[HiringManagerFeedback]:
        _ = context
        resume = payload.resume or {}
        job = payload.job_profile
        company = payload.company_profile
        mappings = payload.evidence_map.mappings

        hard = [m for m in mappings if m.importance == "hard"]
        matched_hard = [
            m for m in hard if m.candidate_status in ("MATCH", "PARTIAL")
        ]
        missing_hard = [
            m.requirement
            for m in hard
            if m.candidate_status == "MISSING"
            or m.evidence_strength == "No Evidence"
        ]
        coverage = (len(matched_hard) / len(hard)) if hard else 0.5

        explicit = sum(1 for m in mappings if m.evidence_strength == "Explicit Evidence")
        strong = sum(1 for m in mappings if m.evidence_strength == "Strong Inference")
        evidence_quality = _clamp_score(
            (explicit * 10 + strong * 6) if mappings else 40
        )
        if mappings:
            evidence_quality = _clamp_score(
                100 * (explicit + 0.6 * strong) / max(len(mappings), 1)
            )

        style = evaluate_writing_quality(resume)
        resume_quality = int(style.get("overall_score") or 60)
        communication = int(
            style.get("dimensions", {}).get("professional_tone")
            or style.get("dimensions", {}).get("readability")
            or resume_quality
        )

        # Technical vs business fit — profession-agnostic
        tech_terms = set(
            t.lower()
            for t in (
                list(job.technologies)
                + list(job.cloud)
                + list(job.databases)
                + list(job.frameworks)
                + list(job.required_skills)
            )
            if t
        )
        resume_blob = str(resume).lower()
        tech_hits = sum(1 for t in tech_terms if t in resume_blob)
        technical_fit = _clamp_score(
            40 + (tech_hits / max(len(tech_terms), 1)) * 60 if tech_terms else 55 + coverage * 30
        )

        business_signals = list(company.business_priorities or []) + list(
            job.business_domain or []
        )
        business_hits = sum(
            1 for s in business_signals if str(s).lower()[:20] in resume_blob
        )
        customer_bonus = 8 if any(
            w in resume_blob for w in ("customer", "client", "patient", "guest")
        ) and company.customer_type not in (UNKNOWN, "") else 0
        business_fit = _clamp_score(
            45 + coverage * 35 + min(business_hits, 3) * 5 + customer_bonus
        )

        overall = _clamp_score(
            coverage * 40
            + evidence_quality * 0.2
            + technical_fit * 0.15
            + business_fit * 0.15
            + resume_quality * 0.1
        )

        section_effectiveness = _section_scores(resume, payload.evidence_map)
        ranked_sections = sorted(
            section_effectiveness.items(), key=lambda kv: kv[1], reverse=True
        )
        strongest = [name for name, score in ranked_sections if score >= 70][:3]
        weakest = [name for name, score in ranked_sections if score < 65][:3]
        if not strongest and ranked_sections:
            strongest = [ranked_sections[0][0]]
        if not weakest and len(ranked_sections) > 1:
            weakest = [ranked_sections[-1][0]]

        why_interview: list[str] = []
        if matched_hard:
            sample = ", ".join(m.requirement for m in matched_hard[:4])
            why_interview.append(f"Evidence covers key requirements: {sample}.")
        if technical_fit >= 70:
            why_interview.append("Role-relevant capabilities are clearly evidenced.")
        if business_fit >= 70:
            why_interview.append("Background aligns with business/domain priorities.")
        if resume_quality >= 70:
            why_interview.append("Resume communicates value clearly and professionally.")
        if not why_interview:
            why_interview.append("Partial alignment — interview only if pipeline is thin.")

        why_reject: list[str] = []
        for req in missing_hard[:5]:
            why_reject.append(f"Missing evidence for required: {req}.")
        if evidence_quality < 50:
            why_reject.append("Evidence quality is too thin for confident assessment.")
        if resume_quality < 55:
            why_reject.append("Resume quality undercuts the candidate's signal.")
        if coverage < 0.4:
            why_reject.append("Too many hard requirements lack supporting evidence.")

        actionable: list[str] = []
        for req in missing_hard[:4]:
            actionable.append(
                f"If accurate, surface concrete evidence for '{req}' from real experience."
            )
        for section in weakest:
            actionable.append(f"Strengthen the {section} section with clearer outcomes.")
        if company.preferred_candidate_traits:
            trait = company.preferred_candidate_traits[0]
            actionable.append(
                f"Where evidenced, emphasize traits valued here (e.g. {trait})."
            )
        if not actionable:
            actionable.append("Maintain evidence honesty; tighten weakest bullets for scanability.")

        feedback = HiringManagerFeedback(
            overall_fit=overall,
            technical_fit=technical_fit,
            business_fit=business_fit,
            communication=communication,
            resume_quality=resume_quality,
            evidence_quality=evidence_quality,
            missing_evidence=missing_hard[:12],
            section_effectiveness=section_effectiveness,
            why_interview=why_interview,
            why_reject=why_reject,
            strongest_sections=strongest,
            weakest_sections=weakest,
            actionable_feedback=actionable[:8],
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=feedback,
            metrics={
                "overall_fit": overall,
                "hard_coverage": round(coverage, 3),
                "missing_hard": len(missing_hard),
            },
        )
