"""Regression: Foundational backend tailor must not invent employer/dates/skills.

Reproduces the Gal Lifshitz (Tel Hai) → fabricated Tel Aviv University /
October 2021 – Present / team-of-5 / Kubernetes failure mode observed in
production preview.
"""

from __future__ import annotations

import json

from intelligent_tailoring.canonical_resume import (
    ensure_minimum_content_from_source,
    restore_missing_content_from_source,
)
from intelligent_tailoring.claim_validator import (
    dates_supported,
    organization_supported,
    project_name_supported,
    role_title_supported,
    statement_supported_by_evidence,
    validate_claims,
)
from intelligent_tailoring.quality_gates import evaluate_quality_gates


GAL_TEL_HAI_SOURCE = """
Gal Lifshitz
Israel | 052-352-7293 | gal.lifshiz123@gmail.com | GitHub | LinkedIn
Summary
Junior Software Developer with hands-on experience building real-time systems, backend APIs and cloud-based
applications. Experienced with Python, FastAPI and modern cloud tools. Passionate about designing scalable systems
and learning new technologies quickly.
Technical Skills
Frontend: React, React Native, Angular, HTML, CSS
Backend: FastAPI, Node.js, Laravel, REST APIs, WebSockets
Databases: PostgreSQL, SQL, MongoDB, Firebase, SQLite
Cloud & Tools: AWS (EC2, RDS, S3), Git, CI/CD, SQLAlchemy, Expo, Generative AI
Experience
Capstone Project Lead – Tribe Platform | Tel Hai University | 2024 – 2025
Led development of a real-time social activity platform including mobile client, backend services and cloud
infrastructure.
• Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL
• Implemented activity CRUD services, validation logic and relational data models
• Built WebSocket real-time updates for activity state and user participation
• Integrated Generative AI for automatic event image generation
• Deployed backend infrastructure using AWS (EC2, RDS, S3) and implemented basic CI/CD workflows
• Implemented automated testing using pytest including integration tests and reusable testing utilities
Python Programming Tutor | Tel Hai University | Jul 2022 – Jul 2023
Delivered weekly tutoring sessions for CS students, explaining algorithms and data structures and assisting with
debugging Python programs.
Projects
Server Monitor System
Built a backend monitoring system that continuously checks server health using multiple protocols.
• Developed REST API using FastAPI and PostgreSQL
• Implemented background worker performing parallel health checks (HTTP, FTP, SSH)
• Used ThreadPoolExecutor for concurrent server monitoring
• Designed database schema for server health tracking and request history
Restaurant Menu Ordering App
Android application for local ordering including item selection, quantities and notes. Implemented offline storage with
SQLite and synchronized orders to Firebase.
Education
B.Sc. Computer Science – Tel Hai University (Mar 2022 – Aug 2025)
Specialization: Artificial Intelligence, Machine Learning, Large Language Models (LLMs), Generative AI.
"""


def _fabricated_foundational_resume() -> dict:
    return {
        "professional_title": "Backend Engineer",
        "professional_summary": (
            "Backend Engineer with hands-on experience building scalable services and "
            "applications using FastAPI, Python, Docker and MongoDB. Strong technical "
            "background within an Engineering Team at Tel Aviv University starting from "
            "October 2021. Experience in database backend development using Node.js, "
            "C++, and SQL, as well as building multi-threaded applications in C++."
        ),
        "skills": [
            "Languages: Python, SQL, C++, Java",
            "AI/ML: PyTorch, scikit-learn",
            "Tools: Git",
            "Backend & Frameworks: FastAPI, Custom Node.js, REST APIs, Microservices, Kubernetes",
            "Databases & Caching: MongoDB, Redis, PostgreSQL, MySQL",
            "Frontend: React, React Native, Angular, CSS, HTML",
            "Cloud & DevOps: AWS, Azure",
        ],
        "experience": [
            {
                "title": "Project Lead - Title Platform",
                "company": "Tel Aviv University",
                "dates": "October 2021 – Present",
                "bullets": [
                    "Designed weekly summary solutions for students, implementing algorithms and data structures.",
                    "Managed a team of 5 members.",
                    "Spearheaded the creation of a real-time social activity platform, integrating mobile clients, backend services, and cloud networks using Node.js and Python.",
                ],
            }
        ],
        "projects": [
            {
                "name": "REST API Development",
                "description": (
                    "Developed a REST API with FastAPI and MongoDB, including data "
                    "handling and server-level encryption."
                ),
                "bullets": [
                    "Designed a database system in local server environments using PostgreSQL, supporting efficient data management using Node.js and Python.",
                ],
            }
        ],
        "education": [
            {
                "institution": "Tel Aviv University",
                "degree": "B.Sc. in Computer Science",
                "dates": "2021 – Present",
            }
        ],
        "certifications": [],
    }


def _gal_resume_facts() -> dict:
    """Structured source facts for the Tel Hai CV (post-extraction shape)."""
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
                    "Assisted with debugging Python programs",
                ],
            },
        ],
        "projects": [
            {
                "name": "Server Monitor System",
                "description": "Backend monitoring system with parallel health checks",
                "bullets": [
                    "Developed REST API using FastAPI and PostgreSQL",
                    "Used ThreadPoolExecutor for concurrent server monitoring",
                ],
                "technologies": ["FastAPI", "PostgreSQL", "ThreadPoolExecutor"],
            },
            {
                "name": "Restaurant Menu Ordering App",
                "description": "Android ordering app with offline SQLite and Firebase sync",
                "bullets": [
                    "Implemented offline storage with SQLite",
                    "Synchronized orders to Firebase",
                ],
                "technologies": ["SQLite", "Firebase"],
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
        "skills": [
            "Python",
            "FastAPI",
            "Node.js",
            "Laravel",
            "WebSockets",
            "PostgreSQL",
            "MongoDB",
            "Firebase",
            "SQLite",
            "AWS",
            "React",
            "SQLAlchemy",
            "pytest",
            "Generative AI",
        ],
    }


class TestIdentityLockHelpers:
    def test_tel_aviv_not_supported_when_source_is_tel_hai(self):
        assert organization_supported("Tel Hai University", GAL_TEL_HAI_SOURCE)
        assert not organization_supported("Tel Aviv University", GAL_TEL_HAI_SOURCE)

    def test_invented_dates_rejected(self):
        assert dates_supported("2024 – 2025", GAL_TEL_HAI_SOURCE)
        assert dates_supported("Jul 2022 – Jul 2023", GAL_TEL_HAI_SOURCE)
        assert not dates_supported("October 2021 – Present", GAL_TEL_HAI_SOURCE)
        assert not dates_supported("2021 – Present", GAL_TEL_HAI_SOURCE)

    def test_near_miss_title_rejected(self):
        assert role_title_supported(
            "Capstone Project Lead – Tribe Platform", GAL_TEL_HAI_SOURCE
        )
        assert not role_title_supported(
            "Project Lead - Title Platform", GAL_TEL_HAI_SOURCE
        )

    def test_generic_invented_project_name_rejected(self):
        assert project_name_supported("Server Monitor System", GAL_TEL_HAI_SOURCE)
        assert project_name_supported(
            "Restaurant Menu Ordering App", GAL_TEL_HAI_SOURCE
        )
        assert not project_name_supported("REST API Development", GAL_TEL_HAI_SOURCE)

    def test_team_headcount_and_summary_org_rejected(self):
        ok, reason = statement_supported_by_evidence(
            "Managed a team of 5 members.",
            source_text=GAL_TEL_HAI_SOURCE,
        )
        assert ok is False
        assert "team" in reason or "unsupported" in reason

        ok, reason = statement_supported_by_evidence(
            (
                "Backend Engineer with FastAPI at Tel Aviv University starting "
                "from October 2021 using Docker and C++."
            ),
            source_text=GAL_TEL_HAI_SOURCE,
            min_token_overlap=0.35,
        )
        assert ok is False


class TestFoundationalFabricationSanitized:
    def test_claim_validator_strips_foundational_hallucinations(self):
        result = validate_claims(
            original_resume_text=GAL_TEL_HAI_SOURCE,
            tailored_resume=_fabricated_foundational_resume(),
        )
        cleaned = result.cleaned_resume.to_dict()
        blob = json.dumps(cleaned, ensure_ascii=False).lower()

        for bad in (
            "tel aviv",
            "october 2021",
            "team of 5",
            "kubernetes",
            "pytorch",
            "scikit-learn",
            "azure",
            "redis",
            "mysql",
            "c++",
            "docker",
        ):
            assert bad not in blob, f"fabricated token survived: {bad}"

        # Real source institution must remain available as a supported identity,
        # and invented education/experience shells must not survive.
        assert cleaned.get("education") == []
        assert cleaned.get("experience") == []
        assert not (cleaned.get("professional_summary") or "").strip()

        # Evidenced skills may remain.
        skills_blob = " ".join(str(s) for s in cleaned.get("skills") or []).lower()
        assert "python" in skills_blob
        assert "fastapi" in skills_blob
        assert "kubernetes" not in skills_blob

    def test_quality_gates_flag_unsanitized_identity_claims(self):
        # Backup gate if a future path bypasses claim validation.
        gates = evaluate_quality_gates(
            tailored_resume=_fabricated_foundational_resume(),
            original_resume_text=GAL_TEL_HAI_SOURCE,
            facts=[],
            change_log=[],
            require_summary=False,
        )
        failures = " ".join(gates.get("failures") or []).lower()
        assert "tel aviv" in failures or "unsupported_organization" in failures
        assert "invalid_dates" in failures or "2021" in failures

    def test_claim_plus_restore_brings_back_omitted_source_content(self):
        """Fabricated substitute entries must not permanently hide source facts."""
        result = validate_claims(
            original_resume_text=GAL_TEL_HAI_SOURCE,
            tailored_resume=_fabricated_foundational_resume(),
        )
        cleaned = result.cleaned_resume.to_dict()
        # Invented shells removed — otherwise restore cannot re-home source entries.
        assert cleaned.get("experience") == []
        assert cleaned.get("projects") == []
        assert cleaned.get("education") == []

        facts = _gal_resume_facts()
        restored = restore_missing_content_from_source(cleaned, resume_facts=facts)
        restored = ensure_minimum_content_from_source(restored, resume_facts=facts)
        blob = json.dumps(restored, ensure_ascii=False).lower()

        for must in (
            "tel hai",
            "capstone",
            "python programming tutor",
            "server monitor",
            "restaurant",
            "fastapi",
            "websocket",
            "sqlalchemy",
            "firebase",
            "sqlite",
            "laravel",
            "2024",
            "2022",
        ):
            assert must in blob, f"source content still omitted: {must}"

        for bad in ("tel aviv", "october 2021", "kubernetes", "team of 5", "rest api development"):
            assert bad not in blob, f"fabricated substitute survived restore: {bad}"

        titles = {
            str(e.get("title") or "").lower() for e in (restored.get("experience") or [])
        }
        assert any("capstone" in t for t in titles)
        assert any("tutor" in t for t in titles)
        names = {
            str(p.get("name") or "").lower() for p in (restored.get("projects") or [])
        }
        assert any("server monitor" in n for n in names)
        assert any("restaurant" in n for n in names)
