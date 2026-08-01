"""Agent 4 — Evidence Mapping Agent.

Maps every job requirement to candidate evidence with strength, confidence,
source location, allowed wording, and forbidden wording.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    EvidenceMap,
    EvidenceMapping,
    EvidenceMappingInput,
    normalize_evidence_strength,
)
from intelligent_tailoring.ontology import get_ontology
from intelligent_tailoring.stages.evidence_mapping import build_evidence_map
from intelligent_tailoring.stages.semantic_inference import run_semantic_inference


_FORBIDDEN_GENERIC = (
    "expert in",
    "world-class",
    "best-in-class",
    "ninja",
    "rockstar",
    "guru",
    "passionate about",
    "highly motivated",
    "proven track record of excellence",
)


def _source_location(kb: Any, supporting: str, resume_facts: dict[str, Any]) -> str:
    text = (supporting or "").strip()
    if not text:
        return "unknown"
    if kb is not None and hasattr(kb, "facts"):
        low = text.lower()
        for fact in kb.facts:
            original = str(getattr(fact, "original_text", "") or "").lower()
            if original and (original in low or low in original or low[:40] in original):
                section = str(getattr(fact, "source_section", "") or "resume")
                entry = str(getattr(fact, "source_entry_id", "") or "")
                return f"{section}:{entry}" if entry else section
    # Fall back to section scan on structured facts
    for role in resume_facts.get("experience_roles") or []:
        if not isinstance(role, dict):
            continue
        for bullet in role.get("bullets") or []:
            if text.lower()[:48] in str(bullet).lower():
                company = str(role.get("company") or "experience")
                return f"experience:{company}"
    for project in resume_facts.get("projects") or []:
        if not isinstance(project, dict):
            continue
        blob = " ".join(
            [str(project.get("name") or "")]
            + [str(b) for b in (project.get("bullets") or [])]
        )
        if text.lower()[:48] in blob.lower():
            return f"projects:{project.get('name') or 'project'}"
    skills = resume_facts.get("display_skills") or resume_facts.get("skills") or []
    if any(text.lower() in str(s).lower() or str(s).lower() in text.lower() for s in skills):
        return "skills"
    return "resume"


def _allowed_wording(
    *,
    requirement: str,
    supporting: str,
    strength: str,
    statement: str,
) -> list[str]:
    allowed: list[str] = []
    if supporting:
        # Prefer grounding phrases taken from evidence, not invented claims
        snippet = supporting.strip()
        if len(snippet) > 160:
            snippet = snippet[:157].rstrip() + "..."
        allowed.append(snippet)
    if strength == "Explicit Evidence" and requirement:
        allowed.append(requirement)
    if statement and statement not in allowed and strength in (
        "Explicit Evidence",
        "Strong Inference",
    ):
        allowed.append(statement)
    return allowed[:6]


def _forbidden_wording(requirement: str, strength: str) -> list[str]:
    forbidden = list(_FORBIDDEN_GENERIC)
    if strength == "No Evidence":
        forbidden.extend(
            [
                f"expert in {requirement}",
                f"extensive experience with {requirement}",
                f"proficient in {requirement}",
                f"deep expertise in {requirement}",
                requirement,  # cannot claim without any evidence
            ]
        )
    elif strength == "Weak Inference":
        # Transferable: allow careful mention; forbid expert-level claims
        forbidden.extend(
            [
                f"expert in {requirement}",
                f"extensive experience with {requirement}",
                f"proficient in {requirement}",
                f"deep expertise in {requirement}",
                f"years of experience with {requirement}",
            ]
        )
    elif strength == "Strong Inference":
        forbidden.extend(
            [
                f"expert in {requirement}",
                f"certified in {requirement}",
                f"years of experience with {requirement}",
            ]
        )
    # Dedupe preserve order
    out: list[str] = []
    for item in forbidden:
        if item and item not in out:
            out.append(item)
    return out


class EvidenceMappingAgent(Agent[EvidenceMappingInput, EvidenceMap]):
    agent_id = "evidence_mapping"
    responsibility = "Map every job requirement to candidate evidence with wording constraints"

    def run(
        self,
        payload: EvidenceMappingInput,
        context: AgentContext | None = None,
    ) -> AgentResult[EvidenceMap]:
        context = context or AgentContext()
        ontology = get_ontology()
        requirements = payload.job_profile.to_legacy_requirements()
        inferred = list(payload.inferred or [])

        # Run semantic inference if caller did not supply it (keeps agent self-contained)
        if not inferred:
            inferred = run_semantic_inference(
                resume_facts=payload.resume_facts,
                requirements=requirements,
                language=context.language,
                use_cache=context.use_cache,
                ontology=ontology,
            )

        legacy = build_evidence_map(
            resume_facts=payload.resume_facts,
            requirements=requirements,
            inferred=inferred,
            ontology=ontology,
        )

        mappings: list[EvidenceMapping] = []
        for entry in legacy:
            category = str(entry.get("inference_category") or "Unsupported")
            status = str(entry.get("candidate_status") or "MISSING")
            strength = normalize_evidence_strength(category, status)
            requirement = str(entry.get("requirement") or "")
            supporting = str(entry.get("supporting_evidence") or "")
            statement = str(entry.get("generated_statement") or "")
            source = _source_location(
                payload.knowledge_base, supporting, payload.resume_facts
            )
            mappings.append(
                EvidenceMapping(
                    requirement=requirement,
                    evidence_strength=strength,
                    candidate_status=status,
                    importance=str(entry.get("importance") or "soft"),
                    source_location=source,
                    supporting_evidence=supporting,
                    confidence=float(entry.get("confidence_score") or 0.0),
                    allowed_wording=_allowed_wording(
                        requirement=requirement,
                        supporting=supporting,
                        strength=strength,
                        statement=statement,
                    ),
                    forbidden_wording=_forbidden_wording(requirement, strength),
                    inference_category=category,
                    ontology_rule_id=str(entry.get("ontology_rule_id") or ""),
                    generated_statement=statement,
                )
            )

        evidence_map = EvidenceMap(mappings=mappings)
        explicit = sum(1 for m in mappings if m.evidence_strength == "Explicit Evidence")
        strong = sum(1 for m in mappings if m.evidence_strength == "Strong Inference")
        weak = sum(1 for m in mappings if m.evidence_strength == "Weak Inference")
        none = sum(1 for m in mappings if m.evidence_strength == "No Evidence")
        return AgentResult(
            agent_id=self.agent_id,
            output=evidence_map,
            metrics={
                "total": len(mappings),
                "explicit": explicit,
                "strong_inference": strong,
                "weak_inference": weak,
                "no_evidence": none,
            },
        )


def run_evidence_mapping(
    *,
    resume_facts: dict[str, Any],
    job_profile: Any,
    inferred: list[Any] | None = None,
    knowledge_base: Any = None,
    use_cache: bool = True,
    language: str = "en",
) -> AgentResult[EvidenceMap]:
    return EvidenceMappingAgent().run(
        EvidenceMappingInput(
            resume_facts=resume_facts,
            job_profile=job_profile,
            inferred=inferred or [],
            knowledge_base=knowledge_base,
        ),
        AgentContext(use_cache=use_cache, language=language),
    )
