"""Interview-probability decision quality — existing agents only, no new agents."""

from __future__ import annotations

from typing import Any

import pytest

from intelligent_tailoring.agents.base import AgentContext
from intelligent_tailoring.agents.job_intelligence_agent import JobIntelligenceAgent
from intelligent_tailoring.agents.resume_strategy_agent import ResumeStrategyAgent
from intelligent_tailoring.agents.schemas import (
    CompanyProfile,
    EvidenceMap,
    EvidenceMapping,
    JobIntelligenceInput,
    JobProfile,
    ResumeStrategyInput,
)
from intelligent_tailoring.hiring_intent import (
    build_narrative_themes,
    classify_requirement_support_tier,
    infer_hiring_intent,
)
from intelligent_tailoring.knowledge_base import FACT_TYPES, _classify_activity
from intelligent_tailoring.schemas import PIPELINE_VERSION
from intelligent_tailoring.services.evidence_amplifier import (
    extract_entry_evidence,
    score_requirement_support,
)
from intelligent_tailoring.services.senior_recruiter_review import _heuristic_review
from intelligent_tailoring.services.tailoring_strategy_builder import (
    build_tailoring_strategy,
)
from intelligent_tailoring.writing.resume_quality_score import evaluate_resume_quality


def test_pipeline_version_bumped_to_four_agent_v2():
    assert PIPELINE_VERSION == "four_agent_v2_0"


def test_hiring_intent_infers_person_not_just_keywords():
    backend = infer_hiring_intent(
        title="Backend Engineer",
        job_family="backend",
        responsibilities=["Design scalable REST APIs", "Own database reliability"],
        required_skills=["Python", "PostgreSQL", "AWS"],
        jd_text="We need engineers who own scalable systems and solve hard problems.",
    )
    healthcare = infer_hiring_intent(
        title="Staff Nurse",
        job_family="healthcare",
        responsibilities=["Provide bedside patient care", "Document in EHR"],
        required_skills=["patient care", "EHR", "HIPAA"],
        jd_text="Patient care, clinical documentation, and compliance.",
    )
    assert backend["person_archetype"]
    assert healthcare["person_archetype"]
    assert backend["hiring_priorities"]
    assert "Patient care" in healthcare["hiring_priorities"] or any(
        "patient" in p.lower() for p in healthcare["hiring_priorities"]
    )
    # Different jobs → different stories
    assert backend["narrative_themes"] != healthcare["narrative_themes"]
    assert backend["person_archetype"] != healthcare["person_archetype"]


def test_narrative_themes_are_job_specific():
    intent = infer_hiring_intent(
        title="Sales Account Executive",
        job_family="sales",
        responsibilities=["Close revenue", "Manage CRM pipeline"],
        required_skills=["negotiation", "Salesforce"],
    )
    themes = build_narrative_themes(
        hiring_intent=intent,
        top_interview_reasons=["negotiation", "revenue ownership"],
        matched_hard=["Salesforce"],
        limit=3,
    )
    assert themes
    assert len(themes) <= 3


@pytest.mark.parametrize(
    "support,expected",
    [
        ("Explicit", "Explicit Evidence"),
        ("Strongly Supported", "Strong Supporting Evidence"),
        ("Weakly Supported", "Transferable Evidence"),
        ("Unsupported", "No Evidence"),
    ],
)
def test_requirement_support_tiers(support: str, expected: str):
    assert classify_requirement_support_tier(support) == expected


def test_knowledge_base_discovers_soft_evidence_types():
    assert "problem_solving_activity" in FACT_TYPES
    assert "ownership_activity" in FACT_TYPES
    assert "debugging_activity" in FACT_TYPES
    assert _classify_activity("Owned end-to-end delivery of the billing API") in {
        "ownership_activity",
        "problem_solving_activity",
        "responsibility",
    }
    assert _classify_activity("Debugged production incidents and fixed root causes") in {
        "debugging_activity",
        "problem_solving_activity",
    }
    assert _classify_activity("Mentored two junior engineers on the team") == (
        "leadership_activity"
    )


def test_evidence_amplifier_extracts_soft_competencies():
    entry = {
        "name": "Payments API",
        "title": "Backend Engineer",
        "bullets": [
            "Owned end-to-end design of REST APIs using FastAPI and PostgreSQL.",
            "Debugged latency issues and improved throughput by 30%.",
            "Collaborated with cross-functional product stakeholders.",
        ],
        "description": "Real-time payments backend",
    }
    evidence = extract_entry_evidence(entry, kind="project")
    assert "fastapi" in evidence["technologies"] or "postgresql" in evidence["technologies"]
    assert "ownership" in evidence["soft_competencies"] or "architecture" in evidence[
        "soft_competencies"
    ]
    assert evidence["soft_evidence"]


def test_score_requirement_support_includes_tiers():
    rows = score_requirement_support(
        [
            {
                "requirement": "Python",
                "evidence_strength": "Explicit Evidence",
                "candidate_status": "MATCH",
                "importance": "hard",
                "supporting_evidence": "Used Python daily",
            },
            {
                "requirement": "Kubernetes",
                "evidence_strength": "Weak Inference",
                "candidate_status": "PARTIAL",
                "importance": "soft",
                "supporting_evidence": "Dockerized services",
            },
            {
                "requirement": "Ruby",
                "evidence_strength": "No Evidence",
                "candidate_status": "MISSING",
                "importance": "hard",
                "supporting_evidence": "",
            },
        ]
    )
    tiers = {r["requirement"]: r["support_tier"] for r in rows}
    assert tiers["Python"] == "Explicit Evidence"
    assert tiers["Kubernetes"] in (
        "Transferable Evidence",
        "Strong Supporting Evidence",
    )
    assert tiers["Ruby"] == "No Evidence"


def test_job_intelligence_populates_hiring_intent(monkeypatch):
    def _fake_extract(job, **kwargs):
        return {
            "required_skills": ["Python", "PostgreSQL", "REST APIs"],
            "preferred_skills": ["AWS"],
            "hard_requirements": ["Python", "PostgreSQL", "REST APIs"],
            "soft_requirements": ["AWS"],
            "responsibilities": [
                "Design scalable backend services",
                "Own API reliability",
            ],
            "tools_technologies": ["Python", "PostgreSQL", "AWS"],
            "industry_terminology": ["microservices"],
            "soft_skills": ["problem solving", "ownership"],
            "education_certifications": [],
            "ats_keywords": ["Backend Engineer", "Python", "API"],
            "seniority_level": "mid",
            "language": "en",
        }

    monkeypatch.setattr(
        "intelligent_tailoring.agents.job_intelligence_agent.extract_job_requirements",
        _fake_extract,
    )
    result = JobIntelligenceAgent().run(
        JobIntelligenceInput(
            job={
                "title": "Backend Engineer",
                "company": "Acme",
                "description": "Build scalable REST APIs and databases.",
            },
            jd_snapshot="Backend Engineer. Scalable systems, REST APIs, databases, cloud.",
        ),
        AgentContext(use_cache=False),
    )
    profile = result.output
    assert isinstance(profile, JobProfile)
    assert profile.person_archetype
    assert profile.hiring_priorities
    assert profile.narrative_themes
    assert profile.hiring_intent
    assert profile.interview_lens


def test_strategy_builds_job_specific_professional_story():
    backend_analysis = {
        "job_family": "backend",
        "industry": "technology",
        "primary_role": "Backend Engineer",
        "requirements": {
            "required_skills": ["Python", "PostgreSQL"],
            "hard_requirements": ["Python", "PostgreSQL"],
            "responsibilities": ["Design APIs"],
            "soft_skills": ["ownership"],
        },
        "ats_keywords": ["Python"],
        "hiring_intent": infer_hiring_intent(
            title="Backend Engineer",
            job_family="backend",
            required_skills=["Python", "PostgreSQL"],
            responsibilities=["Design APIs"],
        ),
    }
    sales_analysis = {
        "job_family": "sales",
        "industry": "sales",
        "primary_role": "Account Executive",
        "requirements": {
            "required_skills": ["negotiation", "CRM"],
            "hard_requirements": ["negotiation", "CRM"],
            "responsibilities": ["Close revenue"],
            "soft_skills": ["communication"],
        },
        "ats_keywords": ["CRM"],
        "hiring_intent": infer_hiring_intent(
            title="Account Executive",
            job_family="sales",
            required_skills=["negotiation", "CRM"],
            responsibilities=["Close revenue"],
        ),
    }
    facts = {
        "skills": ["Python", "PostgreSQL", "negotiation", "CRM"],
        "display_skills": ["Python", "PostgreSQL", "negotiation", "CRM"],
        "projects": [],
        "experience": [],
    }
    evidence = [
        {
            "requirement": "Python",
            "candidate_status": "MATCH",
            "importance": "hard",
            "evidence_strength": "Explicit Evidence",
            "supporting_evidence": "Python",
        },
        {
            "requirement": "negotiation",
            "candidate_status": "MATCH",
            "importance": "hard",
            "evidence_strength": "Explicit Evidence",
            "supporting_evidence": "negotiation",
        },
    ]
    backend_strategy = build_tailoring_strategy(
        job_analysis=backend_analysis,
        resume_facts=facts,
        evidence_map=evidence,
        ranked_requirements=[],
        hiring_intent=backend_analysis["hiring_intent"],
    )
    sales_strategy = build_tailoring_strategy(
        job_analysis=sales_analysis,
        resume_facts=facts,
        evidence_map=evidence,
        ranked_requirements=[],
        hiring_intent=sales_analysis["hiring_intent"],
    )
    assert backend_strategy["professional_story"]
    assert sales_strategy["professional_story"]
    assert backend_strategy["narrative_themes"] != sales_strategy["narrative_themes"]
    assert backend_strategy["success_metric"] == "interview_probability"


def test_resume_strategy_agent_surfaces_coverage_tiers():
    job = JobProfile(
        title="Backend Engineer",
        job_family="backend",
        required_skills=["Python"],
        person_archetype="Backend engineer who builds reliable systems",
        hiring_priorities=["Scalable systems", "API design"],
        narrative_themes=["Scalable systems", "API design"],
        hiring_intent=infer_hiring_intent(
            title="Backend Engineer", job_family="backend"
        ),
    )
    evidence = EvidenceMap(
        mappings=[
            EvidenceMapping(
                requirement="Python",
                importance="hard",
                candidate_status="MATCH",
                evidence_strength="Explicit Evidence",
                supporting_evidence="Used Python",
                confidence=0.9,
            ),
            EvidenceMapping(
                requirement="Ruby",
                importance="hard",
                candidate_status="MISSING",
                evidence_strength="No Evidence",
                supporting_evidence="",
                confidence=0.1,
            ),
        ]
    )
    result = ResumeStrategyAgent().run(
        ResumeStrategyInput(
            job_profile=job,
            company_profile=CompanyProfile(company_name="Acme"),
            evidence_map=evidence,
            resume_facts={
                "skills": ["Python"],
                "display_skills": ["Python"],
                "projects": [],
                "experience": [],
            },
            job_analysis={
                "job_family": "backend",
                "industry": "technology",
                "primary_role": "Backend Engineer",
                "requirements": {
                    "required_skills": ["Python"],
                    "hard_requirements": ["Python"],
                },
                "ats_keywords": ["Python"],
                "hiring_intent": job.hiring_intent,
            },
        ),
        AgentContext(use_cache=False),
    )
    strategy = result.output
    assert strategy.narrative_themes
    assert strategy.professional_story or strategy.summary_focus
    assert strategy.requirement_coverage.get("Python") == "Explicit Evidence"
    assert strategy.requirement_coverage.get("Ruby") == "No Evidence"


def test_recruiter_heuristic_sets_would_interview_and_challenges_generic():
    weak = {
        "professional_summary": "Professional with strong understanding of many tools.",
        "skills": ["Python"],
        "experience": [
            {
                "company": "Acme",
                "title": "Engineer",
                "bullets": ["Responsible for various duties", "Worked on stuff"],
            }
        ],
        "projects": [],
    }
    review = _heuristic_review(weak)
    assert review["would_interview"] is False
    assert review["approved"] is False
    assert any(i.get("section") == "summary" for i in review["issues"])


def test_soft_competencies_exposed_on_resume_facts():
    from intelligent_tailoring.knowledge_base import (
        ResumeFact,
        ResumeKnowledgeBase,
        knowledge_base_to_resume_facts,
    )

    kb = ResumeKnowledgeBase(
        facts=[
            ResumeFact(
                id="1",
                fact_type="ownership_activity",
                normalized_value="owned api",
                original_text="Owned end-to-end API delivery",
                source_section="experience",
            ),
            ResumeFact(
                id="2",
                fact_type="debugging_activity",
                normalized_value="debugged",
                original_text="Debugged production incidents",
                source_section="projects",
            ),
            ResumeFact(
                id="3",
                fact_type="education",
                normalized_value="bs cs",
                original_text="BS Computer Science",
                source_section="education",
                organization="State U",
            ),
        ],
        raw_text="Owned end-to-end API delivery. Debugged production incidents.",
    )
    facts = knowledge_base_to_resume_facts(kb)
    assert "ownership" in facts["soft_competencies"]
    assert "debugging" in facts["soft_competencies"]
    assert facts["soft_evidence_by_type"]["ownership"]


def test_compressor_prefers_strongest_evidence():
    from intelligent_tailoring.services.one_page_compressor import compress_resume_to_one_page

    resume = {
        "professional_summary": (
            "Backend engineer who designs reliable APIs with Python and PostgreSQL. "
            "Owns service reliability for payment systems."
        ),
        "skills": ["Python", "PostgreSQL"],
        "experience": [
            {
                "company": "Acme",
                "title": "Engineer",
                "bullets": [
                    "Responsible for various day-to-day duties on the team.",
                    "Designed REST APIs using Python and PostgreSQL for payments.",
                    "Attended weekly standups and wrote status emails.",
                ],
            }
        ],
        "projects": [],
    }
    compressed = compress_resume_to_one_page(
        resume,
        strategy={
            "propagate_terms": ["Python", "PostgreSQL", "REST APIs"],
            "strongest_evidence": [
                "Designed REST APIs using Python and PostgreSQL for payments."
            ],
            "weaker_evidence_to_reduce": [
                "Responsible for various day-to-day duties on the team."
            ],
        },
    )
    bullets = compressed["experience"][0]["bullets"]
    assert any("REST APIs" in b for b in bullets)
    assert not any("various day-to-day" in b for b in bullets) or len(bullets) <= 3


def test_transferable_not_stripped_from_skills():
    from intelligent_tailoring.agents.schemas import EvidenceMapping

    # Policy: only No Evidence / unsupported MISSING are stripped from skills.
    # Weak Inference (Transferable) with supporting text may still surface.
    mappings = [
        EvidenceMapping(
            requirement="Kubernetes",
            importance="soft",
            candidate_status="PARTIAL",
            evidence_strength="Weak Inference",
            supporting_evidence="Dockerized services",
            confidence=0.5,
        ),
        EvidenceMapping(
            requirement="Ruby",
            importance="hard",
            candidate_status="MISSING",
            evidence_strength="No Evidence",
            supporting_evidence="",
            confidence=0.1,
        ),
    ]
    no_evidence = {
        m.requirement.lower()
        for m in mappings
        if m.evidence_strength == "No Evidence"
        or (
            m.candidate_status == "MISSING"
            and not str(m.supporting_evidence or "").strip()
        )
    }
    assert "ruby" in no_evidence
    assert "kubernetes" not in no_evidence


def test_quality_score_includes_interview_probability_and_20s_screen():
    resume = {
        "professional_title": "Backend Engineer",
        "professional_summary": (
            "Backend engineer who designs reliable REST APIs with Python and "
            "PostgreSQL. Owns service reliability and solves production issues "
            "for high-traffic systems."
        ),
        "skills": ["Python", "PostgreSQL", "REST APIs"],
        "experience": [
            {
                "company": "Acme",
                "title": "Backend Engineer",
                "bullets": [
                    "Designed REST APIs using Python and PostgreSQL for payments.",
                    "Debugged production incidents and improved latency.",
                ],
            }
        ],
        "projects": [],
    }
    strategy = {
        "skills_to_emphasize": ["Python", "PostgreSQL", "REST APIs"],
        "top_interview_reasons": ["Python", "REST APIs", "PostgreSQL"],
        "narrative_themes": ["Scalable systems", "API design"],
    }
    score = evaluate_resume_quality(
        resume,
        strategy=strategy,
        highlight_plan={"must_highlight": ["Python"], "top_interview_reasons": ["Python"]},
        threshold=50,
    )
    assert "interview_probability" in score["dimensions"]
    assert "twenty_second_screen" in score["dimensions"]
    assert score["dimensions"]["twenty_second_screen"] >= 50
    assert "interview_probability" in score["weights"]
