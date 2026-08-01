"""Agent 10 — Hiring Manager Simulation.

Role: Hiring Manager for THIS specific job.
Evaluates: Can this person perform the job? What evidence supports that?
What concerns remain? Sends only weak sections back for rewriting.
Never modifies the resume.
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
    summary_words = len(summary.split())
    # Prefer concise one-page summaries (40–60 words)
    if 35 <= summary_words <= 65:
        scores["summary"] = 80
    elif 25 <= summary_words <= 80:
        scores["summary"] = 65
    else:
        scores["summary"] = 45 if summary else 20
    low_summary = summary.lower()
    if any(
        p in low_summary
        for p in (
            "professional with",
            "strong understanding",
            "passionate about",
            "highly motivated",
        )
    ):
        scores["summary"] = min(scores["summary"], 40)

    experience = resume.get("experience") or []
    bullets = 0
    tech_in_bullets = 0
    for entry in experience:
        if isinstance(entry, dict):
            entry_bullets = list(entry.get("bullets") or [])
            bullets += len(entry_bullets)
            techs = [str(t) for t in (entry.get("technologies") or []) if str(t).strip()]
            if techs and any(
                t.lower() in str(b).lower() for t in techs for b in entry_bullets
            ):
                tech_in_bullets += 1
    scores["experience"] = _clamp_score(40 + min(bullets, 10) * 4 + tech_in_bullets * 4)

    projects = resume.get("projects") or []
    if projects:
        pbullets = 0
        story_signal = 0
        for p in projects:
            if not isinstance(p, dict):
                continue
            pb = [str(b) for b in (p.get("bullets") or []) if str(b).strip()]
            pbullets += len(pb)
            for b in pb:
                if len(b.split()) >= 10 and any(
                    w in b.lower()
                    for w in ("designed", "built", "implemented", "supporting", "using")
                ):
                    story_signal += 1
        scores["projects"] = _clamp_score(40 + min(pbullets, 6) * 6 + min(story_signal, 3) * 5)
    else:
        scores["projects"] = 50  # optional section

    skills = resume.get("skills") or []
    scores["skills"] = _clamp_score(35 + min(len(skills), 8) * 6)

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

        # Hiring priorities / story from Job Intelligence
        hiring_priorities = list(getattr(job, "hiring_priorities", None) or [])
        narrative_themes = list(getattr(job, "narrative_themes", None) or [])
        archetype = str(getattr(job, "person_archetype", "") or "")
        screening_focus = list(getattr(job, "interview_screening_focus", None) or [])

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
        if archetype and archetype.lower()[:24] in resume_blob:
            why_interview.append(f"Signals match the person we want: {archetype}.")
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
        # Story / hiring-priority gaps reduce confidence
        missing_priority_signals = [
            p
            for p in (hiring_priorities[:4] + narrative_themes[:3])
            if p and str(p).lower().split()[0] not in resume_blob
        ]
        if missing_priority_signals and coverage < 0.7:
            why_reject.append(
                "Story does not clearly match what we hire for: "
                + ", ".join(str(p) for p in missing_priority_signals[:3])
                + "."
            )

        actionable: list[str] = []
        summary = str(
            resume.get("professional_summary") or resume.get("summary") or ""
        ).strip()
        if not summary or len(summary.split()) < 25:
            actionable.append(
                "I still don't understand why this candidate fits — rewrite the summary "
                "to sell specialization and evidenced strengths for THIS role."
            )
            if "summary" not in weakest:
                weakest = ["summary"] + list(weakest)
        elif any(
            p in summary.lower()
            for p in (
                "professional with",
                "strong understanding",
                "passionate about",
                "highly motivated",
                "knowledge of",
                "experienced in",
            )
        ):
            actionable.append(
                "Summary sounds AI-generated — rewrite into natural recruiter language "
                "that answers who / why fit / what work, without filler phrases."
            )
            if "summary" not in weakest:
                weakest = ["summary"] + list(weakest)
        # Challenge missing hiring-story themes when evidence likely exists
        for theme in (screening_focus or narrative_themes or hiring_priorities)[:3]:
            token = str(theme).lower().split()[0] if theme else ""
            if token and len(token) > 3 and token not in summary.lower():
                # Only ask to surface if somewhere on resume
                if token in resume_blob:
                    actionable.append(
                        f"Make '{theme}' more obvious in the Summary — evidence exists "
                        f"but my confidence that they can do this job is still low."
                    )
                    break
        # Challenge under-emphasized evidenced technologies
        for group_name, terms in (
            ("Cloud", list(job.cloud or [])),
            ("Database", list(job.databases or [])),
            ("Framework", list(job.frameworks or [])),
            ("Technology", list(job.technologies or [])[:6]),
        ):
            for term in terms[:2]:
                t = str(term).strip()
                if not t or t.lower() not in resume_blob:
                    continue
                # Present somewhere but not in experience/projects bullets
                exp_proj = " ".join(
                    str(b)
                    for e in list(resume.get("experience") or [])
                    + list(resume.get("projects") or [])
                    if isinstance(e, dict)
                    for b in (e.get("bullets") or [])
                ).lower()
                if t.lower() not in exp_proj:
                    actionable.append(
                        f"{group_name} experience ({t}) should be more visible in "
                        f"Experience/Projects bullets — weave it in only where evidenced."
                    )
        for req in missing_hard[:3]:
            actionable.append(
                f"Required '{req}' is too weak or missing — surface concrete evidence "
                f"only if it already exists in the resume."
            )
        for m in matched_hard[:3]:
            term = m.requirement
            if term and term.lower() not in resume_blob:
                actionable.append(
                    f"'{term}' is supported by evidence but not visible enough — "
                    f"reinforce it in summary/skills/projects without inventing facts."
                )
        for section in weakest:
            if section == "projects":
                actionable.append(
                    "Project bullets should better demonstrate problem solving — "
                    "rewrite as value stories using existing project facts."
                )
            elif section == "skills":
                actionable.append(
                    "Skills ordering does not lead with role-critical competencies — reorder."
                )
            elif section == "experience":
                theme_hint = (
                    f" Lead with evidenced work on: {', '.join(narrative_themes[:2])}."
                    if narrative_themes
                    else ""
                )
                actionable.append(
                    "Strongest role-relevant experience is under-emphasized — expand "
                    "exceptional evidenced bullets and reduce weaker duty lists."
                    + theme_hint
                )
            else:
                actionable.append(
                    f"Strengthen the {section} section with clearer, natural value statements."
                )
        if company.preferred_candidate_traits:
            trait = company.preferred_candidate_traits[0]
            actionable.append(
                f"Where evidenced, emphasize traits valued here (e.g. {trait})."
            )
        if overall < 70:
            why_reject.insert(
                0,
                "I still don't understand why this candidate can perform this specific job.",
            )
        if not actionable:
            actionable.append(
                "Tighten weakest bullets for scanability; keep emphasis role-specific."
            )
        # Only send weak sections back — dedupe preserve order
        weakest = list(dict.fromkeys(weakest))[:3]

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
