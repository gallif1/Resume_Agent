"""Regression: underfilled half-page tailor output must be padded from source."""

from __future__ import annotations

from intelligent_tailoring.canonical_resume import (
    estimate_content_density,
    expand_thin_entries_from_source,
    ensure_minimum_content_from_source,
    restore_missing_content_from_source,
)


SOURCE_FACTS = {
    "experience_roles": [
        {
            "title": "Capstone Project Lead – Tribe Platform",
            "company": "Tel Hai University",
            "dates": "2024 – 2025",
            "bullets": [
                "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                "Implemented activity CRUD services, validation logic and relational data models",
                "Built WebSocket real-time updates for activity state and user participation",
                "Deployed backend infrastructure using AWS (EC2, RDS, S3) and implemented basic CI/CD workflows",
                "Implemented automated testing using pytest including integration tests",
            ],
        },
        {
            "title": "Python Programming Tutor",
            "company": "Tel Hai University",
            "dates": "Jul 2022 – Jul 2023",
            "bullets": [
                "Delivered weekly tutoring sessions for CS students on algorithms and data structures",
                "Assisted students with debugging Python programs and coursework",
            ],
        },
    ],
    "projects": [
        {
            "name": "Server Monitor System",
            "description": "Backend monitoring system for server health checks",
            "bullets": [
                "Developed REST API using FastAPI and PostgreSQL",
                "Implemented background worker performing parallel health checks",
                "Used ThreadPoolExecutor for concurrent server monitoring",
                "Designed database schema for server health tracking and request history",
            ],
        },
        {
            "name": "Restaurant Menu Ordering App",
            "description": "Android ordering app with offline storage",
            "bullets": [
                "Built React Native mobile UI for item selection and notes",
                "Implemented offline storage with SQLite and Firebase sync",
            ],
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
        "Cloud & Tools: AWS, Git, CI/CD, SQLAlchemy, pytest",
    ],
}


def _half_page_tailored() -> dict:
    """Mirrors the sparse Bylith Backend preview: one thin role + one thin project."""
    return {
        "professional_title": "Backend Developer",
        "professional_summary": (
            "Backend Developer skilled in building scalable services with Python, "
            "AWS, and PostgreSQL."
        ),
        "skills": [
            "Backend: FastAPI, REST APIs",
            "Languages: Python, SQL",
            "Cloud & DevOps: AWS, CI/CD",
        ],
        "experience": [
            {
                "title": "Capstone Project Lead – Tribe Platform",
                "company": "Tel Hai University",
                "dates": "2024 – 2025",
                "bullets": [
                    "Led development of a real-time platform including mobile client and backend.",
                    "Managing CI/CD pipeline.",
                ],
            }
        ],
        "projects": [
            {
                "name": "Server Monitor System",
                "description": "",
                "bullets": [
                    "Designed database schema for server health tracking and analysis history.",
                ],
            }
        ],
        "education": [
            {
                "institution": "Tel Hai University",
                "degree": "B.Sc. Computer Science",
            }
        ],
    }


def test_half_page_resume_is_flagged_underfilled():
    density = estimate_content_density(_half_page_tailored())
    assert density["underfilled"] is True
    assert density["inventory"]["experience_bullets"] + density["inventory"][
        "project_bullets"
    ] < 7


def test_expand_and_restore_fill_half_page_from_source():
    thin = _half_page_tailored()
    filled = restore_missing_content_from_source(
        thin,
        resume_facts=SOURCE_FACTS,
        max_roles=0,
        max_projects=0,
        min_bullets_per_role=3,
        min_bullets_per_project=3,
    )
    filled = expand_thin_entries_from_source(
        filled,
        resume_facts=SOURCE_FACTS,
        target_bullets_per_role=4,
        target_bullets_per_project=3,
    )
    filled = ensure_minimum_content_from_source(
        filled,
        resume_facts=SOURCE_FACTS,
        min_bullets_per_role=3,
        min_bullets_per_project=2,
    )

    density = estimate_content_density(filled)
    total_bullets = (
        density["inventory"]["experience_bullets"]
        + density["inventory"]["project_bullets"]
    )
    assert total_bullets >= 8
    assert density["inventory"]["experience_entries"] >= 2
    assert density["inventory"]["projects"] >= 2

    titles = " ".join(
        str(e.get("title") or "") for e in (filled.get("experience") or [])
    ).lower()
    assert "tutor" in titles
    names = " ".join(
        str(p.get("name") or "") for p in (filled.get("projects") or [])
    ).lower()
    assert "restaurant" in names or "server monitor" in names

    # Capstone should carry more than the two thin tailored bullets.
    capstone = next(
        e
        for e in filled["experience"]
        if "capstone" in str(e.get("title") or "").lower()
    )
    assert len([b for b in capstone.get("bullets") or [] if str(b).strip()]) >= 3
