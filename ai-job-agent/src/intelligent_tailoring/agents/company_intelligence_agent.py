"""Agent 3 — Company Intelligence Agent.

Responsibility: understand the employer from JD + available metadata only.
Never fabricates company information. Unknown stays Unknown.
"""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.agents.base import Agent, AgentContext, AgentResult
from intelligent_tailoring.agents.schemas import (
    UNKNOWN,
    CompanyIntelligenceInput,
    CompanyProfile,
    JobProfile,
)

_INDUSTRY_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("Healthcare", ("hospital", "patient", "clinical", "healthcare", "medical", "pharma")),
    ("Finance", ("bank", "fintech", "trading", "investment", "insurance", "payments")),
    ("Education", ("school", "university", "student", "curriculum", "edtech", "teacher")),
    ("Retail", ("retail", "store", "merchandise", "e-commerce", "ecommerce", "pos")),
    ("Hospitality", ("hotel", "hospitality", "restaurant", "guest", "resort")),
    ("Manufacturing", ("manufactur", "factory", "plant", "assembly", "production line")),
    ("Construction", ("construction", "site supervisor", "contractor", "building")),
    ("Government", ("government", "public sector", "municipal", "federal", "civic")),
    ("Legal", ("law firm", "legal counsel", "litigation", "attorney", "paralegal")),
    ("Technology", ("software", "saas", "platform", "cloud", "developer", "engineering")),
    ("Marketing", ("marketing", "brand", "campaign", "seo", "content marketing")),
    ("Human Resources", ("human resources", "talent acquisition", "hrbp", "people ops")),
    ("Operations", ("operations", "supply chain", "logistics", "fulfillment")),
    ("Customer Service", ("customer service", "call center", "support center", "help desk")),
    ("Sales", ("quota", "pipeline", "account executive", "b2b sales", "revenue")),
]

_BUSINESS_MODEL_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("B2B SaaS", ("b2b", "saas", "subscription", "enterprise software")),
    ("B2C", ("b2c", "consumer", "retail customer", "end user")),
    ("Marketplace", ("marketplace", "two-sided", "platform connecting")),
    ("Agency", ("agency", "clients", "retainer", "campaigns for")),
    ("Non-profit", ("non-profit", "nonprofit", "ngo", "charity")),
    ("Public sector", ("government", "public sector", "municipal")),
]

_PRODUCT_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("Software platform", ("platform", "saas", "api", "application", "product")),
    ("Physical goods", ("manufactur", "hardware", "product line", "sku")),
    ("Professional services", ("consulting", "advisory", "services firm")),
    ("Care services", ("patient care", "clinical services", "treatment")),
    ("Education programs", ("curriculum", "courses", "degree program")),
]

_CULTURE_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("Collaborative / cross-functional", ("cross-functional", "collaborate", "team-oriented")),
    ("Fast-paced / ownership-driven", ("fast-paced", "ownership", "move quickly", "startup")),
    ("Process-driven / compliance-aware", ("process", "compliance", "governance", "audit")),
    ("Customer-obsessed", ("customer-obsessed", "customer first", "client success")),
]

_COMM_STYLE_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("Direct and concise", ("concise", "direct communication", "clear written")),
    ("Stakeholder-facing", ("stakeholder", "executive communication", "present to")),
    ("Empathetic / service-oriented", ("empathy", "patient", "guest satisfaction", "de-escalat")),
]

_LEARNING_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("Strong learning culture", ("continuous learning", "mentorship", "training budget", "grow with")),
    ("On-the-job training", ("on-the-job", "will train", "training provided")),
]

_SCALE_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("Large / enterprise scale", ("enterprise", "global", "millions of", "fortune", "scale")),
    ("Growth-stage", ("hypergrowth", "scaling", "series ", "rapidly growing")),
    ("Local / small team", ("small team", "boutique", "local business", "family-owned")),
]


def _first_match(blob: str, rules: list[tuple[str, tuple[str, ...]]]) -> str:
    for label, cues in rules:
        if any(c in blob for c in cues):
            return label
    return UNKNOWN


def _collect_matches(blob: str, rules: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    hits: list[str] = []
    for label, cues in rules:
        if any(c in blob for c in cues) and label not in hits:
            hits.append(label)
    return hits


def _trait_signals(blob: str, job_profile: JobProfile | None) -> list[str]:
    traits: list[str] = []
    soft = list((job_profile.soft_skills if job_profile else []) or [])
    for s in soft[:8]:
        if s not in traits:
            traits.append(s)
    cue_traits = [
        ("detail-oriented", ("detail", "accuracy", "precise")),
        ("ownership", ("ownership", "end-to-end", "accountable")),
        ("collaboration", ("collaborat", "cross-functional", "team player")),
        ("customer empathy", ("empathy", "customer-focused", "patient-centered")),
        ("analytical thinking", ("analy", "data-driven", "problem solv")),
        ("adaptability", ("adapt", "fast-paced", "ambiguity")),
    ]
    for label, cues in cue_traits:
        if any(c in blob for c in cues) and label not in traits:
            traits.append(label)
    return traits[:12]


def _priority_signals(blob: str, job_profile: JobProfile | None) -> list[str]:
    priorities: list[str] = []
    rules = [
        ("Reliability and quality", ("quality", "reliability", "uptime", "safety")),
        ("Growth and revenue", ("revenue", "growth", "pipeline", "quota")),
        ("Customer satisfaction", ("nps", "csat", "customer satisfaction", "guest experience")),
        ("Compliance and risk", ("compliance", "risk", "audit", "regulatory")),
        ("Innovation and product", ("innovation", "product", "roadmap", "new features")),
        ("Operational efficiency", ("efficiency", "cost", "throughput", "productivity")),
    ]
    for label, cues in rules:
        if any(c in blob for c in cues):
            priorities.append(label)
    if job_profile and job_profile.responsibilities and not priorities:
        priorities.append("Deliver core role responsibilities")
    return priorities[:8]


def _tech_focus(job_profile: JobProfile | None, blob: str) -> list[str]:
    focus: list[str] = []
    if job_profile:
        for item in (
            list(job_profile.technologies)
            + list(job_profile.cloud)
            + list(job_profile.frameworks)
            + list(job_profile.databases)
        ):
            if item and item not in focus:
                focus.append(item)
    # Cap — only terms evidenced in source blob/metadata, never invented names
    return [t for t in focus if t.lower() in blob][:12]


class CompanyIntelligenceAgent(Agent[CompanyIntelligenceInput, CompanyProfile]):
    agent_id = "company_intelligence"
    responsibility = (
        "Extract CompanyProfile from JD and available metadata without fabrication"
    )

    def run(
        self,
        payload: CompanyIntelligenceInput,
        context: AgentContext | None = None,
    ) -> AgentResult[CompanyProfile]:
        _ = context
        job = payload.job or {}
        meta = dict(payload.company_metadata or {})
        # Official structured fields already on the job record
        for key in (
            "company",
            "company_name",
            "industry",
            "company_industry",
            "company_description",
            "company_size",
            "company_website",
            "about_company",
        ):
            if job.get(key) and key not in meta:
                meta[key] = job.get(key)

        name = (
            str(meta.get("company_name") or meta.get("company") or job.get("company") or "")
            .strip()
            or UNKNOWN
        )
        jd = str(payload.jd_snapshot or "")
        about = str(
            meta.get("company_description")
            or meta.get("about_company")
            or ""
        )
        sources: list[str] = []
        if jd.strip():
            sources.append("job_description")
        if about.strip():
            sources.append("official_company_information")
        if any(k.startswith("company_") for k in meta) or meta.get("industry"):
            sources.append("structured_metadata")

        blob = f"{name}\n{jd}\n{about}\n{meta.get('industry') or ''}".lower()

        industry = str(meta.get("industry") or meta.get("company_industry") or "").strip()
        if not industry or industry.lower() == "unknown":
            industry = _first_match(blob, _INDUSTRY_CUES)
        if industry == UNKNOWN and payload.job_profile:
            industry = (
                payload.job_profile.industry
                if payload.job_profile.industry
                and payload.job_profile.industry.lower() != "general"
                else UNKNOWN
            )

        business_model = _first_match(blob, _BUSINESS_MODEL_CUES)
        product_type = _first_match(blob, _PRODUCT_CUES)
        engineering_culture = _first_match(blob, _CULTURE_CUES)
        communication_style = _first_match(blob, _COMM_STYLE_CUES)
        learning_culture = _first_match(blob, _LEARNING_CUES)
        product_scale = _first_match(blob, _SCALE_CUES)

        customer_type = UNKNOWN
        if any(c in blob for c in ("b2b", "enterprise client", "business customer")):
            customer_type = "Business / B2B"
        elif any(c in blob for c in ("b2c", "consumer", "patient", "guest", "shopper")):
            customer_type = "Consumer / end-user"
        elif any(c in blob for c in ("internal stakeholder", "internal customer")):
            customer_type = "Internal stakeholders"

        innovation = UNKNOWN
        if any(c in blob for c in ("innovat", "cutting-edge", "r&d", "research")):
            innovation = "High"
        elif any(c in blob for c in ("modernize", "digital transformation")):
            innovation = "Moderate"
        elif any(c in blob for c in ("legacy", "maintain", "stable environment")):
            innovation = "Stability-focused"

        cloud_maturity = UNKNOWN
        if any(c in blob for c in ("multi-cloud", "cloud-native", "kubernetes", "aws", "azure", "gcp")):
            cloud_maturity = "Cloud-forward"
        elif "cloud" in blob:
            cloud_maturity = "Cloud-aware"

        security_focus = UNKNOWN
        if any(c in blob for c in ("security", "soc2", "zero trust", "encryption", "hipaa")):
            security_focus = "Elevated"
        elif "compliance" in blob:
            security_focus = "Compliance-oriented"

        ai_usage = UNKNOWN
        if any(c in blob for c in ("machine learning", "artificial intelligence", " llm", "generative ai", "ml model")):
            ai_usage = "Actively used"
        elif re.search(r"\bai\b", blob):
            ai_usage = "Mentioned"

        tech_focus = _tech_focus(payload.job_profile, blob)
        traits = _trait_signals(blob, payload.job_profile)
        priorities = _priority_signals(blob, payload.job_profile)

        profile = CompanyProfile(
            company_name=name,
            industry=industry or UNKNOWN,
            business_model=business_model,
            product_type=product_type,
            technology_focus=tech_focus,
            engineering_culture=engineering_culture,
            customer_type=customer_type,
            innovation_level=innovation,
            cloud_maturity=cloud_maturity,
            security_focus=security_focus,
            ai_usage=ai_usage,
            product_scale=product_scale,
            preferred_candidate_traits=traits,
            communication_style=communication_style,
            learning_culture=learning_culture,
            business_priorities=priorities,
            sources_used=sources or ["none"],
        )
        unknown_fields = [
            key
            for key, value in profile.to_dict().items()
            if value == UNKNOWN
            or (isinstance(value, list) and not value and key not in ("sources_used", "unknown_fields"))
        ]
        profile.unknown_fields = unknown_fields

        return AgentResult(
            agent_id=self.agent_id,
            output=profile,
            metrics={
                "known_field_count": len(profile.to_dict()) - len(unknown_fields) - 2,
                "unknown_field_count": len(unknown_fields),
                "sources": list(sources),
            },
        )


def run_company_intelligence(
    job: dict[str, Any],
    *,
    job_profile: JobProfile | None = None,
    jd_snapshot: str = "",
    company_metadata: dict[str, Any] | None = None,
) -> AgentResult[CompanyProfile]:
    return CompanyIntelligenceAgent().run(
        CompanyIntelligenceInput(
            job=job,
            job_profile=job_profile,
            jd_snapshot=jd_snapshot,
            company_metadata=company_metadata,
        )
    )
