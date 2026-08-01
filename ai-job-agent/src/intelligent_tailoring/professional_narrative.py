"""Final professional narrative test before export.

Answers the seven interview-probability questions. If unclear, signals which
sections need targeted regeneration — never the whole resume.
"""

from __future__ import annotations

import re
from typing import Any


_GENERIC_OPENINGS = re.compile(
    r"\b(accomplished professional|results[- ]driven|highly motivated|"
    r"passionate about|proven ability|professional with knowledge|"
    r"seasoned professional)\b",
    re.I,
)


def evaluate_professional_narrative(
    resume: dict[str, Any],
    *,
    strategy: dict[str, Any] | None = None,
    genuine_gaps: list[str] | None = None,
    top_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Return narrative clarity answers + sections needing regeneration."""
    strategy = strategy or {}
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    skills = list(resume.get("skills") or [])
    projects = [p for p in (resume.get("projects") or []) if isinstance(p, dict)]
    experience = [e for e in (resume.get("experience") or []) if isinstance(e, dict)]
    blob = " ".join(
        [summary]
        + [str(s) for s in skills]
        + [
            str(b)
            for e in experience + projects
            for b in (e.get("bullets") or [])
        ]
    )

    reasons = list(
        top_reasons
        or strategy.get("top_reasons_to_interview")
        or strategy.get("top_interview_reasons")
        or []
    )[:3]
    gaps = list(genuine_gaps or strategy.get("genuine_gaps") or [])[:8]
    narrative = str(
        strategy.get("professional_narrative")
        or strategy.get("professional_story")
        or ""
    ).strip()

    who = ""
    if summary:
        who = summary.split(".")[0].strip()[:160]
    elif experience:
        who = f"{experience[0].get('title') or 'Candidate'} with evidenced work history"

    can_do: list[str] = []
    for reason in reasons:
        if reason:
            can_do.append(str(reason))
    if not can_do:
        # Fall back to first project/experience bullets
        for entry in projects[:2] + experience[:1]:
            for b in (entry.get("bullets") or [])[:2]:
                can_do.append(str(b)[:120])
    can_do = can_do[:3]

    relevance = narrative or (
        "; ".join(reasons[:2]) if reasons else "Role relevance not clearly stated"
    )

    seniority_ok = True
    seniority_notes: list[str] = []
    if re.search(
        r"\b(over|more than)\s+(three|3|four|4|five|5|\d+)\s+years?\b",
        summary,
        re.I,
    ):
        seniority_ok = False
        seniority_notes.append("Summary inflates years of experience")
    if _GENERIC_OPENINGS.search(summary):
        seniority_notes.append("Summary uses generic AI opening language")

    # 15–20 second screen: summary + top reasons + clear skills
    screen_clear = bool(summary) and len(summary.split()) >= 35 and bool(can_do)
    if _GENERIC_OPENINGS.search(summary):
        screen_clear = False

    answers = {
        "who_is_candidate": who or "Unclear",
        "what_can_they_do": can_do,
        "why_relevant": relevance,
        "top_three_interview_reasons": reasons[:3] or can_do[:3],
        "important_gaps": gaps,
        "seniority_preserved": seniority_ok,
        "recruiter_story_clear_in_15s": screen_clear,
    }

    sections_to_regenerate: list[str] = []
    if not who or who == "Unclear" or len(summary.split()) < 30:
        sections_to_regenerate.append("summary")
    if not can_do:
        if projects:
            sections_to_regenerate.append("projects")
        elif experience:
            sections_to_regenerate.append("experience")
    if not screen_clear and "summary" not in sections_to_regenerate:
        sections_to_regenerate.append("summary")
    if not seniority_ok and "summary" not in sections_to_regenerate:
        sections_to_regenerate.append("summary")
    # Underused evidenced tech in skills
    emphasize = [
        str(s).lower()
        for s in (strategy.get("skills_to_emphasize") or strategy.get("skills_priority") or [])
    ]
    skills_blob = " ".join(str(s).lower() for s in skills)
    missing_skill_emphasis = [
        s for s in emphasize[:6] if s and s not in skills_blob and s in blob.lower()
    ]
    if missing_skill_emphasis:
        sections_to_regenerate.append("skills")

    passed = (
        bool(who and who != "Unclear")
        and bool(can_do)
        and screen_clear
        and seniority_ok
        and len(sections_to_regenerate) == 0
    )

    return {
        "passed": passed,
        "answers": answers,
        "sections_to_regenerate": list(dict.fromkeys(sections_to_regenerate)),
        "seniority_notes": seniority_notes,
        "missing_skill_emphasis": missing_skill_emphasis[:6],
    }
