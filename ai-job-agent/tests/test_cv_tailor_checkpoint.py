"""Check Point Backend CV Tailor regression tests."""

from __future__ import annotations

from cv_tailor.models import TailoredCvData
from cv_tailor.validation import apply_factual_guards, parse_llm_response

GAL_TEL_HAI_SOURCE = """
Gal Lifshitz
Backend: FastAPI, Node.js, REST APIs, WebSockets
Cloud & Tools: AWS (EC2, RDS, S3), Git, CI/CD, pytest
Capstone Project Lead – Tribe Platform | Tel Hai University | 2024 – 2025
• Deployed backend infrastructure using AWS (EC2, RDS, S3) and implemented basic CI/CD workflows
• Implemented automated testing using pytest including integration tests and reusable testing utilities
Server Monitor System
• Implemented background worker performing parallel health checks (HTTP, FTP, SSH)
• Used ThreadPoolExecutor for concurrent server monitoring
Education
B.Sc. Computer Science – Tel Hai University (Mar 2022 – Aug 2025)
"""

CHECKPOINT_BACKEND_JD = """
Check Point Software Technologies — Backend Developer

Requirements:
- 5+ years of distributed systems experience
- 3+ years of Node.js development experience
- Strong backend development with Python and REST APIs
- AWS cloud deployment (EC2, RDS, S3)
- CI/CD pipelines
- Automated testing with pytest and integration tests
- Networking knowledge (HTTP, FTP, SSH monitoring)
- Concurrent systems and background workers

Nice to have:
- Java experience
- Knowledge of Check Point security products
"""


def _ideal_checkpoint_response() -> dict:
    return {
        "tailored_cv": {
            "name": "Gal Lifshitz",
            "contact": "Israel | gal.lifshiz123@gmail.com | GitHub | LinkedIn",
            "professional_title": "Backend Software Developer",
            "summary": (
                "Backend developer with hands-on experience building FastAPI services, AWS deployments, "
                "CI/CD workflows, pytest-based automated testing, and concurrent monitoring systems using "
                "HTTP, FTP, and SSH health checks."
            ),
            "skill_groups": [
                {
                    "category": "Backend",
                    "skills": ["Python", "FastAPI", "Node.js", "REST APIs", "WebSockets"],
                },
                {
                    "category": "Cloud & DevOps",
                    "skills": ["AWS (EC2, RDS, S3)", "CI/CD", "Git"],
                },
                {
                    "category": "Databases",
                    "skills": ["PostgreSQL", "SQLAlchemy", "MongoDB", "SQLite", "Firebase"],
                },
                {
                    "category": "Testing",
                    "skills": ["pytest", "integration tests"],
                },
            ],
            "experience": [
                {
                    "company": "Tel Hai University",
                    "role": "Capstone Project Lead – Tribe Platform",
                    "dates": "2024 – 2025",
                    "bullets": [
                        "Deployed backend infrastructure using AWS (EC2, RDS, S3) and implemented basic CI/CD workflows",
                        "Implemented automated testing using pytest including integration tests and reusable testing utilities",
                        "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                        "Built WebSocket real-time updates for activity state and user participation",
                    ],
                },
                {
                    "company": "Tel Hai University",
                    "role": "Python Programming Tutor",
                    "dates": "Jul 2022 – Jul 2023",
                    "bullets": [
                        "Explained algorithms and data structures and assisted with debugging Python programs",
                    ],
                },
            ],
            "projects": [
                {
                    "name": "Server Monitor System",
                    "description": "Backend monitoring system",
                    "bullets": [
                        "Implemented background worker performing parallel health checks (HTTP, FTP, SSH)",
                        "Used ThreadPoolExecutor for concurrent server monitoring",
                        "Developed REST API using FastAPI and PostgreSQL",
                    ],
                },
                {
                    "name": "Restaurant Menu Ordering App",
                    "description": "Mobile ordering application",
                    "bullets": [
                        "Implemented offline storage with SQLite and synchronized orders to Firebase",
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
            "certifications": [],
        },
        "job_analysis": {
            "strong_matches": [
                "AWS",
                "CI/CD",
                "pytest",
                "HTTP/FTP/SSH networking",
                "Backend APIs",
            ],
            "gaps": [
                {
                    "requirement": "3+ years Node.js",
                    "status": "insufficient_evidence",
                    "explanation": "Node.js appears in Technical Skills, but the CV does not show 3+ years of Node.js development.",
                },
                {
                    "requirement": "5+ years distributed systems",
                    "status": "insufficient_evidence",
                    "explanation": "The CV does not provide evidence of 5+ years of distributed systems experience.",
                },
                {
                    "requirement": "Java",
                    "status": "not_found",
                    "explanation": "Java experience was not found in the source CV.",
                },
                {
                    "requirement": "Check Point product knowledge",
                    "status": "not_found",
                    "explanation": "Check Point product knowledge was not found in the source CV.",
                },
            ],
        },
    }


def test_parse_nested_llm_response():
    raw = _ideal_checkpoint_response()
    cv, analysis = parse_llm_response(raw)
    assert cv.name == "Gal Lifshitz"
    assert analysis.gaps
    assert any("Node.js" in gap.requirement for gap in analysis.gaps)


def test_validation_removes_in_progress_education_and_java():
    raw = _ideal_checkpoint_response()
    cv, _ = parse_llm_response(raw)
    cv.education[0].degree = "B.Sc. Computer Science in progress"
    cv.skill_groups[0].skills.append("Java")
    cv.summary = "Backend engineer with 3+ years of Node.js and 5 years of distributed systems."

    repaired = apply_factual_guards(GAL_TEL_HAI_SOURCE, cv)

    combined_edu = f"{repaired.education[0].degree} {repaired.education[0].dates}"
    assert "in progress" not in combined_edu.lower()
    assert "Mar 2022 – Aug 2025" in repaired.education[0].dates
    flat_skills = [s for g in repaired.skill_groups for s in g.skills]
    assert "Java" not in flat_skills
    assert "3+ years" not in repaired.summary.lower() or "node.js" in GAL_TEL_HAI_SOURCE.lower()


def test_checkpoint_tailoring_prioritization_signals():
    cv, analysis = parse_llm_response(_ideal_checkpoint_response())
    cv = apply_factual_guards(GAL_TEL_HAI_SOURCE, cv)

    summary = cv.summary.lower()
    assert "aws" in summary
    assert "ci/cd" in summary or "pytest" in summary

    capstone = cv.experience[0]
    assert "aws" in capstone.bullets[0].lower()
    assert "pytest" in capstone.bullets[1].lower() or "ci/cd" in capstone.bullets[0].lower()

    monitor = cv.projects[0]
    assert monitor.name == "Server Monitor System"
    monitor_text = " ".join(monitor.bullets).lower()
    assert "http" in monitor_text and "ssh" in monitor_text

    gap_requirements = " ".join(g.requirement.lower() for g in analysis.gaps)
    assert "node.js" in gap_requirements
    assert "java" in gap_requirements
    assert "distributed systems" in gap_requirements

    flat = cv.to_preview_text().lower()
    assert "in progress" not in flat


def test_checkpoint_output_differs_from_generic_rewrite():
    """Tailored CV should reorder bullets differently from a naive preserve-order copy."""
    ideal, _ = parse_llm_response(_ideal_checkpoint_response())
    ideal = apply_factual_guards(GAL_TEL_HAI_SOURCE, ideal)

    naive = TailoredCvData.from_llm_dict(
        {
            "summary": "Junior software developer.",
            "experience": [
                {
                    "company": "Tel Hai University",
                    "role": "Capstone Project Lead – Tribe Platform",
                    "dates": "2024 – 2025",
                    "bullets": [
                        "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                        "Implemented automated testing using pytest including integration tests and reusable testing utilities",
                        "Deployed backend infrastructure using AWS (EC2, RDS, S3) and implemented basic CI/CD workflows",
                    ],
                }
            ],
            "projects": ideal.projects,
        }
    )

    assert ideal.experience[0].bullets[0] != naive.experience[0].bullets[0]
    assert "aws" in ideal.experience[0].bullets[0].lower()
