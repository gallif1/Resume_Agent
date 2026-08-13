"""Regression: Bylith Frontend tailor must emphasize UI evidence, not invent backend.

Mirrors the mobile preview failure where a Frontend Developer CV led with
Machine Learning / AWS / microservices / Trike Platform / Tel Aviv University.
"""

from __future__ import annotations

import json

from intelligent_tailoring.canonical_resume import (
    ensure_minimum_content_from_source,
    restore_missing_content_from_source,
)
from intelligent_tailoring.claim_validator import (
    project_name_supported,
    role_title_supported,
    statement_supported_by_evidence,
    validate_claims,
)
from intelligent_tailoring.skill_taxonomy import (
    category_order_for_role,
    normalize_skill_lines,
)
from intelligent_tailoring.summary_builder import (
    build_professional_summary,
    build_summary_plan,
)
from match_tailor_service import skill_supported_by_source
from tests.test_foundational_identity_hallucination import GAL_TEL_HAI_SOURCE


def _fabricated_bylith_frontend_resume() -> dict:
    return {
        "professional_title": "Frontend Developer",
        "professional_summary": (
            "Professional and focused Frontend Development experience. Frontend "
            "Developer with hands-on experience building scalable solutions and "
            "applications, using Machine Learning, Linux, OS, and GraphQL. "
            "Experience includes automated testing, composition of indices, "
            "integration tests and scalable testing utilities. Background includes "
            "backend cloud infrastructure using AWS EC2, SQS, SES and implemented "
            "linear CI/CD."
        ),
        "skills": [
            "Frontend: React, Redux-Sagas, Angular, HTML, CSS, SCSS",
            "Backend & Frameworks: Node.js, Flask/Pyramid, REST APIs, Algo-trading, Microservices",
            "Tools: Git",
            "Languages: Python, SQL",
            "Databases & Caching: Postgres, Mongodb, ElasticSearch, Redis",
            "AI & Data: Machine Learning, Statsmodels, SciPy",
            "Cloud & Devops: AWS, CI/CD",
        ],
        "experience": [
            {
                "title": "Capstone Project Lead - Trike Platform",
                "company": "Tel Aviv University",
                "dates": "September 2019 – 2023",
                "bullets": [
                    "Deployed numerous microservices using AWS (EC2, SQS, SES) and implemented CI/CD pipelines for seamless integration.",
                    "Architected a multi-machine search activity platform, encompassing mobile client, backend services, and cloud infrastructure using Django and postgres.",
                    "Implemented automated testing with pytest, including integration tests and scalable testing utilities.",
                    "Built interactive interfaces with React, Redux-Sagas and Angular.",
                ],
            }
        ],
        "projects": [],
        "education": [
            {
                "institution": "Tel Aviv University",
                "degree": "B.Sc in Computer Science",
                "dates": "2019 – 2023",
            }
        ],
        "certifications": [],
    }


def _frontend_resume_facts() -> dict:
    return {
        "experience_roles": [
            {
                "title": "Capstone Project Lead – Tribe Platform",
                "company": "Tel Hai University",
                "dates": "2024 – 2025",
                "bullets": [
                    "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                    "Built WebSocket real-time updates for activity state and user participation",
                    "Deployed backend infrastructure using AWS (EC2, RDS, S3) and implemented basic CI/CD workflows",
                ],
            },
            {
                "title": "Python Programming Tutor",
                "company": "Tel Hai University",
                "dates": "Jul 2022 – Jul 2023",
                "bullets": [
                    "Delivered weekly tutoring sessions for CS students, explaining algorithms and data structures",
                ],
            },
        ],
        "projects": [
            {
                "name": "Restaurant Menu Ordering App",
                "description": "Android ordering application with offline storage",
                "bullets": [
                    "Built React Native mobile UI for item selection, quantities and notes",
                    "Implemented offline storage with SQLite and synchronized orders to Firebase",
                ],
                "technologies": ["React Native", "SQLite", "Firebase"],
            },
            {
                "name": "Server Monitor System",
                "description": "Backend monitoring system",
                "bullets": [
                    "Developed REST API using FastAPI and PostgreSQL",
                    "Used ThreadPoolExecutor for concurrent server monitoring",
                ],
                "technologies": ["FastAPI", "PostgreSQL"],
            },
        ],
        "education": [
            {
                "institution": "Tel Hai University",
                "degree": "B.Sc. Computer Science",
                "dates": "Mar 2022 – Aug 2025",
            }
        ],
        "display_skills": [
            "Frontend: React, React Native, Angular, HTML, CSS",
            "Backend: FastAPI, Node.js, Laravel, REST APIs, WebSockets",
            "Databases: PostgreSQL, SQL, MongoDB, Firebase, SQLite",
            "Cloud & Tools: AWS (EC2, RDS, S3), Git, CI/CD, SQLAlchemy, Expo, Generative AI",
        ],
        "skills": {
            "languages": ["Python", "SQL"],
            "frameworks": ["React", "React Native", "Angular", "FastAPI", "Node.js"],
            "databases": ["PostgreSQL", "MongoDB", "SQLite", "Firebase"],
            "cloud": ["AWS", "CI/CD"],
            "other": ["HTML", "CSS", "Git", "WebSockets"],
        },
    }


class TestBylithFabricationsBlocked:
    def test_trike_and_tel_aviv_rejected(self):
        assert not role_title_supported(
            "Capstone Project Lead - Trike Platform", GAL_TEL_HAI_SOURCE
        )
        assert role_title_supported(
            "Capstone Project Lead – Tribe Platform", GAL_TEL_HAI_SOURCE
        )

    def test_invented_frontend_adjacent_skills_rejected(self):
        for skill in (
            "Redux-Sagas",
            "GraphQL",
            "Django",
            "Flask",
            "Pyramid",
            "ElasticSearch",
            "Redis",
            "Statsmodels",
            "SciPy",
            "Algo-trading",
            "SCSS",
            "SES",
            "OS",
        ):
            assert not skill_supported_by_source(skill, GAL_TEL_HAI_SOURCE), skill

    def test_redux_sagas_bullet_rejected_despite_react_overlap(self):
        ok, reason = statement_supported_by_evidence(
            "Built interactive interfaces with React, Redux-Sagas and Angular.",
            source_text=GAL_TEL_HAI_SOURCE,
        )
        assert ok is False
        assert "tech" in reason or "unsupported" in reason or "entities" in reason

    def test_claim_validator_strips_bylith_shell(self):
        result = validate_claims(
            original_resume_text=GAL_TEL_HAI_SOURCE,
            tailored_resume=_fabricated_bylith_frontend_resume(),
        )
        cleaned = result.cleaned_resume.to_dict()
        blob = json.dumps(cleaned, ensure_ascii=False).lower()
        for bad in (
            "trike",
            "tel aviv",
            "2019",
            "graphql",
            "django",
            "redux",
            "elasticsearch",
            "scipy",
            "algo-trading",
            "scss",
        ):
            assert bad not in blob, bad


class TestFrontendEmphasisAfterRestore:
    def test_frontend_category_stays_first_despite_aws_emphasize(self):
        order = category_order_for_role(
            "frontend",
            emphasize=["AWS (EC2", "S3)", "RDS", "CSS", "HTML", "React"],
        )
        assert order[0] == "Frontend"
        lines = normalize_skill_lines(
            [
                "Cloud & DevOps: AWS, CI/CD",
                "Frontend: React, Angular, HTML, CSS",
                "Backend: FastAPI, Node.js",
                "Languages: Python",
            ],
            emphasize=["AWS (EC2", "S3)", "React", "HTML", "CSS"],
            job_family="frontend",
        )
        assert lines
        assert lines[0].lower().startswith("frontend:")

    def test_summary_leads_with_ui_not_scalable_services(self):
        facts = _frontend_resume_facts()
        strategy = {
            "job_family": "frontend",
            "primary_role": "Frontend Developer",
            "honest_title": "Frontend Developer",
            "skills_to_emphasize": ["React", "Angular", "HTML", "CSS", "AWS (EC2", "S3)"],
            "must_highlight_in_summary": ["React", "HTML", "CSS"],
        }
        plan = build_summary_plan(
            strategy=strategy,
            resume_facts=facts,
            resume_text=GAL_TEL_HAI_SOURCE,
        )
        comps = " ".join(plan["top_supported_competencies"]).lower()
        assert "react" in comps or "html" in comps or "css" in comps
        assert "s3)" not in comps

        summary = build_professional_summary(
            strategy=strategy,
            resume_facts=facts,
            resume_text=GAL_TEL_HAI_SOURCE,
        )["summary"]
        low = summary.lower()
        assert "frontend" in low
        assert "user interface" in low or "client-side" in low or "react" in low
        assert "scalable services" not in low
        # Prefer UI project evidence over backend Capstone when available
        assert "react" in low or "mobile" in low or "html" in low or "css" in low

    def test_restore_keeps_restaurant_ui_project_and_real_identity(self):
        result = validate_claims(
            original_resume_text=GAL_TEL_HAI_SOURCE,
            tailored_resume=_fabricated_bylith_frontend_resume(),
        )
        cleaned = result.cleaned_resume.to_dict()
        facts = _frontend_resume_facts()
        restored = restore_missing_content_from_source(cleaned, resume_facts=facts)
        restored = ensure_minimum_content_from_source(restored, resume_facts=facts)
        blob = json.dumps(restored, ensure_ascii=False).lower()

        assert "tel hai" in blob
        assert "tribe" in blob
        assert "trike" not in blob
        assert "restaurant" in blob
        assert "react native" in blob or "react" in blob
        assert "2024" in blob
        assert "2019" not in blob
