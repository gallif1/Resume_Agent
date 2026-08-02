"""Agent 2 — Job Intelligence Agent.

Responsibility: understand what type of person the company is actually hiring,
not just extract keywords. Infers hiring priorities and narrative themes.
Never generates resume text.
"""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    JobIntelligenceInput,
    JobProfile,
    ScoredRequirement,
    enrich_lists,
)
from intelligent_tailoring.hiring_intent import infer_hiring_intent
from intelligent_tailoring.services.job_analyzer import analyze_job
from intelligent_tailoring.services.job_family import detect_industry, detect_job_family
from intelligent_tailoring.stages.deterministic_job_extraction import (
    extract_job_requirements_deterministic,
)
from match_tailor_service import build_job_payload
from job_analyzer import parse_stored_job_profile

# Profession-agnostic cue buckets (keywords may appear in any domain).
_CUE_BUCKETS: dict[str, tuple[str, ...]] = {
    "methodologies": (
        "agile", "scrum", "kanban", "lean", "six sigma", "waterfall",
        "design thinking", "okr", "kpi", "continuous improvement", "itil",
        "devops", "ci/cd", "tdd", "bdd",
    ),
    "leadership": (
        "lead", "manage", "mentor", "supervise", "coach", "direct",
        "own", "ownership", "stakeholder", "cross-functional",
    ),
    "learning": (
        "learn", "grow", "curious", "training", "upskill", "mentorship",
        "continuous learning", "development plan",
    ),
    "communication": (
        "present", "communicate", "written", "verbal", "collaborate",
        "client-facing", "stakeholder management", "documentation",
    ),
    "cloud": (
        "aws", "azure", "gcp", "cloud", "saas", "paas", "iaas",
        "kubernetes", "serverless",
    ),
    "security": (
        "security", "secure", "iam", "encryption", "vulnerability",
        "soc2", "iso 27001", "penetration", "zero trust", "hipaa",
    ),
    "databases": (
        "sql", "postgres", "mysql", "mongodb", "redis", "oracle",
        "database", "data warehouse", "snowflake", "bigquery",
    ),
    "frameworks": (
        "framework", "react", "angular", "django", "spring", "rails",
        ".net", "flask", "fastapi", "tensorflow", "pytorch",
    ),
    "compliance": (
        "compliance", "gdpr", "hipaa", "sox", "pci", "audit", "regulation",
        "policy", "quality assurance", "fda", "iso",
    ),
    "customer_interaction": (
        "customer", "client", "patient", "guest", "account", "support",
        "service desk", "front desk", "retail", "sales",
    ),
}


def _bucket_hits(texts: list[str], cues: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for text in texts:
        low = text.lower()
        for cue in cues:
            if cue in low and text not in hits:
                hits.append(text)
                break
    return hits


# Central themes that raise screening weight / daily-work centrality.
_SCREENING_CUES = (
    "typescript", "react", "full stack", "fullstack", "3+", "three year",
    "api", "testing", "deployment", "monitoring", "ai-assisted", "cursor",
    "chatgpt", "claude", "copilot", "production", "clean code",
)
_SENIORITY_CUES = (
    "3+", "3 year", "three year", "5+", "5 year", "senior", "lead", "principal",
    "staff", "architect",
)


def _score_requirement(
    text: str,
    *,
    required: bool,
    index: int,
    total: int,
    category: str,
) -> ScoredRequirement:
    # Earlier + required → higher importance; confidence higher for concrete tokens.
    position = 1.0 - (index / max(total, 1)) * 0.4
    importance = (0.85 if required else 0.45) * position
    tokenish = bool(re.search(r"[A-Za-z0-9]{2,}", text))
    confidence = 0.9 if tokenish else 0.55
    low = text.lower()
    screening = 0.5
    if any(c in low for c in _SCREENING_CUES):
        screening = 0.9 if required else 0.7
    elif required:
        screening = 0.75
    seniority = 0.0
    if any(c in low for c in _SENIORITY_CUES):
        seniority = 0.85 if required else 0.55
    # Evidence expected hint
    if category in ("frameworks", "cloud", "databases") or any(
        t in low for t in ("react", "python", "aws", "sql", "api")
    ):
        evidence_expected = "project_or_employment_bullet"
    elif category == "experience" or seniority > 0:
        evidence_expected = "dated_professional_roles"
    elif category in ("soft", "communication", "leadership"):
        evidence_expected = "behavioral_or_activity_evidence"
    else:
        evidence_expected = "explicit_skill_or_bullet"
    return ScoredRequirement(
        text=text,
        category=category,
        required_or_preferred="required" if required else "preferred",
        importance_score=round(min(1.0, importance), 3),
        confidence=confidence,
        id=f"req_{abs(hash(text.lower())) % 10_000_000}",
        normalized_competency=text.strip(),
        screening_weight=round(screening, 3),
        seniority_impact=round(seniority, 3),
        evidence_expected=evidence_expected,
        synonyms=[],
    )


def _classify_category(text: str) -> str:
    low = text.lower()
    for bucket, cues in _CUE_BUCKETS.items():
        if any(c in low for c in cues):
            return bucket
    if any(w in low for w in ("degree", "bachelor", "master", "certif", "license")):
        return "education"
    if any(w in low for w in ("year", "experience", "senior", "junior")):
        return "experience"
    if any(w in low for w in ("responsib", "own", "deliver", "manage")):
        return "responsibility"
    return "skill"


class JobIntelligenceAgent(Agent[JobIntelligenceInput, JobProfile]):
    agent_id = "job_intelligence"
    responsibility = (
        "Infer who the company wants to hire and extract structured JobProfile"
    )

    def run(
        self,
        payload: JobIntelligenceInput,
        context: AgentContext | None = None,
    ) -> AgentResult[JobProfile]:
        context = context or AgentContext()
        job = payload.job or {}
        requirements = payload.existing_requirements
        if requirements is None:
            requirements = extract_job_requirements_deterministic(
                job,
                jd_snapshot=payload.jd_snapshot,
            )

        analysis = analyze_job(
            job,
            use_cache=context.use_cache,
            jd_snapshot=payload.jd_snapshot,
            requirements=requirements,
        )

        required = list(
            requirements.get("hard_requirements")
            or requirements.get("required_skills")
            or []
        )
        preferred = list(
            requirements.get("soft_requirements")
            or requirements.get("preferred_skills")
            or []
        )
        tools = list(requirements.get("tools_technologies") or [])
        responsibilities = list(requirements.get("responsibilities") or [])
        soft = list(requirements.get("soft_skills") or [])
        education = list(requirements.get("education_certifications") or [])
        terminology = list(requirements.get("industry_terminology") or [])
        ats = list(requirements.get("ats_keywords") or [])

        pool = enrich_lists(required, preferred, tools, responsibilities, soft, terminology, ats)
        methodologies = _bucket_hits(pool, _CUE_BUCKETS["methodologies"])
        leadership = _bucket_hits(pool, _CUE_BUCKETS["leadership"])
        learning = _bucket_hits(pool, _CUE_BUCKETS["learning"])
        communication = _bucket_hits(pool, _CUE_BUCKETS["communication"])
        cloud = _bucket_hits(pool, _CUE_BUCKETS["cloud"])
        security = _bucket_hits(pool, _CUE_BUCKETS["security"])
        databases = _bucket_hits(pool, _CUE_BUCKETS["databases"])
        frameworks = _bucket_hits(pool, _CUE_BUCKETS["frameworks"])
        compliance = _bucket_hits(pool, _CUE_BUCKETS["compliance"])
        customer = _bucket_hits(pool, _CUE_BUCKETS["customer_interaction"])

        # Experience / business domain from responsibilities + title
        experience_expectations = [
            r for r in responsibilities + required
            if any(w in str(r).lower() for w in ("year", "experience", "senior", "junior"))
        ]
        business_domain = list(
            dict.fromkeys(
                [str(analysis.get("industry") or "").strip()]
                + [t for t in terminology if t]
            )
        )
        business_domain = [b for b in business_domain if b and b.lower() != "general"]

        scored: list[ScoredRequirement] = []
        for i, text in enumerate(required):
            scored.append(
                _score_requirement(
                    str(text),
                    required=True,
                    index=i,
                    total=len(required),
                    category=_classify_category(str(text)),
                )
            )
        for i, text in enumerate(preferred):
            scored.append(
                _score_requirement(
                    str(text),
                    required=False,
                    index=i,
                    total=len(preferred),
                    category=_classify_category(str(text)),
                )
            )

        title = str(job.get("title") or "")
        company = str(job.get("company") or "")
        job_family = str(
            analysis.get("job_family") or detect_job_family(title, requirements)
        )
        industry = str(analysis.get("industry") or detect_industry(title, requirements))

        jd_text = payload.jd_snapshot or str(requirements.get("jd_text") or "")
        if not jd_text:
            stored = parse_stored_job_profile(job.get("job_profile"))
            jd_text = build_job_payload(job, stored)

        intent = infer_hiring_intent(
            title=title,
            job_family=job_family,
            responsibilities=[str(x) for x in responsibilities],
            required_skills=[str(x) for x in required],
            soft_skills=[str(x) for x in soft],
            leadership_expectations=leadership,
            learning_expectations=learning,
            communication_expectations=communication,
            customer_interaction=customer,
            jd_text=jd_text,
            business_priorities=business_domain,
        )

        profile = JobProfile(
            title=title,
            company=company,
            responsibilities=[str(x) for x in responsibilities],
            required_skills=[str(x) for x in required],
            preferred_skills=[str(x) for x in preferred],
            technologies=[str(x) for x in tools],
            methodologies=methodologies,
            soft_skills=[str(x) for x in soft],
            education=[str(x) for x in education],
            experience_expectations=[str(x) for x in experience_expectations],
            industry_terminology=[str(x) for x in terminology],
            leadership_expectations=leadership,
            learning_expectations=learning,
            communication_expectations=communication,
            cloud=cloud,
            security=security,
            databases=databases,
            frameworks=frameworks,
            compliance=compliance,
            customer_interaction=customer,
            business_domain=business_domain,
            scored_requirements=scored,
            seniority_level=str(requirements.get("seniority_level") or ""),
            ats_keywords=[str(x) for x in ats],
            language=str(requirements.get("language") or context.language or "en"),
            jd_text=jd_text,
            raw_requirements=dict(requirements),
            job_family=job_family,
            industry=industry,
            person_archetype=str(intent.get("person_archetype") or ""),
            problem_to_solve=str(intent.get("problem_to_solve") or ""),
            hiring_priorities=list(intent.get("hiring_priorities") or []),
            narrative_themes=list(intent.get("narrative_themes") or []),
            hiring_signals=list(intent.get("must_signal_traits") or []),
            interview_screening_focus=list(
                intent.get("interview_screening_focus") or []
            ),
            interview_lens=str(intent.get("interview_lens") or ""),
            hiring_intent=dict(intent),
        )
        return AgentResult(
            agent_id=self.agent_id,
            output=profile,
            metrics={
                "required_count": len(required),
                "preferred_count": len(preferred),
                "scored_count": len(scored),
                "job_family": job_family,
                "industry": industry,
                "person_archetype": profile.person_archetype,
                "hiring_priorities_count": len(profile.hiring_priorities),
            },
        )


def run_job_intelligence(
    job: dict[str, Any],
    *,
    jd_snapshot: str | None = None,
    use_cache: bool = True,
    language: str = "en",
) -> AgentResult[JobProfile]:
    return JobIntelligenceAgent().run(
        JobIntelligenceInput(job=job, jd_snapshot=jd_snapshot),
        AgentContext(use_cache=use_cache, language=language),
    )
