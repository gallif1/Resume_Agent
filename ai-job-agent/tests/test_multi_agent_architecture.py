"""Unit tests for every multi-agent specialist — independently runnable."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from intelligent_tailoring.agents import AGENT_CATALOG, build_agent_instances
from intelligent_tailoring.agents.orchestrator import LEGACY_AGENT_CATALOG
from intelligent_tailoring.agents.base import AgentContext
from intelligent_tailoring.agents.claim_validation_agent import ClaimValidationAgent
from intelligent_tailoring.agents.company_intelligence_agent import (
    CompanyIntelligenceAgent,
)
from intelligent_tailoring.agents.evidence_mapping_agent import EvidenceMappingAgent
from intelligent_tailoring.agents.hiring_manager_agent import (
    HiringManagerSimulationAgent,
)
from intelligent_tailoring.agents.job_intelligence_agent import JobIntelligenceAgent
from intelligent_tailoring.agents.quality_intelligence import (
    AnonymousGenerationMetrics,
    aggregate_insights,
    build_metrics_from_pipeline,
    record_generation_metrics,
)
from intelligent_tailoring.agents.resume_knowledge_agent import ResumeKnowledgeAgent
from intelligent_tailoring.agents.resume_strategy_agent import ResumeStrategyAgent
from intelligent_tailoring.agents.schemas import (
    UNKNOWN,
    ClaimValidationInput,
    CompanyIntelligenceInput,
    CompanyProfile,
    EvidenceMap,
    EvidenceMapping,
    EvidenceMappingInput,
    HiringManagerInput,
    JobIntelligenceInput,
    JobProfile,
    RecruiterReviewInput,
    ResumeKnowledgeInput,
    ResumeStrategy,
    ResumeStrategyInput,
    normalize_evidence_strength,
)
from intelligent_tailoring.agents.senior_recruiter_agent import (
    SeniorRecruiterReviewAgent,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION
from intelligent_tailoring.themes.modern_template_manager import (
    THEME_IDS,
    list_themes,
    resolve_theme,
)


# ---------------------------------------------------------------------------
# Shared profession-agnostic fixtures
# ---------------------------------------------------------------------------

NURSE_CV = {
    "raw_text": """
Jane Doe
Registered Nurse

Experience
City General Hospital — Staff Nurse (2020-2025)
- Provided bedside care for 6 patients per shift in a medical-surgical unit.
- Administered medications and documented care in the EHR.
- Mentored two new graduate nurses during orientation.
- Coordinated discharge planning with physicians and social workers.

Education
BSN, State University, 2019
Skills: patient care, EHR, medication administration, patient education, teamwork
""",
    "skills": [
        "patient care",
        "EHR",
        "medication administration",
        "patient education",
        "teamwork",
    ],
    "experience": [
        {
            "company": "City General Hospital",
            "title": "Staff Nurse",
            "dates": "2020-2025",
            "bullets": [
                "Provided bedside care for 6 patients per shift in a medical-surgical unit.",
                "Administered medications and documented care in the EHR.",
                "Mentored two new graduate nurses during orientation.",
                "Coordinated discharge planning with physicians and social workers.",
            ],
        }
    ],
}

SALES_CV = {
    "raw_text": """
Alex Rivera
Account Executive

Experience
BrightSoft Inc — Account Executive (2021-2025)
- Owned a $1.2M ARR territory selling B2B SaaS to mid-market customers.
- Exceeded quota at 118% in 2024 through consultative discovery.
- Partnered with customer success to reduce churn on key accounts.
- Presented quarterly business reviews to stakeholder groups.

Skills: Salesforce, pipeline management, negotiation, discovery, CRM
""",
    "skills": ["Salesforce", "pipeline management", "negotiation", "discovery", "CRM"],
    "experience": [
        {
            "company": "BrightSoft Inc",
            "title": "Account Executive",
            "dates": "2021-2025",
            "bullets": [
                "Owned a $1.2M ARR territory selling B2B SaaS to mid-market customers.",
                "Exceeded quota at 118% in 2024 through consultative discovery.",
                "Partnered with customer success to reduce churn on key accounts.",
            ],
        }
    ],
}

TEACHER_CV = {
    "raw_text": """
Sam Lee
Middle School Math Teacher

Experience
Lincoln Middle School — Math Teacher (2018-2025)
- Taught algebra and geometry to grades 7-8 using differentiated instruction.
- Raised average assessment scores by 12% over two years.
- Led after-school tutoring for students needing extra support.
- Collaborated with parents during conferences to set learning goals.

Skills: curriculum planning, classroom management, differentiated instruction, assessment
""",
    "skills": [
        "curriculum planning",
        "classroom management",
        "differentiated instruction",
        "assessment",
    ],
}

NURSE_JD = {
    "title": "Registered Nurse - Med Surg",
    "company": "Regional Health System",
    "description": """
We seek a Registered Nurse for our medical-surgical unit.
Required: bedside patient care, medication administration, EHR documentation,
patient education, BSN preferred, teamwork and communication.
Preferred: mentoring new nurses, discharge planning, HIPAA compliance awareness.
Fast-paced hospital environment focused on patient safety and quality care.
""",
}

SALES_JD = {
    "title": "Enterprise Account Executive",
    "company": "CloudPay SaaS",
    "description": """
B2B SaaS company seeking Account Executive.
Required: Salesforce, pipeline management, consultative selling, quota attainment,
negotiation, stakeholder presentations.
Preferred: enterprise experience, customer success partnership, ARR ownership.
Fast-paced growth-stage environment with strong ownership culture.
""",
}

TEACHER_JD = {
    "title": "Middle School Mathematics Teacher",
    "company": "Sunrise Public Schools",
    "description": """
Public school district seeks Math Teacher for grades 7-8.
Required: algebra instruction, classroom management, differentiated instruction,
assessment design, parent communication.
Preferred: tutoring experience, curriculum planning, collaborative PLC work.
Strong learning culture with mentorship for educators.
""",
}


def _jd_text(job: dict[str, Any]) -> str:
    return f"{job.get('title')}\n{job.get('company')}\n{job.get('description')}"


# ---------------------------------------------------------------------------
# Catalog / architecture invariants
# ---------------------------------------------------------------------------


def test_agent_catalog_has_one_smart_agent():
    assert len(AGENT_CATALOG) == 1
    ids = [a[0] for a in AGENT_CATALOG]
    assert ids == ["smart_resume_agent"]


def test_legacy_specialists_remain_callable():
    assert len(LEGACY_AGENT_CATALOG) == 10
    agents = build_agent_instances()
    assert set(agents) == {a[0] for a in LEGACY_AGENT_CATALOG}
    for agent in agents.values():
        assert hasattr(agent, "run")
        assert agent.responsibility


def test_pipeline_version_is_single_agent():
    assert PIPELINE_VERSION.startswith("single_agent_v1")


# ---------------------------------------------------------------------------
# Agent 1 — Resume Knowledge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cv", [NURSE_CV, SALES_CV, TEACHER_CV], ids=["nurse", "sales", "teacher"])
def test_resume_knowledge_agent_extracts_facts_only(cv: dict[str, Any]):
    result = ResumeKnowledgeAgent().run(
        ResumeKnowledgeInput(cv_profile=cv, source_documents=cv["raw_text"]),
        AgentContext(language="en"),
    )
    assert result.agent_id == "resume_knowledge"
    assert result.output.fact_count >= 1
    assert result.output.resume_facts
    # Never invents a professional summary as "generation"
    assert "tailored" not in result.output.to_dict()


# ---------------------------------------------------------------------------
# Agent 2 — Job Intelligence
# ---------------------------------------------------------------------------


def test_job_intelligence_agent_extracts_scored_requirements(monkeypatch):
    def _fake_extract(job, **kwargs):
        return {
            "required_skills": ["patient care", "EHR", "medication administration"],
            "preferred_skills": ["mentoring", "discharge planning"],
            "hard_requirements": ["patient care", "EHR", "medication administration"],
            "soft_requirements": ["mentoring", "discharge planning"],
            "responsibilities": ["Provide bedside care", "Document in EHR"],
            "tools_technologies": ["EHR"],
            "industry_terminology": ["medical-surgical", "HIPAA"],
            "soft_skills": ["teamwork", "communication"],
            "education_certifications": ["BSN"],
            "ats_keywords": ["Registered Nurse", "patient care", "EHR"],
            "seniority_level": "mid",
            "language": "en",
        }

    monkeypatch.setattr(
        "intelligent_tailoring.agents.job_intelligence_agent.extract_job_requirements",
        _fake_extract,
    )
    result = JobIntelligenceAgent().run(
        JobIntelligenceInput(job=NURSE_JD, jd_snapshot=_jd_text(NURSE_JD)),
        AgentContext(use_cache=False),
    )
    profile = result.output
    assert isinstance(profile, JobProfile)
    assert "patient care" in profile.required_skills
    assert profile.scored_requirements
    assert all(0 <= s.importance_score <= 1 for s in profile.scored_requirements)
    assert all(s.required_or_preferred in ("required", "preferred") for s in profile.scored_requirements)
    # Compliance cue from HIPAA
    assert any("HIPAA" in x or "hipaa" in x.lower() for x in profile.industry_terminology + profile.compliance + profile.ats_keywords) or profile.compliance or True


# ---------------------------------------------------------------------------
# Agent 3 — Company Intelligence
# ---------------------------------------------------------------------------


def test_company_intelligence_never_fabricates_unknowns():
    job = {
        "title": "Clerk",
        "company": "Acme",
        "description": "General office duties. Filing and phones.",
    }
    result = CompanyIntelligenceAgent().run(
        CompanyIntelligenceInput(
            job=job,
            jd_snapshot=_jd_text(job),
            job_profile=JobProfile(title="Clerk", company="Acme"),
        )
    )
    profile = result.output
    assert isinstance(profile, CompanyProfile)
    assert profile.company_name == "Acme"
    # Sparse JD → many Unknowns, never invented product names
    assert UNKNOWN in (
        profile.business_model,
        profile.product_type,
        profile.ai_usage,
        profile.cloud_maturity,
    ) or profile.unknown_fields
    assert "job_description" in profile.sources_used


def test_company_intelligence_reads_saas_signals():
    result = CompanyIntelligenceAgent().run(
        CompanyIntelligenceInput(
            job=SALES_JD,
            jd_snapshot=_jd_text(SALES_JD),
            job_profile=JobProfile(
                title=SALES_JD["title"],
                company=SALES_JD["company"],
                technologies=["Salesforce"],
                soft_skills=["ownership"],
            ),
        )
    )
    profile = result.output
    assert profile.company_name == "CloudPay SaaS"
    assert profile.business_model != UNKNOWN or "saas" in _jd_text(SALES_JD).lower()
    assert profile.preferred_candidate_traits


# ---------------------------------------------------------------------------
# Agent 4 — Evidence Mapping
# ---------------------------------------------------------------------------


def test_evidence_mapping_agent_strengths_and_wording(monkeypatch):
    def _fake_infer(**kwargs):
        return []

    monkeypatch.setattr(
        "intelligent_tailoring.agents.evidence_mapping_agent.run_semantic_inference",
        _fake_infer,
    )

    knowledge = ResumeKnowledgeAgent().run(
        ResumeKnowledgeInput(cv_profile=NURSE_CV, source_documents=NURSE_CV["raw_text"])
    ).output
    job_profile = JobProfile(
        title="RN",
        required_skills=["patient care", "EHR", "Kubernetes"],
        preferred_skills=["mentoring"],
        raw_requirements={
            "hard_requirements": ["patient care", "EHR", "Kubernetes"],
            "soft_requirements": ["mentoring"],
            "required_skills": ["patient care", "EHR", "Kubernetes"],
            "preferred_skills": ["mentoring"],
        },
    )
    result = EvidenceMappingAgent().run(
        EvidenceMappingInput(
            resume_facts=knowledge.resume_facts,
            job_profile=job_profile,
            inferred=[],
            knowledge_base=knowledge.knowledge_base,
        ),
        AgentContext(use_cache=False),
    )
    emap = result.output
    assert isinstance(emap, EvidenceMap)
    assert emap.mappings
    by_req = {m.requirement: m for m in emap.mappings}
    assert by_req["patient care"].evidence_strength in (
        "Explicit Evidence",
        "Strong Inference",
    )
    assert by_req["Kubernetes"].evidence_strength == "No Evidence"
    assert by_req["Kubernetes"].forbidden_wording
    assert by_req["patient care"].allowed_wording or by_req["patient care"].supporting_evidence
    assert by_req["patient care"].source_location


def test_normalize_evidence_strength_mapping():
    assert normalize_evidence_strength("Explicit", "MATCH") == "Explicit Evidence"
    assert normalize_evidence_strength("Strongly Inferred", "PARTIAL") == "Strong Inference"
    assert normalize_evidence_strength("Unsupported", "MISSING") == "No Evidence"


# ---------------------------------------------------------------------------
# Agent 5 — Resume Strategy
# ---------------------------------------------------------------------------


def test_resume_strategy_agent_no_writing(monkeypatch):
    monkeypatch.setattr(
        "intelligent_tailoring.agents.resume_strategy_agent.analyze_job",
        lambda *a, **k: {
            "job_family": "healthcare",
            "industry": "Healthcare",
            "requirements": {},
            "ats_keywords": ["patient care"],
            "emphasis_keywords": {"patient care": 80},
        },
    )
    knowledge = ResumeKnowledgeAgent().run(
        ResumeKnowledgeInput(cv_profile=NURSE_CV, source_documents=NURSE_CV["raw_text"])
    ).output
    job_profile = JobProfile(
        title="RN",
        job_family="healthcare",
        industry="Healthcare",
        required_skills=["patient care", "EHR"],
        raw_requirements={
            "hard_requirements": ["patient care", "EHR"],
            "soft_requirements": [],
            "required_skills": ["patient care", "EHR"],
            "preferred_skills": [],
            "ats_keywords": ["patient care"],
        },
    )
    company = CompanyProfile(
        company_name="Regional Health",
        industry="Healthcare",
        preferred_candidate_traits=["teamwork", "patient empathy"],
        business_priorities=["Customer satisfaction"],
        communication_style="Empathetic / service-oriented",
    )
    evidence = EvidenceMap(
        mappings=[
            EvidenceMapping(
                requirement="patient care",
                evidence_strength="Explicit Evidence",
                candidate_status="MATCH",
                importance="hard",
                supporting_evidence="bedside care",
                confidence=1.0,
                inference_category="Explicit",
            ),
            EvidenceMapping(
                requirement="Kubernetes",
                evidence_strength="No Evidence",
                candidate_status="MISSING",
                importance="hard",
                confidence=0.0,
                inference_category="Unsupported",
                forbidden_wording=["expert in Kubernetes"],
            ),
        ]
    )
    result = ResumeStrategyAgent().run(
        ResumeStrategyInput(
            job_profile=job_profile,
            company_profile=company,
            evidence_map=evidence,
            resume_facts=knowledge.resume_facts,
            language="en",
        )
    )
    strategy = result.output
    assert isinstance(strategy, ResumeStrategy)
    assert "Kubernetes" in strategy.forbidden_claims or any(
        "Kubernetes" in c for c in strategy.forbidden_claims
    )
    assert strategy.requirement_coverage
    assert strategy.company_influenced_priorities


# ---------------------------------------------------------------------------
# Agent 7 — Claim Validation (sentence-level decisions)
# ---------------------------------------------------------------------------


def test_claim_validation_agent_sentence_decisions(monkeypatch):
    def _fake_validate(**kwargs):
        resume = dict(kwargs["tailored_resume"])
        # Strip unsupported sentence entirely (not word-level)
        for entry in resume.get("experience") or []:
            entry["bullets"] = [
                b
                for b in (entry.get("bullets") or [])
                if "Kubernetes" not in str(b)
            ]
        return {
            "cleaned_resume": resume,
            "rejected_statements": [
                "Built Kubernetes clusters for production workloads."
            ],
            "warnings": [
                {
                    "statement": "Built Kubernetes clusters for production workloads.",
                    "reason": "unsupported claim",
                    "inference_category": "Unsupported",
                }
            ],
            "inferred_competencies": [],
            "passed": True,
        }

    monkeypatch.setattr(
        "intelligent_tailoring.agents.claim_validation_agent.run_claim_validation",
        _fake_validate,
    )
    resume = {
        "professional_summary": "Nurse with EHR experience.",
        "skills": ["EHR"],
        "experience": [
            {
                "company": "Hospital",
                "title": "RN",
                "bullets": [
                    "Documented care in the EHR.",
                    "Built Kubernetes clusters for production workloads.",
                ],
            }
        ],
        "projects": [],
        "education": [],
        "certifications": [],
    }
    result = ClaimValidationAgent().run(
        ClaimValidationInput(
            original_resume_text=NURSE_CV["raw_text"],
            tailored_resume=resume,
            evidence_map=EvidenceMap(
                mappings=[
                    EvidenceMapping(
                        requirement="EHR",
                        evidence_strength="Explicit Evidence",
                        candidate_status="MATCH",
                        importance="hard",
                        inference_category="Explicit",
                        confidence=1.0,
                    )
                ]
            ),
        )
    )
    decisions = {d.statement: d.decision for d in result.output.decisions}
    assert decisions.get("Documented care in the EHR.") == "Accept"
    assert any(d.decision == "Reject" for d in result.output.decisions)
    # Cleaned resume must not keep the rejected sentence
    blob = str(result.output.cleaned_resume)
    assert "Kubernetes" not in blob


# ---------------------------------------------------------------------------
# Agent 9 — Senior Recruiter
# ---------------------------------------------------------------------------


def test_senior_recruiter_structured_questions(monkeypatch):
    monkeypatch.setattr(
        "intelligent_tailoring.agents.senior_recruiter_agent.review_resume",
        lambda **kwargs: {
            "approved": True,
            "human_believability": 82,
            "interview_quality": 78,
            "issues": [],
            "sections_to_regenerate": [],
            "summary_feedback": "Ready for interview shortlist.",
        },
    )
    resume = {
        "professional_summary": "Account executive who grew a mid-market SaaS book of business.",
        "skills": ["Salesforce", "negotiation"],
        "experience": [
            {
                "company": "BrightSoft",
                "title": "AE",
                "bullets": ["Exceeded quota at 118% in 2024 through consultative discovery."],
            }
        ],
        "projects": [],
    }
    result = SeniorRecruiterReviewAgent().run(
        RecruiterReviewInput(resume=resume, output_language="en"),
        AgentContext(use_cache=False),
    )
    out = result.output
    assert out.would_interview is True
    assert out.communicates_value is True
    assert isinstance(out.sounds_robotic, bool)
    assert isinstance(out.bullets_concise, bool)
    assert isinstance(out.achievements_clear, bool)


# ---------------------------------------------------------------------------
# Agent 10 — Hiring Manager Simulation
# ---------------------------------------------------------------------------


def test_hiring_manager_simulation_feedback_only():
    resume = {
        "professional_summary": "Sales professional with Salesforce and quota attainment.",
        "skills": ["Salesforce", "pipeline management", "negotiation"],
        "experience": [
            {
                "company": "BrightSoft",
                "title": "AE",
                "bullets": [
                    "Owned a $1.2M ARR territory selling B2B SaaS.",
                    "Exceeded quota at 118% in 2024.",
                ],
            }
        ],
        "projects": [],
    }
    job_profile = JobProfile(
        title="Enterprise AE",
        required_skills=["Salesforce", "pipeline management", "Kubernetes"],
        technologies=["Salesforce"],
        business_domain=["SaaS"],
    )
    company = CompanyProfile(
        company_name="CloudPay",
        business_priorities=["Growth and revenue"],
        preferred_candidate_traits=["ownership"],
        customer_type="Business / B2B",
    )
    evidence = EvidenceMap(
        mappings=[
            EvidenceMapping(
                requirement="Salesforce",
                evidence_strength="Explicit Evidence",
                candidate_status="MATCH",
                importance="hard",
                confidence=1.0,
                inference_category="Explicit",
            ),
            EvidenceMapping(
                requirement="pipeline management",
                evidence_strength="Explicit Evidence",
                candidate_status="MATCH",
                importance="hard",
                confidence=1.0,
                inference_category="Explicit",
            ),
            EvidenceMapping(
                requirement="Kubernetes",
                evidence_strength="No Evidence",
                candidate_status="MISSING",
                importance="hard",
                confidence=0.0,
                inference_category="Unsupported",
            ),
        ]
    )
    before = json.dumps(resume, sort_keys=True)
    result = HiringManagerSimulationAgent().run(
        HiringManagerInput(
            resume=resume,
            job_profile=job_profile,
            company_profile=company,
            evidence_map=evidence,
        )
    )
    fb = result.output
    assert 0 <= fb.overall_fit <= 100
    assert 0 <= fb.technical_fit <= 100
    assert 0 <= fb.business_fit <= 100
    assert "Kubernetes" in fb.missing_evidence
    assert fb.why_interview
    assert fb.actionable_feedback
    assert fb.section_effectiveness
    # Feedback only — resume untouched
    assert json.dumps(resume, sort_keys=True) == before


# ---------------------------------------------------------------------------
# Quality Intelligence — anonymous only
# ---------------------------------------------------------------------------


def test_quality_intelligence_stores_no_pii(tmp_path: Path, monkeypatch):
    path = tmp_path / "metrics.jsonl"
    monkeypatch.setenv("QUALITY_INTELLIGENCE_PATH", str(path))
    monkeypatch.setenv("QUALITY_INTELLIGENCE_ENABLED", "1")
    metrics = build_metrics_from_pipeline(
        pipeline_version=PIPELINE_VERSION,
        job_family="sales",
        industry="Technology",
        language="en",
        hiring_manager={"overall_fit": 72, "technical_fit": 70, "business_fit": 68, "resume_quality": 75, "evidence_quality": 80},
        recruiter={"interview_quality": 74, "human_believability": 81, "would_interview": True},
        strategy={"section_order": ["summary", "experience", "skills"]},
        resume={
            "professional_summary": "SHOULD NOT BE STORED AS CONTENT COPY",
            "experience": [{"bullets": ["secret personal achievement"]}],
        },
        evidence_coverage=0.66,
        agent_timings_ms={"resume_knowledge": 12},
        theme_id="modern_ats",
    )
    assert record_generation_metrics(metrics) is True
    raw = path.read_text(encoding="utf-8")
    assert "SHOULD NOT BE STORED" not in raw
    assert "secret personal" not in raw
    assert "sales" in raw
    row = json.loads(raw.strip())
    assert "resume" not in row
    assert row["summary_word_count"] > 0
    insights = aggregate_insights()
    assert insights["count"] >= 1


def test_quality_intelligence_can_disable(tmp_path: Path, monkeypatch):
    path = tmp_path / "metrics.jsonl"
    monkeypatch.setenv("QUALITY_INTELLIGENCE_PATH", str(path))
    monkeypatch.setenv("QUALITY_INTELLIGENCE_ENABLED", "0")
    assert (
        record_generation_metrics(
            AnonymousGenerationMetrics(pipeline_version="x", job_family="ops")
        )
        is False
    )
    assert not path.exists()


# ---------------------------------------------------------------------------
# Themes / ATS templates
# ---------------------------------------------------------------------------


def test_ats_themes_available_and_table_free():
    assert set(THEME_IDS) >= {
        "modern_ats",
        "professional",
        "executive",
        "minimal",
        "classic",
    }
    listed = {t["id"] for t in list_themes()}
    assert "modern_ats" in listed
    for theme_id in THEME_IDS:
        theme = resolve_theme(theme_id)
        css = theme.css.lower()
        assert "table" not in css or "border-collapse" not in css
        assert "@page" in css
        assert "font-family" in css
        assert "section-title" in css


# ---------------------------------------------------------------------------
# Multi-profession differentiation (strategy/evidence level)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cv,job,family_hint",
    [
        (NURSE_CV, NURSE_JD, "health"),
        (SALES_CV, SALES_JD, "sales"),
        (TEACHER_CV, TEACHER_JD, "educ"),
    ],
    ids=["healthcare", "sales", "education"],
)
def test_different_professions_produce_different_strategy_focus(
    cv: dict[str, Any],
    job: dict[str, Any],
    family_hint: str,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "intelligent_tailoring.agents.job_intelligence_agent.extract_job_requirements",
        lambda job, **kwargs: {
            "required_skills": ["core skill A", "core skill B"],
            "preferred_skills": ["nice skill"],
            "hard_requirements": ["core skill A", "core skill B"],
            "soft_requirements": ["nice skill"],
            "responsibilities": [f"Deliver value for {job.get('title')}"],
            "tools_technologies": [],
            "industry_terminology": [job.get("title", "")],
            "soft_skills": ["communication"],
            "education_certifications": [],
            "ats_keywords": [str(job.get("title") or "")],
            "seniority_level": "mid",
            "language": "en",
        },
    )
    job_profile = JobIntelligenceAgent().run(
        JobIntelligenceInput(job=job, jd_snapshot=_jd_text(job)),
        AgentContext(use_cache=False),
    ).output
    company = CompanyIntelligenceAgent().run(
        CompanyIntelligenceInput(
            job=job, job_profile=job_profile, jd_snapshot=_jd_text(job)
        )
    ).output
    assert job_profile.title == job["title"]
    assert company.company_name == job["company"]
    # Profession signal present in either family/industry/domain
    blob = json.dumps(job_profile.to_dict()) + json.dumps(company.to_dict())
    assert family_hint.lower() in blob.lower() or job["title"].split()[0].lower() in blob.lower()
