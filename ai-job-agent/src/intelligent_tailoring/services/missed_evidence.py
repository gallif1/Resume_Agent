"""Missed-evidence detection — find overlooked Resume KB facts for high-priority requirements."""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.knowledge_base import ResumeKnowledgeBase, _norm


def find_missed_evidence(
    *,
    kb: ResumeKnowledgeBase,
    job_requirements: dict[str, Any],
    evidence_map: list[dict[str, Any]],
    initially_selected_fact_ids: list[str] | None = None,
    fact_scores: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """For every high-priority requirement, search all KB facts for unused evidence."""
    selected = set(initially_selected_fact_ids or [])
    if not selected and fact_scores:
        # Top half of scored facts considered initially selected
        ranked = sorted(fact_scores, key=lambda x: -int(x.get("score") or 0))
        selected = {str(x["fact_id"]) for x in ranked[: max(8, len(ranked) // 2)]}

    high_priority = list(
        job_requirements.get("hard_requirements")
        or job_requirements.get("required_skills")
        or []
    )
    # Also include top responsibilities
    for r in (job_requirements.get("responsibilities") or [])[:6]:
        if r not in high_priority:
            high_priority.append(r)

    additional: list[dict[str, Any]] = []
    still_uncovered: list[str] = []
    exclusion_reasons: list[dict[str, str]] = []
    initially_selected_facts: list[dict[str, Any]] = []

    for fid in selected:
        fact = kb.fact_by_id(fid)
        if fact:
            initially_selected_facts.append(
                {"fact_id": fid, "text": fact.original_text, "section": fact.source_section}
            )

    covered_reqs: set[str] = set()
    for entry in evidence_map:
        if entry.get("candidate_status") in ("MATCH", "PARTIAL"):
            covered_reqs.add(_norm(str(entry.get("requirement") or "")))

    for req in high_priority:
        req_norm = _norm(str(req))
        req_tokens = set(re.findall(r"[a-z0-9\u0590-\u05ff]+", req_norm))
        found_extra = False
        for fact in kb.facts:
            if fact.id in selected:
                continue
            text_norm = _norm(fact.original_text)
            fact_tokens = set(re.findall(r"[a-z0-9\u0590-\u05ff]+", text_norm))
            overlap = len(req_tokens & fact_tokens) / max(len(req_tokens), 1)
            # Also check ontology-implied competencies stored on the fact
            implied_hit = any(
                _norm(c) in req_norm or req_norm in _norm(c)
                for c in (fact.implied_competencies or [])
            )
            if overlap >= 0.35 or implied_hit or any(
                t in text_norm for t in req_tokens if len(t) > 3
            ):
                additional.append(
                    {
                        "requirement": req,
                        "fact_id": fact.id,
                        "text": fact.original_text,
                        "source_section": fact.source_section,
                        "reason": "Overlooked fact relevant to high-priority requirement",
                        "overlap": round(overlap, 3),
                    }
                )
                found_extra = True
                selected.add(fact.id)  # avoid re-adding

        if not found_extra and req_norm not in covered_reqs:
            # Check if truly no evidence
            any_evidence = any(
                any(t in _norm(f.original_text) for t in req_tokens if len(t) > 3)
                for f in kb.facts
            )
            if not any_evidence:
                still_uncovered.append(str(req))
                exclusion_reasons.append(
                    {
                        "requirement": str(req),
                        "reason": "No supporting evidence found in Resume Knowledge Base",
                    }
                )

    return {
        "high_priority_requirements": [str(r) for r in high_priority],
        "initially_selected_facts": initially_selected_facts,
        "additional_relevant_facts_found": additional[:40],
        "facts_still_uncovered": still_uncovered[:20],
        "exclusion_reasons": exclusion_reasons[:20],
        "additional_fact_ids": [a["fact_id"] for a in additional],
    }


def enrich_strategy_with_missed_evidence(
    strategy: dict[str, Any],
    missed: dict[str, Any],
) -> dict[str, Any]:
    """Promote overlooked facts into strategy expand/preserve lists."""
    updated = dict(strategy)
    expand = list(updated.get("facts_to_expand") or [])
    preserve = list(updated.get("facts_to_preserve") or [])
    for item in missed.get("additional_relevant_facts_found") or []:
        text = str(item.get("text") or "").strip()
        if text and text not in expand:
            expand.append(text)
        if text and text not in preserve:
            preserve.append(text)
    updated["facts_to_expand"] = expand[:30]
    updated["facts_to_preserve"] = preserve[:40]
    warnings = list(updated.get("risk_warnings") or [])
    for uncovered in missed.get("facts_still_uncovered") or []:
        msg = f"No evidence for requirement: {uncovered}"
        if msg not in warnings:
            warnings.append(msg)
    updated["risk_warnings"] = warnings[:20]
    updated["missed_evidence"] = {
        "additional_count": len(missed.get("additional_relevant_facts_found") or []),
        "uncovered_count": len(missed.get("facts_still_uncovered") or []),
    }
    return updated
