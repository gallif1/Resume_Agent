"""Stage 6 — Rank job requirements by importance (deterministic)."""

from __future__ import annotations

from typing import Any


def rank_requirements(
    requirements: dict[str, Any],
    evidence_map: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return requirements sorted hard→soft, unmatched hard first for visibility."""
    evidence_map = evidence_map or []
    status_by_req = {
        str(e.get("requirement") or "").lower(): e for e in evidence_map
    }

    ranked: list[dict[str, Any]] = []

    def _push(text: str, *, importance: str, weight: float) -> None:
        text = str(text).strip()
        if not text:
            return
        if any(r["requirement"].lower() == text.lower() for r in ranked):
            return
        ev = status_by_req.get(text.lower()) or {}
        ranked.append(
            {
                "requirement": text,
                "importance": importance,
                "weight": weight,
                "candidate_status": ev.get("candidate_status") or "MISSING",
                "inference_category": ev.get("inference_category") or "Unsupported",
            }
        )

    for req in requirements.get("hard_requirements") or requirements.get("required_skills") or []:
        _push(str(req), importance="hard", weight=1.0)
    for req in requirements.get("tools_technologies") or []:
        _push(str(req), importance="hard", weight=0.9)
    for req in requirements.get("responsibilities") or []:
        _push(str(req), importance="hard", weight=0.85)
    for req in requirements.get("education_certifications") or []:
        _push(str(req), importance="hard", weight=0.8)
    for req in requirements.get("soft_requirements") or requirements.get("preferred_skills") or []:
        _push(str(req), importance="soft", weight=0.5)
    for req in requirements.get("soft_skills") or []:
        _push(str(req), importance="soft", weight=0.4)
    for req in requirements.get("ats_keywords") or []:
        _push(str(req), importance="soft", weight=0.35)

    # Unmet hard requirements first, then matched hard, then soft.
    def _sort_key(item: dict[str, Any]) -> tuple:
        hard = 0 if item["importance"] == "hard" else 1
        unmet = 0 if item.get("candidate_status") == "MISSING" else 1
        return (hard, unmet, -float(item["weight"]), item["requirement"].lower())

    ranked.sort(key=_sort_key)
    return ranked
