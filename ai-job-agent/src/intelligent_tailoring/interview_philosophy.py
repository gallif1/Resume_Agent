"""Interview-probability philosophy shared by every resume agent.

Core question for every decision:
  "Would this change increase the probability that a busy recruiter
   invites this candidate for an interview?"

Not document generation — persuasion through truthful evidence.
Profession-agnostic.
"""

from __future__ import annotations

import re
from typing import Any

PIPELINE_PHILOSOPHY = """
INTERVIEW-PROBABILITY PHILOSOPHY (mandatory for every decision):

You are not generating a document. You are maximizing interview probability
using only truthful evidence.

Before every change, ask:
"If I were reviewing hundreds of resumes today, would THIS change increase
the probability that I invite this candidate for an interview?"

THE 20-SECOND RULE:
Recruiters spend ~15–20 seconds on the first screen. The resume must answer:
1. Who is this candidate?
2. What problems can they solve?
3. Why are they relevant for THIS role?
4. Why should I keep reading?
If the Summary would not make a busy recruiter continue, rewrite it.

HIRING INTENT FIRST:
Understand what type of person the company wants — not just keywords.
Every profession has hidden hiring priorities. Infer them. Tell that story.

EVIDENCE OVER KEYWORDS:
Recruiters hire evidence, not keyword density.
Prefer convincing demonstrated experience over stuffing requirements.
Surface Explicit, Strong Supporting, and Transferable evidence. Never invent.

SELL THE STRONGEST EVIDENCE:
Identify the three strongest reasons this candidate deserves an interview.
Everything else is secondary. Prefer five excellent bullets over twelve average ones.
Expand exceptional evidence. Reduce weaker evidence.

QUALITY BEFORE COMPLETENESS:
Remove low-value information. Every line costs recruiter attention.
If a sentence does not raise interview probability, rewrite or remove it.

THE FINAL QUESTION:
Would you confidently recommend interviewing this candidate?
If not YES, regenerate the weak sections.

Never invent facts. Stay profession-agnostic.
""".strip()

# Live UI stage catalog — four merged agents (legacy 11-agent ids map into these).
TAILOR_STAGES: list[dict[str, str]] = [
    {
        "id": "candidate_opportunity_intelligence",
        "agent_id": "candidate_opportunity_intelligence",
        "label_en": "Analyzing your experience and the opportunity",
        "label_he": "מנתח את הניסיון שלך ואת ההזדמנות",
        "message_en": "Reading candidate profile, job, company, and evidence…",
        "message_he": "קורא פרופיל מועמד, משרה, חברה וראיות…",
    },
    {
        "id": "strategy_content_selection",
        "agent_id": "strategy_content_selection",
        "label_en": "Building the best resume strategy",
        "label_he": "בונה את אסטרטגיית קורות החיים",
        "message_en": "Selecting evidence and structuring the one-page narrative…",
        "message_he": "בוחר ראיות ובונה נרטיב בעמוד אחד…",
    },
    {
        "id": "human_writing_credibility",
        "agent_id": "human_writing_credibility",
        "label_en": "Writing and validating your resume",
        "label_he": "כותב ומאמת את קורות החיים",
        "message_en": "Validating claims and writing natural recruiter-ready prose…",
        "message_he": "מאמת טענות ומנסח ניסוח טבעי למגייסים…",
    },
    {
        "id": "final_hiring_ats_page",
        "agent_id": "final_hiring_ats_page",
        "label_en": "Final recruiter, ATS, and one-page review",
        "label_he": "ביקורת סופית — מגייס, ATS ועמוד אחד",
        "message_en": "Hiring-manager fit, ATS alignment, and one-page enforcement…",
        "message_he": "בודק התאמה, ATS ועמוד אחד…",
    },
]

STAGE_INDEX = {s["id"]: i for i, s in enumerate(TAILOR_STAGES)}

# Legacy specialist / tool stage ids → merged UI stage
LEGACY_STAGE_TO_MERGED: dict[str, str] = {
    "resume_knowledge": "candidate_opportunity_intelligence",
    "job_intelligence": "candidate_opportunity_intelligence",
    "company_intelligence": "candidate_opportunity_intelligence",
    "evidence_mapping": "candidate_opportunity_intelligence",
    "resume_strategy": "strategy_content_selection",
    "resume_tailoring": "strategy_content_selection",
    "claim_validation": "human_writing_credibility",
    "human_writer": "human_writing_credibility",
    "human_resume_writer": "human_writing_credibility",
    "senior_recruiter": "human_writing_credibility",
    "senior_recruiter_review": "human_writing_credibility",
    "hiring_manager": "final_hiring_ats_page",
    "hiring_manager_simulation": "final_hiring_ats_page",
    "final_polish": "final_hiring_ats_page",
    "final_quality": "final_hiring_ats_page",
    "ats_scoring": "final_hiring_ats_page",
    "one_page_enforcement": "final_hiring_ats_page",
    # Also accept already-merged ids
    "candidate_opportunity_intelligence": "candidate_opportunity_intelligence",
    "strategy_content_selection": "strategy_content_selection",
    "human_writing_credibility": "human_writing_credibility",
    "final_hiring_ats_page": "final_hiring_ats_page",
}

STAGE_SUBSTEPS: dict[str, tuple[str, ...]] = {
    "candidate_opportunity_intelligence": (
        "Reading candidate profile",
        "Analyzing job requirements",
        "Reviewing company context",
        "Mapping evidence",
    ),
    "strategy_content_selection": (
        "Selecting strongest evidence",
        "Building tailored structure",
    ),
    "human_writing_credibility": (
        "Validating claims",
        "Writing natural prose",
        "Recruiter credibility review",
    ),
    "final_hiring_ats_page": (
        "Hiring manager simulation",
        "ATS scoring",
        "One-page enforcement",
    ),
}


def resolve_merged_stage(stage: str | None) -> str:
    """Map any legacy or merged stage id to one of the four UI stages."""
    if not stage:
        return TAILOR_STAGES[0]["id"]
    return LEGACY_STAGE_TO_MERGED.get(stage, stage)


def select_top_interview_reasons(
    *,
    highlight_plan: dict[str, Any] | None,
    evidence_map: list[dict[str, Any]] | None,
    strategy: dict[str, Any] | None,
    limit: int = 3,
) -> list[str]:
    """Return up to ``limit`` strongest interview-worthy reasons (truthful)."""
    reasons: list[str] = []
    plan = highlight_plan or {}
    for req in list(plan.get("must_highlight") or []):
        text = str(req).strip()
        if text and text not in reasons:
            reasons.append(text)
        if len(reasons) >= limit:
            return reasons[:limit]

    for entry in evidence_map or []:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("candidate_status") or "")
        strength = str(entry.get("evidence_strength") or "")
        importance = str(entry.get("importance") or "")
        if status not in ("MATCH", "PARTIAL"):
            continue
        if importance != "hard" and "Explicit" not in strength:
            continue
        text = str(entry.get("requirement") or "").strip()
        if text and text not in reasons:
            reasons.append(text)
        if len(reasons) >= limit:
            break

    if len(reasons) < limit:
        for term in list((strategy or {}).get("skills_to_emphasize") or []):
            text = str(term).strip()
            if text and text not in reasons:
                reasons.append(text)
            if len(reasons) >= limit:
                break
    return reasons[:limit]


def decision_emphasize(term: str, *, why: str) -> dict[str, str]:
    return {
        "action": "emphasize",
        "text": f"Highlighting {term} because {why}",
        "target": term,
        "reason": why,
    }


def decision_omit(term: str, *, why: str) -> dict[str, str]:
    return {
        "action": "omit",
        "text": f"Not mentioning {term} because {why}",
        "target": term,
        "reason": why,
    }


def decision_reorder(what: str, *, why: str) -> dict[str, str]:
    return {
        "action": "reorder",
        "text": f"Moving {what} higher because {why}",
        "target": what,
        "reason": why,
    }


def decision_rewrite(section: str, *, why: str) -> dict[str, str]:
    return {
        "action": "rewrite",
        "text": f"Rewriting {section} because {why}",
        "target": section,
        "reason": why,
    }


def bullet_interview_score(bullet: str, emphasize: list[str]) -> int:
    """Score how much a bullet raises interview probability for this role."""
    text = re.sub(r"\s+", " ", (bullet or "").strip())
    low = text.lower()
    if not text:
        return -100
    score = min(len(text.split()), 30)
    for term in emphasize:
        t = (term or "").strip().lower()
        if t and t in low:
            score += 28
    # Value / problem-solving signals (profession-agnostic verbs)
    if re.search(
        r"\b(designed|built|implemented|developed|led|taught|negotiated|"
        r"resolved|improved|reduced|increased|configured|diagnosed|"
        r"coordinated|delivered|secured|trained|forecast|audited)\b",
        low,
    ):
        score += 12
    if re.search(r"\b(\d+%|\$\d+|patients?|students?|clients?|customers?)\b", low):
        score += 10
    # Low-value duty language
    if re.match(
        r"^(responsible for|worked on|helped with|participated in|"
        r"various duties|day-to-day)\b",
        low,
    ):
        score -= 20
    if len(text.split()) < 6:
        score -= 15
    return score


def build_generation_report(
    *,
    result: dict[str, Any],
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    """Concise end-of-run report for the live generation UI."""
    evidence = list(result.get("evidence_map") or [])
    matched = [
        e
        for e in evidence
        if isinstance(e, dict) and e.get("candidate_status") in ("MATCH", "PARTIAL")
    ]
    inferred = list(result.get("inferred_competencies") or [])
    change_log = list(result.get("change_log") or [])
    writing = result.get("writing_report") or {}
    recruiter = result.get("recruiter_review") or {}
    hm = result.get("hiring_manager_feedback") or {}
    strategy = result.get("tailoring_strategy") or {}
    top_reasons = list(
        strategy.get("top_interview_reasons")
        or select_top_interview_reasons(
            highlight_plan=strategy.get("highlight_plan"),
            evidence_map=evidence,
            strategy=strategy,
        )
    )
    score_breakdown = dict(result.get("score_breakdown") or {})
    if not score_breakdown and result.get("original_match_score") is not None:
        # Minimal fallback when scoring details were not attached.
        orig = result.get("original_match_score")
        tailored = result.get("tailored_match_score")
        if orig is not None and tailored is not None:
            score_breakdown = {
                "original_score": int(orig),
                "tailored_score": int(tailored),
                "score_delta": int(tailored) - int(orig),
                "calculation_status": "complete",
                "still_missing": list(result.get("missing_requirements") or [])[:12],
                "improved_because": top_reasons[:6],
            }

    return {
        "status": "success",
        "job_requirements_analyzed": len(evidence),
        "candidate_strengths_identified": len(matched),
        "transferable_skills_inferred": len(inferred),
        "resume_revisions": len(change_log),
        "ats_optimization_completed": True,
        "recruiter_review_completed": bool(recruiter),
        "hiring_manager_review_completed": bool(hm),
        "would_interview": bool(
            recruiter.get("would_interview")
            if recruiter.get("would_interview") is not None
            else int(recruiter.get("interview_quality") or 0) >= 70
        ),
        "top_interview_reasons": top_reasons,
        "review_cycles": writing.get("review_cycles"),
        "hm_refine_pass": bool(writing.get("hm_refine_pass")),
        "generation_time_seconds": (
            round(elapsed_seconds, 1) if elapsed_seconds is not None else None
        ),
        "pipeline_version": result.get("pipeline_version"),
        "sections_changed": _sections_changed(change_log),
        "score_breakdown": score_breakdown,
        "agents_total": len(TAILOR_STAGES),
        "agents_completed": len(TAILOR_STAGES),
        "overall_progress": 100,
        "original_match_score": result.get("original_match_score"),
        "tailored_match_score": result.get("tailored_match_score"),
    }


def _sections_changed(change_log: list[dict[str, Any]]) -> list[str]:
    sections: list[str] = []
    for item in change_log:
        if not isinstance(item, dict):
            continue
        sec = str(item.get("section") or "").strip().lower()
        if not sec:
            continue
        if "summary" in sec:
            label = "Improved Summary"
        elif "experience" in sec:
            label = "Experience rewritten"
        elif "project" in sec:
            label = "Projects reordered"
        elif "skill" in sec:
            label = "Skills reorganized"
        else:
            label = sec.replace("_", " ").title()
        if label not in sections:
            sections.append(label)
    if not any("ATS" in s for s in sections):
        sections.append("ATS optimization completed")
    return sections
