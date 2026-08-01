"""Profession-matrix checks: different jobs → different structured outcomes."""

from __future__ import annotations

from typing import Any

import pytest

from intelligent_tailoring.agents.base import AgentContext
from intelligent_tailoring.agents.company_intelligence_agent import (
    CompanyIntelligenceAgent,
)
from intelligent_tailoring.agents.evidence_mapping_agent import EvidenceMappingAgent
from intelligent_tailoring.agents.hiring_manager_agent import (
    HiringManagerSimulationAgent,
)
from intelligent_tailoring.agents.job_intelligence_agent import JobIntelligenceAgent
from intelligent_tailoring.agents.resume_knowledge_agent import ResumeKnowledgeAgent
from intelligent_tailoring.agents.resume_strategy_agent import ResumeStrategyAgent
from intelligent_tailoring.agents.schemas import (
    CompanyIntelligenceInput,
    EvidenceMappingInput,
    HiringManagerInput,
    JobIntelligenceInput,
    ResumeKnowledgeInput,
    ResumeStrategyInput,
)


def _job(title: str, company: str, description: str) -> dict[str, Any]:
    return {"title": title, "company": company, "description": description}


def _prof(
    name: str,
    raw_text: str,
    skills: list[str],
    title: str,
    company: str,
    description: str,
    reqs: dict[str, Any],
) -> tuple:
    return (
        name,
        {"raw_text": raw_text, "skills": skills},
        _job(title, company, description),
        reqs,
    )


PROFESSIONS = [
    _prof(
        "software",
        """
Pat Kim — Software Engineer
Acme Corp (2021-2025)
- Built REST APIs in Python serving 50k daily users.
- Deployed services on AWS with Docker.
- Collaborated with product managers on roadmap delivery.
Skills: Python, AWS, Docker, SQL, code review
""",
        ["Python", "AWS", "Docker", "SQL"],
        "Backend Engineer",
        "Nimbus Cloud",
        "Build APIs in Python on AWS. Required: Python, Docker, SQL, code review. "
        "Cloud-native SaaS, strong ownership, CI/CD.",
        {
            "required_skills": ["Python", "Docker", "SQL", "Kubernetes"],
            "preferred_skills": ["AWS"],
            "hard_requirements": ["Python", "Docker", "SQL", "Kubernetes"],
            "soft_requirements": ["AWS"],
            "responsibilities": ["Build APIs", "Operate cloud services"],
            "tools_technologies": ["Python", "Docker", "AWS"],
            "industry_terminology": ["REST", "CI/CD"],
            "soft_skills": ["ownership"],
            "education_certifications": [],
            "ats_keywords": ["Python", "Docker", "AWS"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "finance",
        """
Riley Chen — Financial Analyst
North Bank (2020-2025)
- Built monthly forecasting models in Excel and SQL.
- Reconciled accounts and prepared audit packages.
- Presented variance analysis to finance leadership.
Skills: Excel, SQL, forecasting, reconciliation, audit support
""",
        ["Excel", "SQL", "forecasting", "reconciliation"],
        "Financial Analyst",
        "Harbor Capital",
        "Required: forecasting, Excel, SQL, reconciliation, audit support. "
        "Banking environment with strong compliance and risk focus.",
        {
            "required_skills": ["forecasting", "Excel", "SQL", "reconciliation"],
            "preferred_skills": ["audit support"],
            "hard_requirements": ["forecasting", "Excel", "SQL", "reconciliation"],
            "soft_requirements": ["audit support"],
            "responsibilities": ["Build forecasts", "Support audits"],
            "tools_technologies": ["Excel", "SQL"],
            "industry_terminology": ["variance analysis", "audit"],
            "soft_skills": ["attention to detail"],
            "education_certifications": [],
            "ats_keywords": ["forecasting", "Excel", "SQL"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "customer_service",
        """
Casey Brooks — Customer Support Specialist
HelpHub (2021-2025)
- Resolved 40+ customer tickets daily across chat and phone.
- Documented troubleshooting steps in the knowledge base.
- Escalated complex billing issues to senior specialists.
Skills: customer service, Zendesk, de-escalation, documentation
""",
        ["customer service", "Zendesk", "de-escalation", "documentation"],
        "Customer Support Specialist",
        "BrightCare",
        "Required: customer service, Zendesk, de-escalation, documentation. "
        "High-volume support center focused on empathy and first-contact resolution.",
        {
            "required_skills": ["customer service", "Zendesk", "de-escalation"],
            "preferred_skills": ["documentation"],
            "hard_requirements": ["customer service", "Zendesk", "de-escalation"],
            "soft_requirements": ["documentation"],
            "responsibilities": ["Resolve tickets", "Document solutions"],
            "tools_technologies": ["Zendesk"],
            "industry_terminology": ["first-contact resolution"],
            "soft_skills": ["empathy"],
            "education_certifications": [],
            "ats_keywords": ["customer service", "Zendesk"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "sales",
        """
Alex Rivera — Account Executive
BrightSoft Inc (2021-2025)
- Owned a $1.2M ARR territory selling B2B SaaS to mid-market customers.
- Exceeded quota at 118% in 2024 through consultative discovery.
- Partnered with customer success to reduce churn on key accounts.
Skills: Salesforce, pipeline management, negotiation, discovery, CRM
""",
        ["Salesforce", "pipeline management", "negotiation", "discovery", "CRM"],
        "Account Executive",
        "Orbit SaaS",
        "Required: Salesforce, pipeline management, negotiation, discovery. "
        "B2B SaaS sales with consultative selling and CRM discipline.",
        {
            "required_skills": ["Salesforce", "pipeline management", "negotiation"],
            "preferred_skills": ["discovery"],
            "hard_requirements": ["Salesforce", "pipeline management", "negotiation"],
            "soft_requirements": ["discovery"],
            "responsibilities": ["Own territory", "Run discovery"],
            "tools_technologies": ["Salesforce", "CRM"],
            "industry_terminology": ["ARR", "quota"],
            "soft_skills": ["consultative selling"],
            "education_certifications": [],
            "ats_keywords": ["Salesforce", "pipeline management"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "administration",
        """
Jamie Ortiz — Office Administrator
Civic Partners (2019-2025)
- Managed calendars and travel for a 12-person leadership team.
- Maintained filing systems and prepared board meeting packets.
- Coordinated vendor invoices and office supply purchasing.
Skills: scheduling, filing, Microsoft Office, vendor coordination
""",
        ["scheduling", "filing", "Microsoft Office", "vendor coordination"],
        "Office Administrator",
        "North Harbor Group",
        "Required: scheduling, filing, Microsoft Office, vendor coordination. "
        "Busy professional services office needing reliable administration.",
        {
            "required_skills": ["scheduling", "filing", "Microsoft Office"],
            "preferred_skills": ["vendor coordination"],
            "hard_requirements": ["scheduling", "filing", "Microsoft Office"],
            "soft_requirements": ["vendor coordination"],
            "responsibilities": ["Manage calendars", "Prepare packets"],
            "tools_technologies": ["Microsoft Office"],
            "industry_terminology": ["board packets"],
            "soft_skills": ["organization"],
            "education_certifications": [],
            "ats_keywords": ["scheduling", "Microsoft Office"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "education",
        """
Sam Lee — Middle School Math Teacher
Lincoln Middle School (2018-2025)
- Taught algebra and geometry to grades 7-8 using differentiated instruction.
- Raised average assessment scores by 12% over two years.
- Led after-school tutoring for students needing extra support.
Skills: curriculum planning, classroom management, differentiated instruction, assessment
""",
        [
            "curriculum planning",
            "classroom management",
            "differentiated instruction",
            "assessment",
        ],
        "Math Teacher",
        "Riverdale Schools",
        "Required: curriculum planning, classroom management, differentiated instruction. "
        "Middle school math role focused on tutoring and assessment growth.",
        {
            "required_skills": [
                "curriculum planning",
                "classroom management",
                "differentiated instruction",
            ],
            "preferred_skills": ["assessment"],
            "hard_requirements": [
                "curriculum planning",
                "classroom management",
                "differentiated instruction",
            ],
            "soft_requirements": ["assessment"],
            "responsibilities": ["Teach algebra", "Lead tutoring"],
            "tools_technologies": [],
            "industry_terminology": ["differentiated instruction"],
            "soft_skills": ["communication"],
            "education_certifications": ["teaching license"],
            "ats_keywords": ["classroom management", "curriculum planning"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "healthcare",
        """
Jane Doe — Registered Nurse
City General Hospital (2020-2025)
- Provided bedside care for 6 patients per shift in a medical-surgical unit.
- Administered medications and documented care in the EHR.
- Mentored two new graduate nurses during orientation.
Skills: patient care, EHR, medication administration, patient education, teamwork
""",
        ["patient care", "EHR", "medication administration", "patient education"],
        "Staff Nurse",
        "Metro Health",
        "Required: patient care, EHR, medication administration, patient education. "
        "Hospital med-surg unit with HIPAA-compliant documentation.",
        {
            "required_skills": ["patient care", "EHR", "medication administration"],
            "preferred_skills": ["patient education"],
            "hard_requirements": ["patient care", "EHR", "medication administration"],
            "soft_requirements": ["patient education"],
            "responsibilities": ["Provide bedside care", "Document in EHR"],
            "tools_technologies": ["EHR"],
            "industry_terminology": ["HIPAA", "med-surg"],
            "soft_skills": ["teamwork"],
            "education_certifications": ["RN license"],
            "ats_keywords": ["patient care", "EHR"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "logistics",
        """
Drew Hale — Logistics Coordinator
FastLane Freight (2020-2025)
- Scheduled outbound shipments across 4 regional warehouses.
- Tracked inventory discrepancies and coordinated carrier pickups.
- Improved on-time delivery reporting with daily route reviews.
Skills: logistics, inventory, warehouse coordination, routing, Excel
""",
        ["logistics", "inventory", "warehouse coordination", "routing", "Excel"],
        "Logistics Coordinator",
        "Harbor Freight Co",
        "Required: logistics, inventory, warehouse coordination, routing. "
        "Distribution network focused on on-time delivery and inventory accuracy.",
        {
            "required_skills": ["logistics", "inventory", "warehouse coordination"],
            "preferred_skills": ["routing"],
            "hard_requirements": ["logistics", "inventory", "warehouse coordination"],
            "soft_requirements": ["routing"],
            "responsibilities": ["Schedule shipments", "Track inventory"],
            "tools_technologies": ["Excel"],
            "industry_terminology": ["on-time delivery"],
            "soft_skills": ["coordination"],
            "education_certifications": [],
            "ats_keywords": ["logistics", "inventory"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "manufacturing",
        """
Taylor Ng — Production Associate
SteelForm Works (2019-2025)
- Operated CNC equipment to meet daily production quotas.
- Performed quality inspections against work orders.
- Followed safety procedures and trained two new hires on the line.
Skills: CNC, quality control, safety procedures, production scheduling
""",
        ["CNC", "quality control", "safety procedures", "production scheduling"],
        "Production Associate",
        "Summit Manufacturing",
        "Required: CNC, quality control, safety procedures, production scheduling. "
        "Manufacturing floor role with strict safety and quality standards.",
        {
            "required_skills": ["CNC", "quality control", "safety procedures"],
            "preferred_skills": ["production scheduling"],
            "hard_requirements": ["CNC", "quality control", "safety procedures"],
            "soft_requirements": ["production scheduling"],
            "responsibilities": ["Operate CNC", "Inspect quality"],
            "tools_technologies": ["CNC"],
            "industry_terminology": ["work orders", "quotas"],
            "soft_skills": ["safety awareness"],
            "education_certifications": [],
            "ats_keywords": ["CNC", "quality control"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
    _prof(
        "marketing",
        """
Quinn Patel — Marketing Specialist
Northwind Media (2021-2025)
- Planned email campaigns that grew newsletter engagement.
- Coordinated social media content calendars with design partners.
- Reported campaign performance in Google Analytics dashboards.
Skills: email campaigns, social media, Google Analytics, content planning
""",
        ["email campaigns", "social media", "Google Analytics", "content planning"],
        "Marketing Specialist",
        "Pulse Agency",
        "Required: email campaigns, social media, Google Analytics, content planning. "
        "Growth marketing team focused on measurable campaign performance.",
        {
            "required_skills": ["email campaigns", "social media", "Google Analytics"],
            "preferred_skills": ["content planning"],
            "hard_requirements": ["email campaigns", "social media", "Google Analytics"],
            "soft_requirements": ["content planning"],
            "responsibilities": ["Plan campaigns", "Report performance"],
            "tools_technologies": ["Google Analytics"],
            "industry_terminology": ["engagement", "campaigns"],
            "soft_skills": ["creativity"],
            "education_certifications": [],
            "ats_keywords": ["email campaigns", "Google Analytics"],
            "seniority_level": "mid",
            "language": "en",
        },
    ),
]


@pytest.fixture()
def stub_job_extract(monkeypatch):
    store: dict[str, dict[str, Any]] = {}

    def _install(job_title: str, payload: dict[str, Any]) -> None:
        store[job_title] = payload

    def _extract(job, **kwargs):
        title = str(job.get("title") or "")
        return dict(store.get(title) or payload_fallback())

    def payload_fallback():
        return {
            "required_skills": [],
            "preferred_skills": [],
            "hard_requirements": [],
            "soft_requirements": [],
            "responsibilities": [],
            "tools_technologies": [],
            "industry_terminology": [],
            "soft_skills": [],
            "education_certifications": [],
            "ats_keywords": [],
            "seniority_level": "",
            "language": "en",
        }

    monkeypatch.setattr(
        "intelligent_tailoring.agents.job_intelligence_agent.extract_job_requirements",
        _extract,
    )
    monkeypatch.setattr(
        "intelligent_tailoring.agents.evidence_mapping_agent.run_semantic_inference",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "intelligent_tailoring.agents.resume_strategy_agent.analyze_job",
        lambda job, **kwargs: {
            "job_family": "general",
            "industry": "general",
            "requirements": kwargs.get("requirements") or {},
            "ats_keywords": list((kwargs.get("requirements") or {}).get("ats_keywords") or []),
            "emphasis_keywords": {},
        },
    )
    return _install


def test_profession_matrix_produces_distinct_outputs(stub_job_extract):
    strategies = {}
    companies = {}
    hm_scores = {}
    missing = {}

    for name, cv, job, reqs in PROFESSIONS:
        stub_job_extract(job["title"], reqs)
        knowledge = ResumeKnowledgeAgent().run(
            ResumeKnowledgeInput(cv_profile=cv, source_documents=cv["raw_text"]),
            AgentContext(use_cache=False, language="en"),
        ).output
        job_profile = JobIntelligenceAgent().run(
            JobIntelligenceInput(
                job=job,
                jd_snapshot=f"{job['title']}\n{job['company']}\n{job['description']}",
            ),
            AgentContext(use_cache=False),
        ).output
        company = CompanyIntelligenceAgent().run(
            CompanyIntelligenceInput(
                job=job,
                job_profile=job_profile,
                jd_snapshot=job["description"],
            )
        ).output
        evidence = EvidenceMappingAgent().run(
            EvidenceMappingInput(
                resume_facts=knowledge.resume_facts,
                job_profile=job_profile,
                inferred=[],
                knowledge_base=knowledge.knowledge_base,
            ),
            AgentContext(use_cache=False),
        ).output
        strategy = ResumeStrategyAgent().run(
            ResumeStrategyInput(
                job_profile=job_profile,
                company_profile=company,
                evidence_map=evidence,
                resume_facts=knowledge.resume_facts,
                language="en",
            ),
            AgentContext(use_cache=False),
        ).output
        # Minimal tailored resume from facts for HM scoring
        resume = {
            "professional_summary": f"Professional targeting {job['title']}.",
            "skills": list(cv.get("skills") or []),
            "experience": [
                {
                    "company": "Prior Employer",
                    "title": "Role",
                    "bullets": [
                        line.strip("- ").strip()
                        for line in cv["raw_text"].splitlines()
                        if line.strip().startswith("- ")
                    ][:4],
                }
            ],
            "projects": [],
        }
        hm = HiringManagerSimulationAgent().run(
            HiringManagerInput(
                resume=resume,
                job_profile=job_profile,
                company_profile=company,
                evidence_map=evidence,
                strategy=strategy,
            )
        ).output

        strategies[name] = (
            strategy.summary_focus,
            tuple(strategy.skills_priority[:5]),
            tuple(strategy.forbidden_claims[:5]),
        )
        companies[name] = (
            company.industry,
            company.business_model,
            tuple(company.business_priorities[:3]),
        )
        hm_scores[name] = hm.overall_fit
        missing[name] = tuple(hm.missing_evidence[:5])

        # Every hard requirement intentionally evaluated
        hard_reqs = set(job_profile.required_skills)
        mapped = {m.requirement for m in evidence.mappings}
        assert hard_reqs.issubset(mapped)
        assert all(m.evidence_strength for m in evidence.mappings)
        assert all(m.match_type for m in evidence.mappings)
        assert hm.actionable_feedback
        # Genuine gaps remain visible; never request fabrication
        assert hm.genuine_gaps is not None
        assert not any(
            "fabricat" in str(a).lower() or "invent" in str(a).lower()
            for a in hm.actionable_feedback
        )
        # Narrative fields populated
        assert strategy.professional_narrative or strategy.professional_story
        # Capstone/academic context preserved when present
        for fact in knowledge.knowledge_base.facts:
            if "capstone" in (fact.project or fact.original_text).lower():
                assert fact.context_type == "academic"

    # Genuinely different structured outcomes across professions
    assert len(set(strategies.values())) >= max(3, len(PROFESSIONS) // 2)
    assert len(set(companies.values())) >= 2
    assert any(missing[name] for name in missing)  # at least one gap surfaced
    assert len(PROFESSIONS) >= 10
