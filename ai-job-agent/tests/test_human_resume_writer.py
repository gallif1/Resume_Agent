"""Tests for Human Resume Writer, validators, fact lock, and themes."""

from __future__ import annotations

import copy

import pytest

from intelligent_tailoring.services.human_resume_writer import write_human_resume
from intelligent_tailoring.services.senior_recruiter_review import review_resume
from intelligent_tailoring.themes.modern_template_manager import (
    DEFAULT_THEME,
    list_themes,
    resolve_theme,
)
from intelligent_tailoring.writing.ai_detector import detect_ai_writing
from intelligent_tailoring.writing.fact_lock import compare_facts, enforce_fact_lock
from intelligent_tailoring.writing.grammar_validator import GrammarValidator, validate_grammar
from intelligent_tailoring.writing.style_validator import (
    StyleValidator,
    WritingQualityValidator,
    evaluate_writing_quality,
)
from intelligent_tailoring.writing.writing_pipeline import run_human_writing_stage


def _sample_resume(**overrides):
    base = {
        "professional_title": "Backend Engineer",
        "professional_summary": (
            "Results-driven professional with a proven track record and strong "
            "understanding of Python services. Passionate about delivering "
            "exceptional results in fast-paced environments."
        ),
        "summary": (
            "Results-driven professional with a proven track record and strong "
            "understanding of Python services. Passionate about delivering "
            "exceptional results in fast-paced environments."
        ),
        "skills": [
            "Languages: Python, SQL",
            "Backend & Frameworks: FastAPI, SQLAlchemy",
            "Cloud & DevOps: Docker, AWS",
        ],
        "experience": [
            {
                "company": "Acme Corp",
                "title": "Software Engineer",
                "dates": "2021 – Present",
                "bullets": [
                    "Responsible for implementing REST APIs with FastAPI and PostgreSQL",
                    "Worked on monitoring dashboards for production services",
                    "Implemented CRUD endpoints for internal tools",
                    "Implemented automated tests for billing workflows",
                    "Implemented Docker packaging for service deployments",
                ],
            }
        ],
        "projects": [
            {
                "name": "Ops Monitor",
                "description": "Created monitoring system.",
                "bullets": [
                    "Utilized Python and Docker to collect service metrics",
                    "Leveraged PostgreSQL for historical metric storage",
                ],
            }
        ],
        "education": [{"school": "State University", "degree": "B.Sc. Computer Science"}],
        "certifications": ["AWS Cloud Practitioner"],
    }
    base.update(overrides)
    return base


PROFESSION_SAMPLES = {
    "backend": _sample_resume(),
    "frontend": _sample_resume(
        professional_title="Frontend Engineer",
        professional_summary=(
            "Highly motivated frontend developer with extensive experience in React. "
            "Results-driven team player passionate about cutting-edge interfaces."
        ),
        skills=["Languages: TypeScript, JavaScript", "Frameworks: React, Next.js"],
        experience=[
            {
                "company": "Pixel Labs",
                "title": "Frontend Developer",
                "dates": "2020 – Present",
                "bullets": [
                    "Responsible for building React dashboards for operations teams",
                    "Worked on accessibility improvements across core pages",
                ],
            }
        ],
        projects=[
            {
                "name": "Design System",
                "description": "Internal component library.",
                "bullets": ["Developed reusable React components used by three product teams"],
            }
        ],
    ),
    "devops": _sample_resume(
        professional_title="DevOps Engineer",
        professional_summary=(
            "Seasoned professional with knowledge of CI/CD and cloud infrastructure. "
            "Passionate about reliable deployments."
        ),
        skills=["Cloud & DevOps: Kubernetes, Terraform, AWS", "Languages: Python, Bash"],
        experience=[
            {
                "company": "CloudNine",
                "title": "DevOps Engineer",
                "dates": "2019 – Present",
                "bullets": [
                    "Responsible for maintaining Kubernetes clusters and CI pipelines",
                    "Worked on Terraform modules for staging environments",
                ],
            }
        ],
        projects=[],
    ),
    "qa": _sample_resume(
        professional_title="QA Engineer",
        professional_summary=(
            "Detail-oriented professional with a proven track record in test automation."
        ),
        skills=["Tools: Playwright, pytest", "Languages: Python"],
        experience=[
            {
                "company": "Quality First",
                "title": "QA Engineer",
                "dates": "2021 – Present",
                "bullets": [
                    "Responsible for writing end-to-end tests with Playwright",
                    "Worked on regression suites for release validation",
                ],
            }
        ],
        projects=[],
        certifications=[],
    ),
    "customer_service": _sample_resume(
        professional_title="Customer Support Specialist",
        professional_summary=(
            "Highly motivated customer service professional passionate about helping clients."
        ),
        skills=["Tools: Zendesk, Salesforce", "Languages: English, Hebrew"],
        experience=[
            {
                "company": "HelpDesk Co",
                "title": "Support Specialist",
                "dates": "2022 – Present",
                "bullets": [
                    "Responsible for resolving customer tickets in Zendesk",
                    "Worked on onboarding guides for new accounts",
                ],
            }
        ],
        projects=[],
        education=[{"school": "City College", "degree": "BA Communications"}],
        certifications=[],
    ),
    "teacher": _sample_resume(
        professional_title="Mathematics Teacher",
        professional_summary=(
            "Dedicated professional with a passion for education and proven track record."
        ),
        skills=["Classroom instruction", "Curriculum planning", "Parent communication"],
        experience=[
            {
                "company": "Lincoln High School",
                "title": "Math Teacher",
                "dates": "2018 – Present",
                "bullets": [
                    "Responsible for teaching algebra and geometry to grades 9-11",
                    "Worked on after-school tutoring for struggling learners",
                ],
            }
        ],
        projects=[],
        education=[{"school": "Teachers College", "degree": "B.Ed. Mathematics"}],
        certifications=["Teaching License"],
    ),
    "sales": _sample_resume(
        professional_title="Account Executive",
        professional_summary=(
            "Results-driven sales professional with a proven track record of hitting quotas."
        ),
        skills=["Salesforce", "Negotiation", "Pipeline management"],
        experience=[
            {
                "company": "Northwind Sales",
                "title": "Account Executive",
                "dates": "2020 – Present",
                "bullets": [
                    "Responsible for managing a portfolio of mid-market accounts",
                    "Worked on closing renewal deals worth $1.2M annually",
                ],
            }
        ],
        projects=[],
        certifications=[],
    ),
    "healthcare": _sample_resume(
        professional_title="Registered Nurse",
        professional_summary=(
            "Compassionate healthcare professional passionate about patient outcomes."
        ),
        skills=["Patient care", "Electronic health records", "Triage"],
        experience=[
            {
                "company": "City General Hospital",
                "title": "Registered Nurse",
                "dates": "2019 – Present",
                "bullets": [
                    "Responsible for coordinating care for medical-surgical patients",
                    "Worked on triage workflows during peak admission periods",
                ],
            }
        ],
        projects=[],
        certifications=["RN License"],
    ),
    "administration": _sample_resume(
        professional_title="Office Administrator",
        professional_summary=(
            "Organized administrative professional with extensive experience in office operations."
        ),
        skills=["Microsoft Office", "Scheduling", "Vendor coordination"],
        experience=[
            {
                "company": "Harbor Logistics",
                "title": "Office Administrator",
                "dates": "2017 – Present",
                "bullets": [
                    "Responsible for scheduling meetings and maintaining records",
                    "Worked on vendor invoice tracking and office supply orders",
                ],
            }
        ],
        projects=[],
        certifications=[],
    ),
}


def test_fact_lock_rejects_invented_tech_and_metrics():
    baseline = _sample_resume()
    bad = copy.deepcopy(baseline)
    bad["experience"][0]["bullets"][0] = (
        "Built Vue.js microservices that improved engagement by 40%"
    )
    comparison = compare_facts(baseline, bad)
    assert comparison["passed"] is False
    assert any("novel_" in v for v in comparison["violations"])

    locked = enforce_fact_lock(baseline, bad)
    assert locked["reverted"] is True
    assert compare_facts(baseline, locked["resume"])["passed"] is True


def test_fact_lock_allows_wording_only_changes():
    baseline = _sample_resume()
    polished = copy.deepcopy(baseline)
    polished["professional_summary"] = (
        "Backend engineer specializing in Python services with FastAPI and SQLAlchemy. "
        "Builds reliable APIs and operational tooling for production systems at Acme Corp."
    )
    polished["experience"][0]["bullets"][0] = (
        "Developed REST APIs with FastAPI and PostgreSQL for internal platforms"
    )
    assert compare_facts(baseline, polished)["passed"] is True


def test_grammar_validator_flags_ai_cliches_and_repeated_openings():
    result = validate_grammar(_sample_resume())
    assert result["regeneration_required"] is True
    patterns = " ".join(
        p for issue in result["issues"] for p in issue.get("patterns") or []
    )
    assert "ai_cliche" in patterns or "repeated_opening" in patterns
    assert GrammarValidator().validate(_sample_resume())["score"] < 100


def test_style_and_ai_detector_flag_generic_writing():
    resume = _sample_resume()
    style = evaluate_writing_quality(resume, threshold=75)
    assert "naturalness" in style["dimensions"]
    assert "ai_likeness" in style["dimensions"]
    assert style["regeneration_required"] is True or style["overall_score"] < 95

    ai = detect_ai_writing(resume)
    assert ai["signals"]
    assert WritingQualityValidator(threshold=80).validate(resume)
    assert StyleValidator(threshold=80).validate(resume)


def test_human_writer_deterministic_removes_cliches_without_llm():
    baseline = _sample_resume()
    result = write_human_resume(
        validated_resume=baseline,
        strategy={"candidate_value_proposition": "reliable backend delivery"},
        knowledge_base={"facts": []},
        output_language="en",
        allow_llm=False,
    )
    polished = result["tailored_resume"]
    summary = polished["professional_summary"].lower()
    assert "results-driven" not in summary
    assert "passionate about" not in summary
    assert compare_facts(baseline, polished)["passed"] is True
    assert result["fact_lock"]["passed"] is True
    # Weak openings upgraded
    bullets = " ".join(polished["experience"][0]["bullets"]).lower()
    assert "responsible for" not in bullets


def test_senior_recruiter_heuristic_requests_regen_for_ai_text():
    review = review_resume(resume=_sample_resume(), allow_llm=False)
    assert review["approved"] is False
    assert review["sections_to_regenerate"]


def test_writing_pipeline_preserves_facts_across_professions():
    strategy = {
        "target_positioning": "professional contributor",
        "candidate_value_proposition": "clear domain strengths",
        "skills_to_emphasize": [],
        "tone": "professional",
    }
    for name, resume in PROFESSION_SAMPLES.items():
        baseline = copy.deepcopy(resume)
        stage = run_human_writing_stage(
            validated_resume=baseline,
            strategy=strategy,
            knowledge_base={"facts": []},
            output_language="en",
            allow_llm=False,
            max_review_cycles=2,
        )
        assert stage["facts_unchanged"] is True, name
        assert compare_facts(baseline, stage["tailored_resume"])["passed"] is True, name
        polished_summary = stage["tailored_resume"]["professional_summary"].lower()
        assert "results-driven" not in polished_summary, name
        assert "passionate about" not in polished_summary, name


def test_writer_does_not_receive_job_description_in_strategy_sanitizer():
    from intelligent_tailoring.prompts.human_writer_prompts import (
        sanitize_strategy_for_writer,
    )

    clean = sanitize_strategy_for_writer(
        {
            "jd_text": "Must know Vue.js and increase revenue 300%",
            "keywords_to_insert": ["Vue.js"],
            "candidate_value_proposition": "reliable delivery",
            "tone": "professional",
        }
    )
    assert "jd_text" not in clean
    assert "keywords_to_insert" not in clean
    assert clean["candidate_value_proposition"] == "reliable delivery"


def test_modern_themes_resolve():
    assert resolve_theme(None).id == DEFAULT_THEME
    assert resolve_theme("executive").id == "executive"
    assert resolve_theme("legacy").id == "classic"
    themes = list_themes()
    assert len(themes) == 5
