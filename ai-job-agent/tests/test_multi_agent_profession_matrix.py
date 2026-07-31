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


PROFESSIONS = [
    (
        "software",
        {
            "raw_text": """
Pat Kim — Software Engineer
Acme Corp (2021-2025)
- Built REST APIs in Python serving 50k daily users.
- Deployed services on AWS with Docker.
- Collaborated with product managers on roadmap delivery.
Skills: Python, AWS, Docker, SQL, code review
""",
            "skills": ["Python", "AWS", "Docker", "SQL"],
        },
        _job(
            "Backend Engineer",
            "Nimbus Cloud",
            "Build APIs in Python on AWS. Required: Python, Docker, SQL, code review. "
            "Cloud-native SaaS, strong ownership, CI/CD.",
        ),
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
    (
        "finance",
        {
            "raw_text": """
Riley Chen — Financial Analyst
North Bank (2020-2025)
- Built monthly forecasting models in Excel and SQL.
- Reconciled accounts and prepared audit packages.
- Presented variance analysis to finance leadership.
Skills: Excel, SQL, forecasting, reconciliation, audit support
""",
            "skills": ["Excel", "SQL", "forecasting", "reconciliation"],
        },
        _job(
            "Financial Analyst",
            "Harbor Capital",
            "Required: forecasting, Excel, SQL, reconciliation, audit support. "
            "Banking environment with strong compliance and risk focus.",
        ),
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
    (
        "hospitality",
        {
            "raw_text": """
Morgan Diaz — Front Desk Supervisor
Lakeview Hotel (2019-2025)
- Managed guest check-in for a 180-room property.
- Resolved guest complaints with empathy and speed.
- Trained three new associates on PMS procedures.
Skills: guest service, PMS, conflict resolution, training, scheduling
""",
            "skills": ["guest service", "PMS", "conflict resolution", "training"],
        },
        _job(
            "Front Desk Supervisor",
            "Harbor Hotels",
            "Required: guest service, PMS, conflict resolution, team training. "
            "Hospitality brand focused on guest satisfaction and service quality.",
        ),
        {
            "required_skills": ["guest service", "PMS", "conflict resolution"],
            "preferred_skills": ["training"],
            "hard_requirements": ["guest service", "PMS", "conflict resolution"],
            "soft_requirements": ["training"],
            "responsibilities": ["Supervise front desk", "Resolve guest issues"],
            "tools_technologies": ["PMS"],
            "industry_terminology": ["guest satisfaction"],
            "soft_skills": ["empathy"],
            "education_certifications": [],
            "ats_keywords": ["guest service", "PMS"],
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
        assert hm.actionable_feedback

    # Genuinely different structured outcomes across professions
    assert len(set(strategies.values())) == len(PROFESSIONS)
    assert len(set(companies.values())) >= 2
    assert any(missing[name] for name in missing)  # at least one gap surfaced
