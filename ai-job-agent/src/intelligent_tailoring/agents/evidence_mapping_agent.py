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
    strength_to_match_type,
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
    "proven ability to lead projects from inception to deployment",
    "over three years of expertise",
    "customer satisfaction",
    "system scalability",
    "system reliability",
    "production-grade ownership",
)

# Safe inference patterns (context-preserving). Never upgrade seniority.
_SAFE_INFERENCE_HINTS = (
    ("postgresql", "relational database experience"),
    ("sqlite", "relational database experience"),
    ("react", "component-based frontend experience"),
    ("angular", "component-based frontend experience"),
    ("laravel", "familiarity with the PHP ecosystem"),
    ("tutoring", "communication and explaining complex concepts"),
    ("capstone", "academic end-to-end development experience"),
)


def _fact_ids_for_evidence(kb: Any, supporting: str) -> list[str]:
    if kb is None or not hasattr(kb, "facts") or not supporting:
        return []
    low = supporting.lower()
    ids: list[str] = []
    for fact in kb.facts:
        original = str(getattr(fact, "original_text", "") or "").lower()
        fid = str(getattr(fact, "id", "") or "")
        if not fid or not original:
            continue
        if original in low or low[:48] in original or any(
            t.lower() in low for t in (getattr(fact, "technologies", None) or [])
        ):
            if fid not in ids:
                ids.append(fid)
        if len(ids) >= 8:
            break
    return ids


def _scope_valid_for_match(kb: Any, fact_ids: list[str], requirement: str) -> bool:
    """False when evidence is only a general skill but claim implies project usage."""
    if not fact_ids or kb is None or not hasattr(kb, "fact_by_id"):
        return True
    req_l = (requirement or "").lower()
    # TypeScript is never proven by React alone
    if "typescript" in req_l or "type script" in req_l:
        for fid in fact_ids:
            fact = kb.fact_by_id(fid)
            if fact and "typescript" in str(fact.original_text).lower():
                return True
        return False
    return True


def _limitations_for(
    match_type: str,
    requirement: str,
    supporting: str,
    fact_ids: list[str],
    kb: Any,
) -> list[str]:
    limits: list[str] = []
    req_l = (requirement or "").lower()
    if match_type in ("Unsupported", "Weak"):
        limits.append(f"No strong evidence for {requirement}")
    if match_type == "Transferable":
        limits.append("Transferable only — do not claim direct professional mastery")
    # Capstone / academic context
    academic = False
    if kb is not None and hasattr(kb, "fact_by_id"):
        for fid in fact_ids:
            fact = kb.fact_by_id(fid)
            if fact and str(getattr(fact, "context_type", "") or "") == "academic":
                academic = True
                break
    if academic or "capstone" in (supporting or "").lower():
        limits.append("Evidence is academic (capstone) — preserve academic context")
    if "typescript" in req_l:
        limits.append("React does not prove TypeScript")
    if "3+" in req_l or "three year" in req_l or "3 year" in req_l:
        limits.append("Do not invent years of experience to close this gap")
    return limits[:6]


def _safe_and_unsafe(
    *,
    requirement: str,
    supporting: str,
    match_type: str,
    statement: str,
    limitations: list[str],
) -> tuple[list[str], list[str]]:
    safe: list[str] = []
    unsafe: list[str] = list(_FORBIDDEN_GENERIC)
    if supporting and match_type in ("Explicit", "Strongly Supported", "Transferable"):
        snippet = supporting.strip()
        if len(snippet) > 160:
            snippet = snippet[:157].rstrip() + "..."
        # Preserve academic framing in safe claims
        if any("academic" in l.lower() for l in limitations):
            if "academic" not in snippet.lower() and "capstone" not in snippet.lower():
                snippet = f"Academic project evidence: {snippet}"
        safe.append(snippet)
    if match_type in ("Explicit", "Strongly Supported") and requirement:
        safe.append(requirement)
    if statement and match_type in ("Explicit", "Strongly Supported"):
        if statement not in safe:
            safe.append(statement)
    # Promote known safe inference hints when evidence tokens match
    support_l = (supporting or "").lower()
    for token, claim in _SAFE_INFERENCE_HINTS:
        if token in support_l and match_type in (
            "Explicit",
            "Strongly Supported",
            "Transferable",
        ):
            if claim not in safe:
                safe.append(claim)
    if match_type == "Unsupported":
        unsafe.append(requirement)
        unsafe.append(f"experienced in {requirement}")
    unsafe.extend(
        [
            f"years of experience with {requirement}",
            f"expert in {requirement}",
            f"proven ability with {requirement}",
        ]
    )
    # Dedupe
    def _dedupe(items: list[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            if item and item not in out:
                out.append(item)
        return out

    return _dedupe(safe)[:8], _dedupe(unsafe)[:16]


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

        # Run semantic inference if caller did not supply it (keeps agent self-contained).
        # Four-agent pipeline already ran inference inside Agent 1 — skip when the
        # caller explicitly marks inference as completed (even if empty).
        inference_done = bool((context.metadata or {}).get("inference_completed"))
        if not inferred and not inference_done:
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
        for idx, entry in enumerate(legacy):
            category = str(entry.get("inference_category") or "Unsupported")
            status = str(entry.get("candidate_status") or "MISSING")
            strength = normalize_evidence_strength(category, status)
            match_type = strength_to_match_type(strength, category)
            # PARTIAL + weak ontology → Transferable rather than Strongly Supported
            if status == "PARTIAL" and match_type == "Strongly Supported":
                if "weak" in category.lower() or "transfer" in category.lower():
                    match_type = "Transferable"
            requirement = str(entry.get("requirement") or "")
            supporting = str(entry.get("supporting_evidence") or "")
            statement = str(entry.get("generated_statement") or "")
            source = _source_location(
                payload.knowledge_base, supporting, payload.resume_facts
            )
            fact_ids = _fact_ids_for_evidence(payload.knowledge_base, supporting)
            scope_ok = _scope_valid_for_match(
                payload.knowledge_base, fact_ids, requirement
            )
            if not scope_ok and match_type != "Unsupported":
                match_type = "Unsupported"
                status = "MISSING"
            limitations = _limitations_for(
                match_type, requirement, supporting, fact_ids, payload.knowledge_base
            )
            safe_claims, unsafe_claims = _safe_and_unsafe(
                requirement=requirement,
                supporting=supporting,
                match_type=match_type,
                statement=statement,
                limitations=limitations,
            )
            recommended: list[str] = []
            if match_type in ("Explicit", "Strongly Supported"):
                recommended = ["skills", "projects", "summary"]
            elif match_type == "Transferable":
                recommended = ["summary"]
            allowed = _allowed_wording(
                requirement=requirement,
                supporting=supporting,
                strength=strength,
                statement=statement,
            )
            # Prefer structured safe_claims as allowed wording
            for claim in safe_claims:
                if claim not in allowed:
                    allowed.append(claim)
            forbidden = _forbidden_wording(requirement, strength)
            for claim in unsafe_claims:
                if claim not in forbidden:
                    forbidden.append(claim)
            mappings.append(
                EvidenceMapping(
                    requirement=requirement,
                    evidence_strength=strength,
                    candidate_status=status,
                    importance=str(entry.get("importance") or "soft"),
                    source_location=source,
                    supporting_evidence=supporting,
                    confidence=float(entry.get("confidence_score") or 0.0),
                    allowed_wording=allowed[:8],
                    forbidden_wording=forbidden[:20],
                    inference_category=category,
                    ontology_rule_id=str(entry.get("ontology_rule_id") or ""),
                    generated_statement=statement,
                    requirement_id=str(entry.get("requirement_id") or f"req_{idx+1}"),
                    resume_fact_ids=fact_ids,
                    match_type=match_type,
                    scope_valid=scope_ok,
                    safe_claims=safe_claims,
                    unsafe_claims=unsafe_claims,
                    limitations=limitations,
                    recommended_sections=recommended,
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
                "match_types": {
                    "Explicit": sum(1 for m in mappings if m.match_type == "Explicit"),
                    "Strongly Supported": sum(
                        1 for m in mappings if m.match_type == "Strongly Supported"
                    ),
                    "Transferable": sum(
                        1 for m in mappings if m.match_type == "Transferable"
                    ),
                    "Weak": sum(1 for m in mappings if m.match_type == "Weak"),
                    "Unsupported": sum(
                        1 for m in mappings if m.match_type == "Unsupported"
                    ),
                },
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
