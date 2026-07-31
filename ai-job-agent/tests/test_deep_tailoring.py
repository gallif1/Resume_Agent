"""Tests for deep job-family tailoring: scoring, rebuild, similarity, differentiation."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from intelligent_tailoring.pipeline import run_intelligent_tailoring
from intelligent_tailoring.services.job_family import detect_job_family
from intelligent_tailoring.services.resume_analyzer import resume_facts_to_baseline_resume
from intelligent_tailoring.services.resume_rebuilder import rebuild_resume_structure
from intelligent_tailoring.services.resume_scorer import score_resume_content
from intelligent_tailoring.services.similarity import compare_resume_pair
from intelligent_tailoring.services.tailoring_strategy_builder import build_tailoring_strategy
from intelligent_tailoring.stages.resume_extraction import extract_structured_resume

FULL_STACK_RESUME = {
    "contact": {"name": "Gal Lifshitz", "email": "gal@example.com"},
    "raw_text": (
        "Gal Lifshitz — Technical Support Specialist at Comax Smart ERP (2023-2024). "
        "Built REST APIs with Python/FastAPI and PostgreSQL. Deployed to AWS EC2. "
        "Developed React and React Native UI for restaurant ordering app. "
        "Debugged production issues, investigated logs, assisted customers. "
        "Projects: Server Monitor System with ThreadPoolExecutor and AWS deployment; "
        "Restaurant App with React Native and FastAPI backend."
    ),
    "skills": {
        "programming_languages": ["Python", "JavaScript"],
        "frameworks": ["FastAPI", "React", "React Native", "Angular"],
        "databases": ["PostgreSQL", "MongoDB"],
        "cloud": ["AWS", "Git", "CI/CD"],
        "other": ["HTML", "CSS", "WebSockets"],
    },
    "experience": {
        "job_titles": ["Technical Support Specialist"],
        "years_of_experience_estimate": 1,
        "roles": [
            {
                "company": "Comax Smart ERP",
                "title": "Technical Support Specialist",
                "dates": "2023-2024",
                "bullets": [
                    "Built REST APIs with Python/FastAPI and PostgreSQL",
                    "Deployed services to AWS EC2 with CI/CD pipelines",
                    "Developed React Native UI for restaurant ordering application",
                    "Debugged production issues and investigated application logs",
                    "Assisted customers with ERP technical problems",
                    "Documented troubleshooting steps and reproduction cases",
                ],
            }
        ],
    },
    "projects": [
        {
            "name": "Server Monitor System",
            "description": "Infrastructure monitoring tool",
            "bullets": [
                "Implemented ThreadPoolExecutor for concurrent health checks",
                "Deployed monitoring service to AWS with automated alerts",
            ],
        },
        {
            "name": "Restaurant App",
            "description": "Full-stack ordering application",
            "bullets": [
                "Built FastAPI backend with PostgreSQL data layer",
                "Created React Native mobile UI with responsive design",
            ],
        },
    ],
}

JOB_SPECS = {
    "backend": {
        "title": "Backend Engineer",
        "full_description": (
            "Required: Python, FastAPI, REST APIs, PostgreSQL, SQL. "
            "Responsibilities: design backend services, database schema, API validation."
        ),
    },
    "frontend": {
        "title": "Frontend Developer",
        "full_description": (
            "Required: React, React Native, Angular, HTML, CSS, responsive UI. "
            "Responsibilities: build client interfaces, integrate REST APIs."
        ),
    },
    "devops": {
        "title": "DevOps Engineer",
        "full_description": (
            "Required: AWS, CI/CD, deployment, monitoring, infrastructure automation. "
            "Responsibilities: manage cloud infrastructure, logging, server health."
        ),
    },
    "qa": {
        "title": "QA Engineer",
        "full_description": (
            "Required: testing, debugging, validation, bug reproduction, documentation. "
            "Responsibilities: verify quality, troubleshoot defects, write test cases."
        ),
    },
    "support": {
        "title": "Technical Support Engineer",
        "full_description": (
            "Required: customer support, troubleshooting, root cause analysis, ERP. "
            "Responsibilities: investigate issues, communicate with customers, analyze logs."
        ),
    },
}


def _job_for(family: str) -> dict[str, Any]:
    spec = JOB_SPECS[family]
    return {
        "id": hash(family) % 1000,
        "title": spec["title"],
        "company": "TestCo",
        "full_description": spec["full_description"],
    }


def _requirements_for(family: str) -> dict[str, Any]:
    jd = JOB_SPECS[family]["full_description"]
    return {
        "required_skills": jd.split(),
        "preferred_skills": [],
        "responsibilities": [jd],
        "tools_technologies": [],
        "industry_terminology": [family],
        "seniority_level": "junior",
        "soft_skills": [],
        "education_certifications": [],
        "ats_keywords": [family, JOB_SPECS[family]["title"]],
        "hard_requirements": [],
        "soft_requirements": [],
        "language": "en",
    }


def _job_analysis(family: str) -> dict[str, Any]:
    from intelligent_tailoring.services.job_analyzer import analyze_job

    return analyze_job(
        _job_for(family),
        requirements=_requirements_for(family),
    )


@pytest.fixture
def resume_facts():
    return extract_structured_resume(FULL_STACK_RESUME)


class TestJobFamilyDetection:
    def test_detects_all_families(self):
        for family in JOB_SPECS:
            job = _job_for(family)
            detected = detect_job_family(job["title"], _requirements_for(family))
            assert detected == family, f"expected {family}, got {detected}"


class TestResumeScoring:
    def test_fastapi_scores_higher_for_backend_than_frontend(self, resume_facts):
        backend_strategy = build_tailoring_strategy(
            job_analysis=_job_analysis("backend"),
            resume_facts=resume_facts,
            evidence_map=[],
            ranked_requirements=[],
            language="en",
        )
        frontend_strategy = build_tailoring_strategy(
            job_analysis=_job_analysis("frontend"),
            resume_facts=resume_facts,
            evidence_map=[],
            ranked_requirements=[],
            language="en",
        )
        backend_scores = score_resume_content(
            resume_facts=resume_facts,
            strategy=backend_strategy,
            job_analysis=_job_analysis("backend"),
            evidence_map=[],
        )
        frontend_scores = score_resume_content(
            resume_facts=resume_facts,
            strategy=frontend_strategy,
            job_analysis=_job_analysis("frontend"),
            evidence_map=[],
        )

        def api_bullet_score(scores: dict[str, Any]) -> int:
            for b in scores.get("experience_bullets") or []:
                if "fastapi" in str(b.get("text") or "").lower():
                    return int(b.get("score") or 0)
            return 0

        def react_bullet_score(scores: dict[str, Any]) -> int:
            for b in scores.get("experience_bullets") or []:
                if "react" in str(b.get("text") or "").lower():
                    return int(b.get("score") or 0)
            return 0

        assert api_bullet_score(backend_scores) > react_bullet_score(backend_scores)
        assert react_bullet_score(frontend_scores) >= api_bullet_score(frontend_scores)

    def test_server_monitor_ranks_first_for_devops(self, resume_facts):
        strategy = build_tailoring_strategy(
            job_analysis=_job_analysis("devops"),
            resume_facts=resume_facts,
            evidence_map=[],
            ranked_requirements=[],
            language="en",
        )
        scores = score_resume_content(
            resume_facts=resume_facts,
            strategy=strategy,
            job_analysis=_job_analysis("devops"),
            evidence_map=[],
        )
        rebuilt = rebuild_resume_structure(
            resume_facts=resume_facts,
            scores=scores,
            strategy=strategy,
        )
        project_names = [
            str(p.get("name") or "") for p in rebuilt.get("projects") or []
        ]
        assert project_names[0].lower().startswith("server monitor")


class TestCrossFamilyDifferentiation:
    def test_rebuilt_structures_differ_across_families(self, resume_facts):
        rebuilt_by_family: dict[str, dict[str, Any]] = {}
        for family in JOB_SPECS:
            strategy = build_tailoring_strategy(
                job_analysis=_job_analysis(family),
                resume_facts=resume_facts,
                evidence_map=[],
                ranked_requirements=[],
                language="en",
            )
            scores = score_resume_content(
                resume_facts=resume_facts,
                strategy=strategy,
                job_analysis=_job_analysis(family),
                evidence_map=[],
            )
            rebuilt_by_family[family] = rebuild_resume_structure(
                resume_facts=resume_facts,
                scores=scores,
                strategy=strategy,
            )

        backend = rebuilt_by_family["backend"]
        frontend = rebuilt_by_family["frontend"]
        devops = rebuilt_by_family["devops"]

        # Skills ordering differs
        assert backend["skills"] != frontend["skills"]

        # Experience bullet order differs between backend and frontend
        backend_bullets = backend["experience"][0]["bullets"]
        frontend_bullets = frontend["experience"][0]["bullets"]
        assert backend_bullets[0] != frontend_bullets[0]

        # DevOps puts Server Monitor first
        devops_projects = [p["name"] for p in devops["projects"]]
        assert "Server Monitor" in devops_projects[0]

        # Cross-pair similarity should be below identical threshold
        pair_sim = compare_resume_pair(backend, frontend)
        assert pair_sim["overall_similarity"] < 0.95


def _family_generation(family: str) -> dict[str, Any]:
    """LLM stub tailored resume emphasizing the target family."""
    emphasis = {
        "backend": {
            "summary": (
                "Backend-focused engineer specializing in FastAPI REST APIs, "
                "PostgreSQL database design, and server-side business logic."
            ),
            "first_bullet": (
                "Designed and built production REST APIs with Python/FastAPI and PostgreSQL"
            ),
            "skills": [
                "APIs & Backend: FastAPI, REST APIs, WebSockets",
                "Databases: PostgreSQL, SQL",
                "Languages: Python",
                "Cloud & DevOps: AWS",
            ],
        },
        "frontend": {
            "summary": (
                "Frontend developer focused on React, React Native, and Angular UI "
                "with responsive interfaces and REST API integration."
            ),
            "first_bullet": (
                "Developed React Native UI with responsive design for restaurant ordering app"
            ),
            "skills": [
                "Frontend Frameworks: React, React Native, Angular",
                "UI & Styling: HTML, CSS, responsive interfaces",
                "API Integration: REST APIs",
                "Languages: JavaScript",
            ],
        },
        "devops": {
            "summary": (
                "DevOps-oriented engineer with AWS deployment, CI/CD automation, "
                "monitoring, and infrastructure health management experience."
            ),
            "first_bullet": (
                "Deployed services to AWS EC2 with CI/CD pipelines and monitoring"
            ),
            "skills": [
                "Cloud & Infrastructure: AWS, EC2",
                "CI/CD & Automation: Git, CI/CD pipelines",
                "Monitoring & Logging: Server Monitor System",
                "Languages: Python",
            ],
        },
        "qa": {
            "summary": (
                "Quality-focused professional skilled in debugging, validation, "
                "bug reproduction, and reliability documentation."
            ),
            "first_bullet": (
                "Debugged production issues, documented reproduction steps and validation cases"
            ),
            "skills": [
                "Testing & Quality: debugging, validation, documentation",
                "Debugging & Analysis: bug reproduction, troubleshooting",
                "Languages: Python",
            ],
        },
        "support": {
            "summary": (
                "Technical support specialist experienced in customer communication, "
                "ERP issue investigation, root cause analysis, and log analysis."
            ),
            "first_bullet": (
                "Assisted customers with ERP technical problems through investigation and follow-up"
            ),
            "skills": [
                "Support & Troubleshooting: ERP, troubleshooting, logs",
                "Communication: customer communication, collaboration",
                "Systems & Networking: ERP systems",
            ],
        },
    }[family]

    bullets = [
        emphasis["first_bullet"],
        "Deployed services to AWS EC2 with CI/CD pipelines",
        "Developed React Native UI for restaurant ordering application",
        "Debugged production issues and investigated application logs",
        "Assisted customers with ERP technical problems",
        "Documented troubleshooting steps and reproduction cases",
    ]

    return {
        "tailored_resume": {
            "professional_title": JOB_SPECS[family]["title"],
            "professional_summary": emphasis["summary"],
            "skills": emphasis["skills"],
            "experience": [
                {
                    "company": "Comax Smart ERP",
                    "title": "Technical Support Specialist",
                    "dates": "2023-2024",
                    "bullets": bullets,
                }
            ],
            "projects": [
                {
                    "name": "Server Monitor System",
                    "description": "Infrastructure monitoring",
                    "bullets": ["AWS deployment with automated health monitoring"],
                },
                {
                    "name": "Restaurant App",
                    "description": "Ordering application",
                    "bullets": ["FastAPI backend with React Native client"],
                },
            ],
            "education": [],
            "certifications": [],
        },
        "change_log": [
            {
                "original_text": "",
                "new_text": emphasis["summary"],
                "reason": f"Job-family summary for {family}",
                "supporting_evidence": FULL_STACK_RESUME["raw_text"][:120],
                "related_job_requirement": JOB_SPECS[family]["title"],
                "inference_category": "Explicit",
                "confidence_score": 1.0,
            }
        ],
        "matched_requirements": [],
        "missing_requirements": [],
        "removed_or_deprioritized_content": [],
        "ats_keywords_added": [family],
    }


def _stage_sequence_for_family(family: str):
    requirements = _requirements_for(family)
    requirements["hard_requirements"] = requirements["required_skills"]
    inference = {"inferred_competencies": []}
    triage = {"triage": [], "section_order": []}
    generation = _family_generation(family)
    claim_llm = {"validation_warnings": []}
    queue = [requirements, inference, triage, generation, claim_llm]

    def _side_effect(*_a: Any, **_k: Any) -> dict[str, Any]:
        namespace = str(_k.get("cache_namespace") or "")
        if "human_writer" in namespace:
            return {
                "tailored_resume": generation["tailored_resume"],
                "writing_notes": ["test_stub"],
                "sections_rewritten": ["summary", "experience", "projects"],
            }
        if "recruiter_review" in namespace:
            return {
                "approved": True,
                "human_believability": 85,
                "interview_quality": 84,
                "issues": [],
                "sections_to_regenerate": [],
                "summary_feedback": "Professionally written.",
            }
        if queue:
            return queue.pop(0)
        return generation

    return _side_effect


@pytest.fixture(autouse=True)
def _ai_available(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "intelligent_tailoring.pipeline.is_ai_available", lambda: True
    )


class TestPipelineJobFamilyOutputs:
    def test_five_job_families_produce_distinct_resumes(self):
        results: dict[str, dict[str, Any]] = {}
        for family in JOB_SPECS:
            with patch(
                "intelligent_tailoring.llm_utils.call_openai_json",
                side_effect=_stage_sequence_for_family(family),
            ):
                results[family] = run_intelligent_tailoring(
                    cv_profile=FULL_STACK_RESUME,
                    job=_job_for(family),
                    use_cache=False,
                )

        summaries = {
            f: results[f]["tailored_resume"]["professional_summary"]
            for f in JOB_SPECS
        }
        assert len(set(summaries.values())) == len(JOB_SPECS)

        # Pairwise cross-similarity stays below 80%
        families = list(JOB_SPECS.keys())
        for i, fa in enumerate(families):
            for fb in families[i + 1:]:
                sim = compare_resume_pair(
                    results[fa]["tailored_resume"],
                    results[fb]["tailored_resume"],
                )
                assert sim["overall_similarity"] < 0.80, (
                    f"{fa} vs {fb} too similar: {sim['overall_similarity']}"
                )

        # Each result has tailoring report
        for family in JOB_SPECS:
            report = results[family].get("tailoring_report") or {}
            assert report.get("job_family") == family
            assert "tailoring_score" in report
            assert results[family].get("tailoring_strategy")

    def test_tailoring_report_fields(self):
        with patch(
            "intelligent_tailoring.llm_utils.call_openai_json",
            side_effect=_stage_sequence_for_family("backend"),
        ):
            result = run_intelligent_tailoring(
                cv_profile=FULL_STACK_RESUME,
                job=_job_for("backend"),
                use_cache=False,
            )
        report = result["tailoring_report"]
        assert report["bullets_rewritten"] >= 0
        assert "resume_similarity" in report
        assert report["tailoring_quality"] in ("excellent", "good", "moderate", "weak")
