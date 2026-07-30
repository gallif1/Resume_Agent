"""Stage 5 — Evidence mapping between resume facts and job requirements."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.ontology import SkillOntology, get_ontology
from intelligent_tailoring.schemas import InferredCompetency
from match_tailor_service import SourceEvidence, normalize_status, skill_supported_by_source
from skill_normalizer import skills_match


def build_evidence_map(
    *,
    resume_facts: dict[str, Any],
    requirements: dict[str, Any],
    inferred: list[InferredCompetency],
    ontology: SkillOntology | None = None,
) -> list[dict[str, Any]]:
    """Map each job requirement to resume evidence and an inference category."""
    ontology = ontology or get_ontology()
    source = str(resume_facts.get("raw_text") or "")
    evidence = SourceEvidence.build(source)
    resume_skills = [str(s) for s in (resume_facts.get("skills") or [])]

    entries: list[dict[str, Any]] = []

    def _add(
        requirement: str,
        *,
        importance: str,
        status: str,
        category: str,
        supporting: str,
        statement: str = "",
        confidence: float = 1.0,
        rule_id: str = "",
    ) -> None:
        entries.append(
            {
                "requirement": requirement,
                "importance": importance,
                "candidate_status": status,
                "inference_category": category,
                "supporting_evidence": supporting,
                "generated_statement": statement,
                "confidence_score": confidence,
                "ontology_rule_id": rule_id,
            }
        )

    hard = list(requirements.get("hard_requirements") or requirements.get("required_skills") or [])
    soft = list(requirements.get("soft_requirements") or requirements.get("preferred_skills") or [])

    for req in hard + soft:
        req_s = str(req).strip()
        if not req_s:
            continue
        importance = "hard" if req_s in hard else "soft"

        # Explicit skill match
        explicit = skill_supported_by_source(req_s, source) or any(
            skills_match(rs, req_s) for rs in resume_skills
        )
        if explicit:
            _add(
                req_s,
                importance=importance,
                status="MATCH",
                category="Explicit",
                supporting=f"Explicitly evidenced in resume for '{req_s}'",
                statement=req_s,
                confidence=1.0,
            )
            continue

        # Strongly inferred via prior inference list
        matched_inf = next(
            (
                inf
                for inf in inferred
                if req_s.lower() in inf.statement.lower()
                or req_s.lower() in inf.related_requirement.lower()
                or inf.related_requirement.lower() in req_s.lower()
            ),
            None,
        )
        if matched_inf:
            _add(
                req_s,
                importance=importance,
                status="PARTIAL",
                category="Strongly Inferred",
                supporting=matched_inf.supporting_evidence,
                statement=matched_inf.statement,
                confidence=matched_inf.confidence_score,
                rule_id=matched_inf.ontology_rule_id,
            )
            continue

        # Ontology bridge from resume terms → requirement
        mapped = ontology.normalize_term(req_s)
        if mapped != req_s and (
            skill_supported_by_source(mapped, source)
            or any(skills_match(rs, mapped) for rs in resume_skills)
        ):
            _add(
                req_s,
                importance=importance,
                status="PARTIAL",
                category="Strongly Inferred",
                supporting=f"Resume evidences '{mapped}' which maps to '{req_s}'",
                statement=mapped,
                confidence=0.85,
            )
            continue

        # Weak: partial token overlap only
        tokens = [t for t in req_s.lower().split() if len(t) > 3]
        if evidence and tokens and sum(1 for t in tokens if evidence.has_word(t)) >= max(
            1, len(tokens) // 2
        ):
            _add(
                req_s,
                importance=importance,
                status="PARTIAL",
                category="Weakly Inferred",
                supporting="Partial token overlap only — not auto-added",
                confidence=0.4,
            )
            continue

        _add(
            req_s,
            importance=importance,
            status="MISSING",
            category="Unsupported",
            supporting="No reliable evidence in original resume",
            confidence=0.0,
        )

    # Also record each Strongly Inferred competency as its own evidence row
    for inf in inferred:
        if any(
            e.get("generated_statement") == inf.statement for e in entries
        ):
            continue
        entries.append(
            {
                "requirement": inf.related_requirement,
                "importance": "inferred",
                "candidate_status": "PARTIAL",
                "inference_category": "Strongly Inferred",
                "supporting_evidence": inf.supporting_evidence,
                "generated_statement": inf.statement,
                "confidence_score": inf.confidence_score,
                "ontology_rule_id": inf.ontology_rule_id,
                "reasoning": inf.reasoning,
            }
        )

    return entries


def evidence_status_for_scoring(evidence_map: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Project evidence map into match_tailor hard/soft requirement buckets."""
    hard: list[dict[str, str]] = []
    soft: list[dict[str, str]] = []
    for entry in evidence_map:
        if entry.get("importance") == "inferred":
            continue
        item = {
            "requirement": str(entry.get("requirement") or ""),
            "candidate_status": normalize_status(entry.get("candidate_status")),
            "evidence_or_gap": str(entry.get("supporting_evidence") or ""),
        }
        if entry.get("importance") == "hard":
            hard.append(item)
        else:
            soft.append(item)
    return {"hard_requirements": hard, "soft_requirements": soft}
