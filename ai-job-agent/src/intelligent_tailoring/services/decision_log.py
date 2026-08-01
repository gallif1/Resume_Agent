"""Human-readable AI decision log for the live generation UI.

Not chain-of-thought. Concise explanations that build user trust.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.interview_philosophy import (
    decision_emphasize,
    decision_omit,
    decision_reorder,
    decision_rewrite,
    select_top_interview_reasons,
)


def build_decision_log(
    *,
    strategy: dict[str, Any] | None = None,
    evidence_map: list[dict[str, Any]] | None = None,
    highlight_plan: dict[str, Any] | None = None,
    removed: list[str] | None = None,
    change_log: list[dict[str, Any]] | None = None,
    recruiter_review: dict[str, Any] | None = None,
    hiring_manager: dict[str, Any] | None = None,
    one_page: dict[str, Any] | None = None,
    writing_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Assemble trust-building decision entries from pipeline artifacts."""
    out: list[dict[str, Any]] = []
    strategy = strategy or {}
    plan = highlight_plan or strategy.get("highlight_plan") or {}

    top = list(strategy.get("top_interview_reasons") or [])[:3]
    if not top:
        top = select_top_interview_reasons(
            highlight_plan=plan,
            evidence_map=evidence_map,
            strategy=strategy,
            limit=3,
        )
    for reason in top:
        out.append(
            {
                "stage": "resume_strategy",
                **decision_emphasize(
                    reason,
                    why="it is among the strongest evidenced reasons to interview",
                ),
            }
        )

    for term in list(plan.get("unsupported_hard") or [])[:4]:
        out.append(
            {
                "stage": "evidence_mapping",
                **decision_omit(
                    str(term),
                    why="no reliable supporting evidence exists",
                ),
            }
        )

    for term in list(strategy.get("facts_to_omit") or [])[:3]:
        text = str(term)[:80]
        if text:
            out.append(
                {
                    "stage": "resume_strategy",
                    **decision_omit(
                        text,
                        why="it does not raise interview probability for this role",
                    ),
                }
            )

    # Project / experience reorder signals
    project_priority = list(strategy.get("project_priority") or [])
    if project_priority:
        out.append(
            {
                "stage": "resume_strategy",
                **decision_reorder(
                    f"project '{project_priority[0]}'",
                    why="it most closely matches the target role",
                ),
            }
        )

    for item in (change_log or [])[:6]:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section") or "content")
        reason = str(item.get("reason") or "stronger role-fit wording")
        out.append(
            {
                "stage": "human_writer",
                **decision_rewrite(section, why=reason[:120]),
            }
        )

    if writing_report and writing_report.get("hm_refine_pass"):
        out.append(
            {
                "stage": "hiring_manager",
                **decision_rewrite(
                    "weak sections",
                    why="hiring manager feedback required clearer role fit",
                ),
            }
        )

    recruiter = recruiter_review or {}
    if recruiter.get("sections_to_regenerate"):
        secs = ", ".join(str(s) for s in recruiter["sections_to_regenerate"][:3])
        out.append(
            {
                "stage": "senior_recruiter",
                **decision_rewrite(
                    secs or "summary",
                    why="recruiter review found weak interview signal",
                ),
            }
        )

    hm = hiring_manager or {}
    for tip in list(hm.get("actionable_feedback") or [])[:3]:
        out.append(
            {
                "stage": "hiring_manager",
                "action": "challenge",
                "text": str(tip)[:180],
                "target": "resume",
                "reason": "hiring manager challenge",
            }
        )

    if one_page and one_page.get("compressed"):
        out.append(
            {
                "stage": "final_polish",
                "action": "compress",
                "text": "Reduced repetition and prioritized strongest bullets for a one-page resume",
                "target": "layout",
                "reason": "one-page interview scanability",
            }
        )

    for text in (removed or [])[:3]:
        snippet = str(text).strip()[:70]
        if snippet:
            out.append(
                {
                    "stage": "resume_strategy",
                    **decision_omit(
                        snippet,
                        why="low interview value for this role",
                    ),
                }
            )

    # Deduplicate by text
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in out:
        key = str(item.get("text") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:24]
