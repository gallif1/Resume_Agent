"""Agent 9 — Senior Recruiter Review.

Structured review feedback only. Does not modify facts.
Adversarial: rejects inflated years, academic-as-professional, generic AI summaries,
missing evidenced tech, unsupported outcomes, and unclear stories.
"""

from __future__ import annotations

import re

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    RecruiterReviewInput,
    RecruiterReviewOutput,
)
from intelligent_tailoring.claim_validator import hard_reject_claim
from intelligent_tailoring.services.senior_recruiter_review import review_resume
from intelligent_tailoring.writing.ai_detector import detect_ai_writing
from intelligent_tailoring.writing.style_validator import evaluate_writing_quality

_ACADEMIC_AS_PRO_RE = re.compile(
    r"\b(?:professional(?:ly)?\s+led|production[- ]grade|"
    r"years?\s+of\s+(?:professional\s+)?(?:full[\s-]?stack\s+)?experience)\b",
    re.I,
)
_INFLATED_YEARS_RE = re.compile(
    r"\b(?:over|more\s+than|at\s+least)\s+(?:three|3|four|4|five|5|\d+)\s*\+?\s*years?\b",
    re.I,
)
_UNSUPPORTED_OUTCOME_RE = re.compile(
    r"\b(customer\s+satisfaction|system\s+scalability|system\s+reliability|"
    r"streamlin(?:e|ed|ing)\s+delivery|optimiz(?:e|ed|ing)\s+team\s+workflows?)\b",
    re.I,
)


def _adversarial_scan(resume: dict) -> dict:
    """Deterministic adversarial checks — return risks + forced regenerations."""
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    blob_parts = [summary]
    for entry in list(resume.get("experience") or []) + list(resume.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        blob_parts.append(str(entry.get("description") or ""))
        blob_parts.extend(str(b) for b in (entry.get("bullets") or []))
    blob = "\n".join(blob_parts)

    credibility_risks: list[str] = []
    required_rewrites: list[str] = []
    sections: list[str] = []
    underused: list[str] = []
    top_strengths: list[str] = []
    one_page_issues: list[str] = []

    reject, reason = hard_reject_claim(summary, source_text="")
    if reject and "years" in reason:
        credibility_risks.append(f"Summary inflates years ({reason})")
        required_rewrites.append("Rewrite summary without unsupported years claims")
        sections.append("summary")

    if _INFLATED_YEARS_RE.search(blob):
        credibility_risks.append("Years-of-experience inflation detected")
        if "summary" not in sections:
            sections.append("summary")

    if _UNSUPPORTED_OUTCOME_RE.search(blob):
        credibility_risks.append("Unsupported business outcome language present")
        required_rewrites.append("Remove unsupported outcome claims")
        sections.extend(["experience", "projects"])

    # Academic presented as professional
    for proj in resume.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        name = str(proj.get("name") or "")
        desc = str(proj.get("description") or "")
        bullets = " ".join(str(b) for b in (proj.get("bullets") or []))
        if re.search(r"\bcapstone\b", name, re.I):
            if _ACADEMIC_AS_PRO_RE.search(desc + " " + bullets) and "academic" not in (
                desc + " " + bullets
            ).lower():
                credibility_risks.append(
                    "Capstone academic work presented without academic context"
                )
                required_rewrites.append(
                    "Preserve academic context for the capstone project"
                )
                sections.append("projects")
            else:
                top_strengths.append("Academic end-to-end capstone ownership")

    # Generic AI summary
    if re.search(
        r"\b(accomplished professional|results[- ]driven|highly motivated|"
        r"passionate about|proven ability|professional with)\b",
        summary,
        re.I,
    ):
        credibility_risks.append("Summary sounds generated / generic")
        required_rewrites.append("Rewrite summary in natural recruiter language")
        sections.append("summary")

    # Only treat as credibility risk when summary is missing or extremely thin.
    # Short but concrete summaries (e.g. one strong sentence) can still interview.
    if not summary or len(summary.split()) < 8:
        credibility_risks.append("Main candidate story is unclear in first 15 seconds")
        sections.append("summary")
    elif len(summary.split()) < 20 and not any(
        w in summary.lower()
        for w in ("built", "grew", "led", "managed", "developed", "owned", "exceeded")
    ):
        credibility_risks.append("Main candidate story is unclear in first 15 seconds")
        sections.append("summary")

    # Skill line health
    for line in resume.get("skills") or []:
        text = str(line)
        if re.search(r"other relevant skills:\s*api\b", text, re.I):
            credibility_risks.append("Skills contain invalid 'Other Relevant Skills: api'")
            sections.append("skills")
        if re.search(r"backend:\s*[^\n]*\breact\b", text, re.I):
            credibility_risks.append("React incorrectly categorized under Backend")
            sections.append("skills")

    # Word-count pressure heuristic for one-page
    total_bullets = sum(
        len(e.get("bullets") or [])
        for e in list(resume.get("experience") or []) + list(resume.get("projects") or [])
        if isinstance(e, dict)
    )
    if total_bullets > 18 or len(summary.split()) > 90:
        one_page_issues.append("Content density may exceed one page")

    # Strengths from first project bullets
    for proj in (resume.get("projects") or [])[:2]:
        if isinstance(proj, dict) and proj.get("name"):
            top_strengths.append(str(proj.get("name")))

    return {
        "credibility_risks": list(dict.fromkeys(credibility_risks))[:8],
        "required_rewrites": list(dict.fromkeys(required_rewrites))[:8],
        "sections": list(dict.fromkeys(sections))[:5],
        "underused_evidence": underused,
        "top_strengths": list(dict.fromkeys(top_strengths))[:5],
        "one_page_issues": one_page_issues,
    }


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
        adversarial = _adversarial_scan(payload.resume)

        interview_quality = int(raw.get("interview_quality") or style.get("overall_score") or 0)
        human = int(raw.get("human_believability") or ai.get("human_score") or 0)
        approved = bool(raw.get("approved"))
        issues = list(raw.get("issues") or [])
        sections = list(raw.get("sections_to_regenerate") or [])
        for sec in adversarial["sections"]:
            if sec not in sections:
                sections.append(sec)

        # Structured answers to the required recruiter questions
        sounds_robotic = (not bool(ai.get("passed", True))) or human < 65
        bullets_concise = int(style.get("dimensions", {}).get("conciseness") or 70) >= 65
        achievements_clear = int(
            style.get("dimensions", {}).get("scanning")
            or style.get("dimensions", {}).get("readability")
            or 70
        ) >= 65
        communicates_value = interview_quality >= 65
        if "would_interview" in raw:
            would_interview = bool(raw.get("would_interview"))
        else:
            would_interview = approved or (interview_quality >= 70 and human >= 65)

        # Hard reject draft when credibility risks are severe
        if adversarial["credibility_risks"]:
            would_interview = False
            approved = False
            for risk in adversarial["credibility_risks"]:
                issues.append({"type": "credibility_risk", "detail": risk})

        if would_interview and approved:
            recommendation = "interview"
        elif would_interview:
            recommendation = "maybe_interview"
        else:
            recommendation = "do_not_interview"

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
            interview_recommendation=recommendation,
            top_strengths=adversarial["top_strengths"],
            credibility_risks=adversarial["credibility_risks"],
            underused_evidence=adversarial["underused_evidence"],
            weak_sections=list(sections),
            required_rewrites=adversarial["required_rewrites"],
            one_page_issues=adversarial["one_page_issues"],
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=output,
            metrics={
                "would_interview": would_interview,
                "human_believability": human,
                "interview_quality": interview_quality,
                "approved": approved,
                "credibility_risk_count": len(adversarial["credibility_risks"]),
            },
        )
