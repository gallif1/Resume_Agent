"""SeniorRecruiterReviewService — independent human-likeness review."""

from __future__ import annotations

import json
import logging
from typing import Any

from ai_client import is_ai_available
from intelligent_tailoring.llm_utils import call_stage_json
from intelligent_tailoring.prompts.human_writer_prompts import (
    SENIOR_RECRUITER_REVIEW_SYSTEM,
    build_recruiter_review_user_prompt,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION, SchemaValidationError
from intelligent_tailoring.writing.ai_detector import detect_ai_writing
from intelligent_tailoring.writing.grammar_validator import validate_grammar
from intelligent_tailoring.writing.style_validator import evaluate_writing_quality

logger = logging.getLogger("intelligent_tailoring.recruiter_review")

MAX_REVIEW_CYCLES = 3


def _heuristic_review(resume: dict[str, Any]) -> dict[str, Any]:
    """Deterministic senior-recruiter-style review when LLM is unavailable."""
    grammar = validate_grammar(resume)
    style = evaluate_writing_quality(resume)
    ai = detect_ai_writing(resume)

    issues: list[dict[str, Any]] = []
    sections: set[str] = set()

    for item in grammar.get("issues") or []:
        issues.append(
            {
                "section": item.get("section") or "overall",
                "problem": ",".join(item.get("patterns") or [])[:160],
                "guidance": "Fix grammar/awkward wording without changing facts.",
            }
        )
        sections.update(grammar.get("affected_sections") or [])

    for dim, score in (style.get("weak_dimensions") or {}).items():
        issues.append(
            {
                "section": "overall",
                "problem": f"{dim} score {score} below threshold",
                "guidance": f"Improve {dim.replace('_', ' ')} while keeping all facts identical.",
            }
        )
        sections.update(style.get("affected_sections") or [])

    for signal in ai.get("signals") or []:
        issues.append(
            {
                "section": "summary" if "summary" in signal else "experience",
                "problem": signal,
                "guidance": "Rewrite to sound naturally human; remove AI clichés and repetition.",
            }
        )
        sections.update(ai.get("affected_sections") or [])

    human = int(ai.get("human_score") or style["dimensions"].get("ai_likeness") or 0)
    interview = int(style.get("overall_score") or 0)
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).lower()
    if any(
        p in summary
        for p in (
            "professional with",
            "strong understanding",
            "passionate about",
            "highly motivated",
            "proven track record",
        )
    ):
        issues.append(
            {
                "section": "summary",
                "problem": "Summary uses generic AI filler phrasing",
                "guidance": (
                    "Rewrite the summary to sound like a human resume writer. "
                    "Explain role fit with evidenced strengths — no clichés."
                ),
            }
        )
        sections.update({"summary"})
    # Thin projects → request regeneration
    for entry in resume.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        bullets = [b for b in (entry.get("bullets") or []) if str(b).strip()]
        if 0 < len(bullets) < 2 or any(len(str(b).split()) < 6 for b in bullets):
            issues.append(
                {
                    "section": "projects",
                    "problem": "Project bullets are too thin or generic",
                    "guidance": (
                        "Expand project bullets using only existing project facts "
                        "to show technical value and design decisions."
                    ),
                }
            )
            sections.update({"projects"})
            break
    approved = (
        not issues
        and human >= 75
        and interview >= 75
        and bool(grammar.get("passed"))
        and bool(style.get("passed"))
        and bool(ai.get("passed"))
    )
    # Normalize section names
    norm_sections = sorted(
        {
            "summary"
            if s in {"summary", "professional_summary"}
            else s
            for s in sections
            if s in {"summary", "experience", "projects", "skills", "overall"}
            or s in {"summary", "experience", "projects", "skills"}
        }
    )
    norm_sections = [s for s in norm_sections if s != "overall"]
    if not approved and not norm_sections:
        norm_sections = ["summary", "experience"]

    return {
        "approved": approved,
        "human_believability": human,
        "interview_quality": interview,
        "issues": issues[:12],
        "sections_to_regenerate": [] if approved else norm_sections,
        "summary_feedback": (
            "Ready for interview shortlist."
            if approved
            else "Writing still feels generic or uneven — polish affected sections."
        ),
        "mode": "heuristic",
    }


def _validate_review(data: dict[str, Any]) -> None:
    if "approved" not in data:
        raise SchemaValidationError("review missing approved")
    if not isinstance(data.get("issues", []), list):
        raise SchemaValidationError("issues must be a list")


def _normalize_review(raw: dict[str, Any]) -> dict[str, Any]:
    issues = []
    for item in raw.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            {
                "section": str(item.get("section") or "overall"),
                "problem": str(item.get("problem") or "")[:240],
                "guidance": str(item.get("guidance") or "")[:320],
            }
        )
    sections = [
        str(s).strip().lower()
        for s in (raw.get("sections_to_regenerate") or [])
        if str(s).strip()
    ]
    # Map aliases
    mapped: list[str] = []
    for s in sections:
        if s in {"professional_summary", "profile", "summary"}:
            mapped.append("summary")
        elif s in {"experience", "work", "employment"}:
            mapped.append("experience")
        elif s in {"projects", "project"}:
            mapped.append("projects")
        elif s == "skills":
            mapped.append("skills")
    mapped = list(dict.fromkeys(mapped))
    approved = bool(raw.get("approved"))
    if approved:
        mapped = []
    return {
        "approved": approved,
        "human_believability": max(
            0, min(100, int(raw.get("human_believability") or 0))
        ),
        "interview_quality": max(0, min(100, int(raw.get("interview_quality") or 0))),
        "issues": issues,
        "sections_to_regenerate": mapped,
        "summary_feedback": str(raw.get("summary_feedback") or "")[:400],
        "mode": "llm",
    }


class SeniorRecruiterReviewService:
    """Independent review stage — feedback only, no factual rewrites."""

    def review(
        self,
        *,
        resume: dict[str, Any],
        output_language: str = "en",
        use_cache: bool = True,
        allow_llm: bool = True,
    ) -> dict[str, Any]:
        return review_resume(
            resume=resume,
            output_language=output_language,
            use_cache=use_cache,
            allow_llm=allow_llm,
        )


def review_resume(
    *,
    resume: dict[str, Any],
    output_language: str = "en",
    use_cache: bool = True,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """Review polished resume; return structured feedback for the writer."""
    heuristic = _heuristic_review(resume)

    if not allow_llm or not is_ai_available():
        return heuristic

    try:
        raw = call_stage_json(
            system_prompt=SENIOR_RECRUITER_REVIEW_SYSTEM,
            user_prompt=build_recruiter_review_user_prompt(
                resume_json=json.dumps(resume, ensure_ascii=False, indent=2),
                output_language=output_language or "en",
            ),
            validate=_validate_review,
            use_cache=use_cache,
            cache_namespace=f"{PIPELINE_VERSION}_recruiter_review",
            cache_payload=f"{output_language}|{json.dumps(resume, sort_keys=True)[:3000]}",
            temperature=0.2,
        )
        llm_review = _normalize_review(raw)
        # Combine: if heuristic found hard AI/grammar issues, keep those sections
        if not heuristic["approved"] and llm_review["approved"]:
            # Don't let LLM rubber-stamp obvious AI clichés / grammar failures
            llm_review["approved"] = False
            llm_review["sections_to_regenerate"] = list(
                dict.fromkeys(
                    list(llm_review.get("sections_to_regenerate") or [])
                    + list(heuristic.get("sections_to_regenerate") or [])
                )
            )
            llm_review["issues"] = (llm_review.get("issues") or []) + (
                heuristic.get("issues") or []
            )[:6]
            llm_review["summary_feedback"] = (
                (llm_review.get("summary_feedback") or "")
                + " | Heuristic flags require another polish pass."
            ).strip(" |")
            llm_review["mode"] = "llm+heuristic"
        # Merge scores conservatively
        llm_review["human_believability"] = min(
            int(llm_review.get("human_believability") or 0),
            int(heuristic.get("human_believability") or 0)
            if not heuristic["approved"]
            else int(llm_review.get("human_believability") or 0),
        ) or int(llm_review.get("human_believability") or heuristic.get("human_believability") or 0)
        return llm_review
    except (SchemaValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("recruiter review LLM failed: %s", exc)
        heuristic["mode"] = "heuristic_fallback"
        heuristic["llm_error"] = str(exc)[:240]
        return heuristic
