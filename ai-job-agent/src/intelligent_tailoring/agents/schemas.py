"""Typed schemas for multi-agent resume intelligence.

All inter-agent communication uses these structured objects.
Profession-agnostic — no software-specific hardcoding.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EvidenceStrength = Literal[
    "Explicit Evidence",
    "Strong Inference",
    "Weak Inference",
    "No Evidence",
]

ClaimDecision = Literal["Accept", "Rewrite from evidence", "Regenerate", "Reject"]

UNKNOWN = "Unknown"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


# ---------------------------------------------------------------------------
# Agent 1 — Resume Knowledge
# ---------------------------------------------------------------------------


@dataclass
class ResumeKnowledgeInput:
    cv_profile: dict[str, Any]
    source_documents: str | None = None
    target_output_language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cv_profile": dict(self.cv_profile or {}),
            "source_documents": self.source_documents,
            "target_output_language": self.target_output_language,
        }


@dataclass
class ResumeKnowledgeOutput:
    """Canonical resume facts. Never contains generated/tailored prose."""

    knowledge_base: Any  # ResumeKnowledgeBase
    resume_facts: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    source_language: str = "en"
    fact_count: int = 0
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kb = self.knowledge_base
        return {
            "knowledge_base": kb.to_dict() if hasattr(kb, "to_dict") else {},
            "resume_facts": dict(self.resume_facts),
            "content_hash": self.content_hash,
            "source_language": self.source_language,
            "fact_count": self.fact_count,
            "coverage": dict(self.coverage),
        }


# ---------------------------------------------------------------------------
# Agent 2 — Job Intelligence
# ---------------------------------------------------------------------------


@dataclass
class ScoredRequirement:
    text: str
    category: str = "skill"  # skill|responsibility|technology|methodology|soft|education|...
    required_or_preferred: str = "required"
    importance_score: float = 0.5
    confidence: float = 0.5
    industry_terminology: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JobProfile:
    """Structured understanding of a job description. Never resume text."""

    title: str = ""
    company: str = ""
    responsibilities: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    methodologies: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    experience_expectations: list[str] = field(default_factory=list)
    industry_terminology: list[str] = field(default_factory=list)
    leadership_expectations: list[str] = field(default_factory=list)
    learning_expectations: list[str] = field(default_factory=list)
    communication_expectations: list[str] = field(default_factory=list)
    cloud: list[str] = field(default_factory=list)
    security: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    compliance: list[str] = field(default_factory=list)
    customer_interaction: list[str] = field(default_factory=list)
    business_domain: list[str] = field(default_factory=list)
    scored_requirements: list[ScoredRequirement] = field(default_factory=list)
    seniority_level: str = ""
    ats_keywords: list[str] = field(default_factory=list)
    language: str = "en"
    jd_text: str = ""
    # Legacy-compatible bag used by existing stages
    raw_requirements: dict[str, Any] = field(default_factory=dict)
    job_family: str = "general"
    industry: str = "general"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scored_requirements"] = [s.to_dict() for s in self.scored_requirements]
        return data

    def to_legacy_requirements(self) -> dict[str, Any]:
        """Bridge to existing stage contracts."""
        if self.raw_requirements:
            base = dict(self.raw_requirements)
        else:
            base = {}
        base.setdefault("required_skills", list(self.required_skills))
        base.setdefault("preferred_skills", list(self.preferred_skills))
        base.setdefault("hard_requirements", list(self.required_skills))
        base.setdefault("soft_requirements", list(self.preferred_skills))
        base.setdefault("responsibilities", list(self.responsibilities))
        base.setdefault("tools_technologies", list(self.technologies))
        base.setdefault("industry_terminology", list(self.industry_terminology))
        base.setdefault("soft_skills", list(self.soft_skills))
        base.setdefault("education_certifications", list(self.education))
        base.setdefault("ats_keywords", list(self.ats_keywords))
        base.setdefault("seniority_level", self.seniority_level)
        base.setdefault("language", self.language)
        base.setdefault("methodologies", list(self.methodologies))
        base.setdefault("leadership_expectations", list(self.leadership_expectations))
        base.setdefault("learning_expectations", list(self.learning_expectations))
        base.setdefault("communication_expectations", list(self.communication_expectations))
        base.setdefault("cloud", list(self.cloud))
        base.setdefault("security", list(self.security))
        base.setdefault("databases", list(self.databases))
        base.setdefault("frameworks", list(self.frameworks))
        base.setdefault("compliance", list(self.compliance))
        base.setdefault("customer_interaction", list(self.customer_interaction))
        base.setdefault("business_domain", list(self.business_domain))
        base.setdefault("experience_expectations", list(self.experience_expectations))
        if self.jd_text:
            base["jd_text"] = self.jd_text
        return base


@dataclass
class JobIntelligenceInput:
    job: dict[str, Any]
    jd_snapshot: str | None = None
    existing_requirements: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": dict(self.job or {}),
            "jd_snapshot": self.jd_snapshot,
            "existing_requirements": self.existing_requirements,
        }


# ---------------------------------------------------------------------------
# Agent 3 — Company Intelligence
# ---------------------------------------------------------------------------


@dataclass
class CompanyProfile:
    """Employer understanding from JD + available metadata only. Never invents."""

    company_name: str = UNKNOWN
    industry: str = UNKNOWN
    business_model: str = UNKNOWN
    product_type: str = UNKNOWN
    technology_focus: list[str] = field(default_factory=list)
    engineering_culture: str = UNKNOWN
    customer_type: str = UNKNOWN
    innovation_level: str = UNKNOWN
    cloud_maturity: str = UNKNOWN
    security_focus: str = UNKNOWN
    ai_usage: str = UNKNOWN
    product_scale: str = UNKNOWN
    preferred_candidate_traits: list[str] = field(default_factory=list)
    communication_style: str = UNKNOWN
    learning_culture: str = UNKNOWN
    business_priorities: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompanyIntelligenceInput:
    job: dict[str, Any]
    job_profile: JobProfile | None = None
    jd_snapshot: str = ""
    company_metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": dict(self.job or {}),
            "job_profile": self.job_profile.to_dict() if self.job_profile else None,
            "jd_snapshot": self.jd_snapshot,
            "company_metadata": dict(self.company_metadata or {}),
        }


# ---------------------------------------------------------------------------
# Agent 4 — Evidence Mapping
# ---------------------------------------------------------------------------


@dataclass
class EvidenceMapping:
    requirement: str
    evidence_strength: EvidenceStrength
    candidate_status: str  # MATCH|PARTIAL|MISSING
    importance: str  # hard|soft
    source_location: str = ""
    supporting_evidence: str = ""
    confidence: float = 0.0
    allowed_wording: list[str] = field(default_factory=list)
    forbidden_wording: list[str] = field(default_factory=list)
    inference_category: str = "Unsupported"
    ontology_rule_id: str = ""
    generated_statement: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence_score"] = self.confidence
        data["requirement"] = self.requirement
        return data


@dataclass
class EvidenceMap:
    mappings: list[EvidenceMapping] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"mappings": [m.to_dict() for m in self.mappings]}

    def to_legacy_list(self) -> list[dict[str, Any]]:
        """Legacy evidence_map list shape used throughout the pipeline."""
        out: list[dict[str, Any]] = []
        for m in self.mappings:
            out.append(
                {
                    "requirement": m.requirement,
                    "importance": m.importance,
                    "candidate_status": m.candidate_status,
                    "inference_category": m.inference_category,
                    "supporting_evidence": m.supporting_evidence,
                    "generated_statement": m.generated_statement,
                    "confidence_score": m.confidence,
                    "ontology_rule_id": m.ontology_rule_id,
                    "evidence_strength": m.evidence_strength,
                    "source_location": m.source_location,
                    "allowed_wording": list(m.allowed_wording),
                    "forbidden_wording": list(m.forbidden_wording),
                }
            )
        return out


@dataclass
class EvidenceMappingInput:
    resume_facts: dict[str, Any]
    job_profile: JobProfile
    inferred: list[Any] = field(default_factory=list)
    knowledge_base: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resume_facts": dict(self.resume_facts),
            "job_profile": self.job_profile.to_dict(),
            "inferred_count": len(self.inferred),
        }


# ---------------------------------------------------------------------------
# Agent 5 — Resume Strategy
# ---------------------------------------------------------------------------


@dataclass
class ResumeStrategy:
    summary_focus: str = ""
    project_order: list[str] = field(default_factory=list)
    experience_order: list[str] = field(default_factory=list)
    skills_priority: list[str] = field(default_factory=list)
    section_order: list[str] = field(default_factory=list)
    important_evidence: list[str] = field(default_factory=list)
    low_priority_evidence: list[str] = field(default_factory=list)
    hidden_evidence: list[str] = field(default_factory=list)
    safe_inferences: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    requirement_coverage: dict[str, str] = field(default_factory=dict)
    company_influenced_priorities: list[str] = field(default_factory=list)
    # Full legacy strategy bag for rewrite / rebuild services
    legacy_strategy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    def to_legacy(self) -> dict[str, Any]:
        base = dict(self.legacy_strategy) if self.legacy_strategy else {}
        base.setdefault("summary_focus", self.summary_focus)
        base.setdefault("project_priority", list(self.project_order))
        base.setdefault("experience_order", list(self.experience_order))
        base.setdefault("skills_to_emphasize", list(self.skills_priority))
        base.setdefault("section_order", list(self.section_order))
        base.setdefault("facts_to_expand", list(self.important_evidence))
        base.setdefault("facts_to_condense", list(self.low_priority_evidence))
        base.setdefault("facts_to_omit", list(self.hidden_evidence))
        base.setdefault("safe_inferences", list(self.safe_inferences))
        base.setdefault("forbidden_claims", list(self.forbidden_claims))
        base.setdefault("requirement_coverage", dict(self.requirement_coverage))
        base.setdefault(
            "company_influenced_priorities", list(self.company_influenced_priorities)
        )
        return base


@dataclass
class ResumeStrategyInput:
    job_profile: JobProfile
    company_profile: CompanyProfile
    evidence_map: EvidenceMap
    resume_facts: dict[str, Any]
    ranked_requirements: list[dict[str, Any]] = field(default_factory=list)
    fact_scores: list[dict[str, Any]] | None = None
    job_analysis: dict[str, Any] | None = None
    language: str = "en"

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_profile": self.job_profile.to_dict(),
            "company_profile": self.company_profile.to_dict(),
            "evidence_map": self.evidence_map.to_dict(),
            "language": self.language,
        }


# ---------------------------------------------------------------------------
# Agent 6 — Resume Tailoring (content selection only)
# ---------------------------------------------------------------------------


@dataclass
class TailoredStructure:
    """Content selection result — not wording-optimized."""

    professional_title: str = ""
    professional_summary: str = ""
    skills: list[str] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    certifications: list[Any] = field(default_factory=list)
    matched_requirements: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    change_log: list[dict[str, Any]] = field(default_factory=list)
    ats_keywords_added: list[str] = field(default_factory=list)
    removed_or_deprioritized_content: list[str] = field(default_factory=list)
    raw_generation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "professional_title": self.professional_title,
            "professional_summary": self.professional_summary,
            "skills": list(self.skills),
            "experience": list(self.experience),
            "projects": list(self.projects),
            "education": list(self.education),
            "certifications": list(self.certifications),
            "matched_requirements": list(self.matched_requirements),
            "missing_requirements": list(self.missing_requirements),
            "change_log": list(self.change_log),
            "ats_keywords_added": list(self.ats_keywords_added),
            "removed_or_deprioritized_content": list(
                self.removed_or_deprioritized_content
            ),
        }

    def as_resume_dict(self) -> dict[str, Any]:
        return {
            "professional_title": self.professional_title,
            "professional_summary": self.professional_summary,
            "summary": self.professional_summary,
            "skills": list(self.skills),
            "experience": list(self.experience),
            "projects": list(self.projects),
            "education": list(self.education),
            "certifications": list(self.certifications),
        }


@dataclass
class TailoringAgentInput:
    knowledge: ResumeKnowledgeOutput
    job_profile: JobProfile
    company_profile: CompanyProfile
    evidence_map: EvidenceMap
    strategy: ResumeStrategy
    ranked_requirements: list[dict[str, Any]] = field(default_factory=list)
    inferred: list[Any] = field(default_factory=list)
    triage: dict[str, Any] = field(default_factory=dict)
    rebuilt_resume: dict[str, Any] = field(default_factory=dict)
    content_scores: dict[str, Any] = field(default_factory=dict)
    language: str = "en"
    regeneration_attempt: int = 0


# ---------------------------------------------------------------------------
# Agent 7 — Claim Validation
# ---------------------------------------------------------------------------


@dataclass
class ClaimValidationItem:
    statement: str
    decision: ClaimDecision
    reason: str = ""
    rewritten_text: str = ""
    source_location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClaimValidationResult:
    cleaned_resume: dict[str, Any] = field(default_factory=dict)
    decisions: list[ClaimValidationItem] = field(default_factory=list)
    rejected_statements: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    inferred_competencies: list[Any] = field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "cleaned_resume": dict(self.cleaned_resume),
            "decisions": [d.to_dict() for d in self.decisions],
            "rejected_statements": list(self.rejected_statements),
            "warnings": list(self.warnings),
            "inferred_competencies": list(self.inferred_competencies),
            "passed": self.passed,
        }


@dataclass
class ClaimValidationInput:
    original_resume_text: str
    tailored_resume: dict[str, Any]
    evidence_map: EvidenceMap
    change_log: list[dict[str, Any]] = field(default_factory=list)
    inferred: list[Any] = field(default_factory=list)
    job_profile: JobProfile | None = None


# ---------------------------------------------------------------------------
# Agent 8 — Human Resume Writer
# ---------------------------------------------------------------------------


@dataclass
class HumanWriterInput:
    validated_resume: dict[str, Any]
    strategy: ResumeStrategy | None = None
    knowledge_base: Any = None
    output_language: str = "en"


@dataclass
class HumanWriterOutput:
    tailored_resume: dict[str, Any] = field(default_factory=dict)
    mode: str = "passthrough"
    facts_unchanged: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "tailored_resume": dict(self.tailored_resume),
            "mode": self.mode,
            "facts_unchanged": self.facts_unchanged,
        }


# ---------------------------------------------------------------------------
# Agent 9 — Senior Recruiter Review
# ---------------------------------------------------------------------------


@dataclass
class RecruiterReviewInput:
    resume: dict[str, Any]
    output_language: str = "en"


@dataclass
class RecruiterReviewOutput:
    would_interview: bool = False
    communicates_value: bool = False
    sounds_robotic: bool = False
    bullets_concise: bool = False
    achievements_clear: bool = False
    sections_to_strengthen: list[str] = field(default_factory=list)
    approved: bool = False
    human_believability: int = 0
    interview_quality: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)
    summary_feedback: str = ""
    sections_to_regenerate: list[str] = field(default_factory=list)
    raw_review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


# ---------------------------------------------------------------------------
# Agent 10 — Hiring Manager Simulation
# ---------------------------------------------------------------------------


@dataclass
class HiringManagerInput:
    resume: dict[str, Any]
    job_profile: JobProfile
    company_profile: CompanyProfile
    evidence_map: EvidenceMap
    strategy: ResumeStrategy | None = None


@dataclass
class HiringManagerFeedback:
    overall_fit: int = 0
    technical_fit: int = 0
    business_fit: int = 0
    communication: int = 0
    resume_quality: int = 0
    evidence_quality: int = 0
    missing_evidence: list[str] = field(default_factory=list)
    section_effectiveness: dict[str, int] = field(default_factory=dict)
    why_interview: list[str] = field(default_factory=list)
    why_reject: list[str] = field(default_factory=list)
    strongest_sections: list[str] = field(default_factory=list)
    weakest_sections: list[str] = field(default_factory=list)
    actionable_feedback: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_evidence_strength(category: str, status: str) -> EvidenceStrength:
    cat = (category or "").strip().lower().replace("_", " ")
    st = (status or "").strip().upper()
    if cat == "explicit" or (st == "MATCH" and "infer" not in cat):
        if st == "MATCH":
            return "Explicit Evidence"
    if cat in ("strongly inferred", "strong inference"):
        return "Strong Inference"
    if cat in ("weakly inferred", "weak inference"):
        return "Weak Inference"
    if st == "MISSING" or cat in ("unsupported", "no evidence", ""):
        return "No Evidence"
    if st == "PARTIAL":
        return "Strong Inference" if "strong" in cat else "Weak Inference"
    if st == "MATCH":
        return "Explicit Evidence"
    return "No Evidence"


def enrich_lists(*groups: list[str]) -> list[str]:
    out: list[str] = []
    for group in groups:
        for item in _as_str_list(group):
            if item not in out:
                out.append(item)
    return out
