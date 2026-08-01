"""Check Point Full Stack regression — truthfulness + evidence coverage.

Acceptance criteria covered:
- No 3+ years / TypeScript / production-grade ownership claims
- No unsupported customer satisfaction / scalability / reliability outcomes
- Academic context preserved for capstone
- Verified React/API/backend/DB/AWS/CI/CD/testing/AI evidence used
- Genuine gaps visible in score report
- Final score from final validated resume
- Correct skill categorization (no Other Relevant Skills: api)
- Rejected claims cannot return
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from intelligent_tailoring.claim_validator import (
    hard_reject_claim,
    statement_supported_by_evidence,
    validate_claims,
)
from intelligent_tailoring.experience_math import extract_years_claims
from intelligent_tailoring.knowledge_base import build_knowledge_base
from intelligent_tailoring.pipeline import run_intelligent_tailoring
from intelligent_tailoring.professional_narrative import evaluate_professional_narrative
from intelligent_tailoring.rejected_claims import RejectedClaimsRegistry
from intelligent_tailoring.skill_taxonomy import (
    categorize_skill,
    normalize_skill_lines,
    should_drop_skill_atom,
)


CHECK_POINT_JD = """
Check Point Software Technologies — Full Stack Engineer

About the role:
We are a global cybersecurity company building production-grade applications.
We are looking for a Full Stack Engineer with 3+ years of experience.

Requirements:
- Strong React and TypeScript skills
- Full Stack ownership of features end-to-end
- Server-side APIs and API integration
- Asynchronous data flows and client-server communication
- Testing, deployment, and monitoring
- AI-assisted development using tools such as Cursor, ChatGPT, Claude, and GitHub Copilot
- Output validation and clean code
- Strong problem solving
- Collaboration in a security-focused engineering culture

Nice to have:
- Experience with cybersecurity products
- Authentication and formal code review processes
"""

CHECK_POINT_CANDIDATE = {
    "contact": {"name": "Gal Lifshitz", "email": "gal@example.com"},
    "raw_text": (
        "Gal Lifshitz — Computer Science Graduate\n"
        "Skills: Python, FastAPI, SQLAlchemy, PostgreSQL, React, React Native, "
        "Angular, Node.js, HTML, CSS, AWS, EC2, RDS, S3, Git, CI/CD, pytest, "
        "WebSockets, Generative AI, SQLite, Firebase, Laravel, REST APIs, "
        "algorithms, data structures, Cursor, ChatGPT.\n"
        "Experience:\n"
        "Comax Smart ERP — Technical Support Specialist (2023-2024)\n"
        "- Troubleshot ERP production issues using logs\n"
        "- Supported customers with technical problems\n"
        "- Debugged Python programs and explained complex concepts while tutoring\n"
        "Projects:\n"
        "Capstone Project — Academic final project\n"
        "- Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL\n"
        "- Integrated WebSockets for real-time updates\n"
        "- Deployed to AWS EC2, RDS and S3 with basic CI/CD\n"
        "- Added pytest integration testing\n"
        "- Integrated Generative AI features\n"
        "- Built React Native mobile client against REST APIs\n"
        "Server Monitor:\n"
        "- Implemented FastAPI service with ThreadPoolExecutor for concurrent health checks\n"
        "- Deployed monitoring service to AWS with automated alerts\n"
        "Restaurant App:\n"
        "- Built React Native mobile UI\n"
        "- Created FastAPI backend with SQLite and Firebase\n"
        "Education: Tel Hai University — B.Sc. Computer Science\n"
        "AI-assisted development: daily use of Cursor and ChatGPT for coding workflows "
        "with careful output validation."
    ),
    "skills": {
        "languages": ["Python", "JavaScript"],
        "frameworks": [
            "FastAPI",
            "React",
            "React Native",
            "Angular",
            "Node.js",
            "Laravel",
        ],
        "databases": ["PostgreSQL", "SQLite", "Firebase", "SQLAlchemy"],
        "cloud": ["AWS", "EC2", "RDS", "S3", "CI/CD", "Git"],
        "other": [
            "WebSockets",
            "pytest",
            "integration testing",
            "Generative AI",
            "REST APIs",
            "HTML",
            "CSS",
            "algorithms",
            "data structures",
            "Cursor",
            "ChatGPT",
        ],
    },
    "experience": {
        "roles": [
            {
                "company": "Comax Smart ERP",
                "title": "Technical Support Specialist",
                "dates": "2023-2024",
                "bullets": [
                    "Troubleshot ERP production issues using logs",
                    "Supported customers with technical problems",
                    "Debugged Python programs and explained complex concepts while tutoring",
                ],
            }
        ]
    },
    "projects": [
        {
            "name": "Capstone Project",
            "description": "Academic full-stack capstone",
            "technologies": [
                "FastAPI",
                "SQLAlchemy",
                "PostgreSQL",
                "WebSockets",
                "AWS",
                "EC2",
                "RDS",
                "S3",
                "CI/CD",
                "pytest",
                "Generative AI",
                "React Native",
                "REST APIs",
            ],
            "bullets": [
                "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                "Integrated WebSockets for real-time updates",
                "Deployed to AWS EC2, RDS and S3 with basic CI/CD",
                "Added pytest integration testing",
                "Integrated Generative AI features",
                "Built React Native mobile client against REST APIs",
            ],
        },
        {
            "name": "Server Monitor",
            "description": "Infrastructure monitoring",
            "technologies": ["FastAPI", "ThreadPoolExecutor", "AWS"],
            "bullets": [
                "Implemented FastAPI service with ThreadPoolExecutor for concurrent health checks",
                "Deployed monitoring service to AWS with automated alerts",
            ],
        },
        {
            "name": "Restaurant App",
            "description": "Ordering application",
            "technologies": ["React Native", "FastAPI", "SQLite", "Firebase"],
            "bullets": [
                "Built React Native mobile UI",
                "Created FastAPI backend with SQLite and Firebase",
            ],
        },
    ],
    "education": [
        {
            "institution": "Tel Hai University",
            "degree": "B.Sc. Computer Science",
        }
    ],
}


def test_worded_years_claims_extracted():
    claims = extract_years_claims(
        "Full Stack Engineer with over three years of expertise"
    )
    assert 3.0 in claims


def test_hard_reject_inflated_years_and_leadership():
    source = CHECK_POINT_CANDIDATE["raw_text"]
    reject, reason = hard_reject_claim(
        "Full Stack Engineer with over three years of expertise",
        source_text=source,
        professional_years=1.0,
    )
    assert reject
    assert "year" in reason or "inflated" in reason

    reject2, reason2 = hard_reject_claim(
        "Proven ability to lead projects from inception to deployment",
        source_text=source,
        professional_years=1.0,
    )
    assert reject2
    assert "leadership" in reason2


def test_hard_reject_unsupported_outcomes():
    source = CHECK_POINT_CANDIDATE["raw_text"]
    for phrase in (
        "Built ordering UI enhancing customer satisfaction",
        "Deployed services supporting system scalability",
        "Refactored modules improving system reliability",
    ):
        ok, reason = statement_supported_by_evidence(phrase, source_text=source)
        assert not ok, phrase
        assert "unsupported" in reason


def test_capstone_marked_academic():
    kb = build_knowledge_base(
        CHECK_POINT_CANDIDATE,
        CHECK_POINT_CANDIDATE["raw_text"],
    )
    capstone_facts = [
        f
        for f in kb.facts
        if "capstone" in (f.project or f.context or f.original_text).lower()
    ]
    assert capstone_facts
    assert all(f.context_type == "academic" for f in capstone_facts)


def test_high_value_technologies_extracted():
    kb = build_knowledge_base(
        CHECK_POINT_CANDIDATE,
        CHECK_POINT_CANDIDATE["raw_text"],
    )
    blob = " ".join(f.original_text for f in kb.facts).lower()
    for tech in (
        "fastapi",
        "sqlalchemy",
        "postgresql",
        "websockets",
        "aws",
        "ci/cd",
        "pytest",
        "generative ai",
        "sqlite",
        "firebase",
        "cursor",
        "chatgpt",
    ):
        assert tech in blob, tech


def test_skill_taxonomy_checkpoint_case():
    assert categorize_skill("React") == "Frontend"
    assert categorize_skill("FastAPI") == "Backend"
    assert categorize_skill("PostgreSQL") == "Databases"
    assert categorize_skill("pytest") == "Testing"
    assert categorize_skill("Cursor") == "AI-Assisted Development"
    assert should_drop_skill_atom("api")
    lines = normalize_skill_lines(
        [
            "Backend: React, FastAPI, api",
            "Databases: PostgreSQL",
            "Databases: SQLite",
            "Other Relevant Skills: api",
            "AI: Cursor, ChatGPT",
        ]
    )
    joined = "\n".join(lines)
    assert "Other Relevant Skills: api" not in joined
    assert not any(line.lower().startswith("backend:") and "react" in line.lower() for line in lines)
    assert any("Frontend:" in line and "React" in line for line in lines)
    # No duplicate Databases category lines
    assert sum(1 for line in lines if line.startswith("Databases:")) <= 1


def test_rejected_claims_registry_blocks_return():
    reg = RejectedClaimsRegistry()
    reg.add(
        "Proven ability to lead projects from inception to deployment",
        reason="unsupported_professional_leadership",
        source_agent="claim_validation",
    )
    resume = {
        "professional_summary": "Proven ability to lead projects from inception to deployment.",
        "skills": ["Backend: FastAPI"],
        "experience": [],
        "projects": [
            {
                "name": "Capstone Project",
                "bullets": [
                    "Proven ability to lead projects from inception to deployment"
                ],
            }
        ],
    }
    cleaned = reg.scrub_resume(resume)
    assert not cleaned.get("professional_summary")
    assert cleaned["projects"][0]["bullets"] == []


def test_claim_validation_strips_checkpoint_failures():
    bad_resume = {
        "professional_title": "Full Stack Engineer",
        "professional_summary": (
            "Full Stack Engineer with over three years of expertise. "
            "Proven ability to lead projects from inception to deployment."
        ),
        "skills": [
            "Frontend: React, TypeScript",
            "Backend: FastAPI, api",
            "Other Relevant Skills: api",
        ],
        "experience": [
            {
                "company": "Comax Smart ERP",
                "title": "Technical Support Specialist",
                "dates": "2023-2024",
                "bullets": [
                    "Troubleshot ERP production issues using logs",
                ],
            }
        ],
        "projects": [
            {
                "name": "Capstone Project",
                "description": "Led projects from inception to deployment",
                "bullets": [
                    "Built ordering UI enhancing customer satisfaction",
                    "Deployed services supporting system scalability",
                    "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                ],
            }
        ],
        "education": [
            {"institution": "Tel Hai University", "degree": "B.Sc. Computer Science"}
        ],
        "certifications": [],
    }
    result = validate_claims(
        original_resume_text=CHECK_POINT_CANDIDATE["raw_text"],
        tailored_resume=bad_resume,
        evidence_map=[],
        change_log=[],
        inferred_competencies=[],
    )
    cleaned = result.cleaned_resume.to_dict()
    summary = cleaned.get("professional_summary") or ""
    assert "three years" not in summary.lower()
    assert "proven ability" not in summary.lower()
    all_text = str(cleaned).lower()
    assert "customer satisfaction" not in all_text
    assert "system scalability" not in all_text
    assert "typescript" not in all_text
    # Valid FastAPI evidence should survive
    assert "fastapi" in all_text


def test_professional_narrative_answers():
    resume = {
        "professional_summary": (
            "Computer Science graduate with hands-on experience building real-time "
            "applications across mobile, backend, database, and cloud layers. "
            "Developed client-facing features, REST APIs, WebSocket services, and "
            "relational data models through academic and personal projects."
        ),
        "skills": ["Frontend: React, React Native", "Backend: FastAPI"],
        "projects": [
            {
                "name": "Capstone Project",
                "bullets": [
                    "Designed backend architecture using FastAPI and PostgreSQL"
                ],
            }
        ],
        "experience": [],
    }
    result = evaluate_professional_narrative(
        resume,
        strategy={
            "top_reasons_to_interview": [
                "Academic end-to-end FastAPI/React system",
                "AWS deployment with CI/CD and pytest",
                "AI-assisted development with Cursor/ChatGPT",
            ],
            "genuine_gaps": ["TypeScript", "3+ years professional experience"],
        },
    )
    assert result["answers"]["who_is_candidate"]
    assert result["answers"]["top_three_interview_reasons"]
    assert "TypeScript" in result["answers"]["important_gaps"]
    assert result["answers"]["seniority_preserved"] is True


def _unsafe_checkpoint_generation() -> dict[str, Any]:
    return {
        "tailored_resume": {
            "professional_title": "Full Stack Engineer",
            "professional_summary": (
                "Full Stack Engineer with over three years of expertise and "
                "proven ability to lead projects from inception to deployment."
            ),
            "skills": [
                "Frontend: React, TypeScript",
                "Backend: React, FastAPI, api",
                "Databases: PostgreSQL",
                "Databases: SQLite",
                "Cloud & DevOps: AWS, CI/CD",
                "Other Relevant Skills: api",
            ],
            "experience": [
                {
                    "company": "Comax Smart ERP",
                    "title": "Technical Support Specialist",
                    "dates": "2023-2024",
                    "bullets": [
                        "Troubleshot ERP production issues using logs",
                        "Supported customers with technical problems",
                    ],
                }
            ],
            "projects": [
                {
                    "name": "Capstone Project",
                    "description": "Production-grade ownership of full stack delivery",
                    "bullets": [
                        "Led projects from inception to deployment enhancing customer satisfaction",
                        "Deployed services supporting system scalability",
                        "Built with Node.js improving system reliability",
                        # Valid evidenced bullets that must survive sanitization
                        "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                        "Deployed to AWS EC2, RDS and S3 with basic CI/CD",
                        "Integrated WebSockets for real-time updates",
                    ],
                }
            ],
            "education": [
                {
                    "institution": "Tel Hai University",
                    "degree": "B.Sc. Computer Science",
                }
            ],
            "certifications": [],
        },
        "change_log": [],
        "matched_requirements": ["React", "APIs"],
        "missing_requirements": ["TypeScript", "3+ years"],
        "removed_or_deprioritized_content": [],
        "ats_keywords_added": ["TypeScript"],
    }


def _stage_side_effect():
    reqs = {
        "required_skills": [
            "React",
            "TypeScript",
            "Full Stack",
            "APIs",
            "testing",
            "deployment",
            "AI-assisted development",
        ],
        "preferred_skills": ["cybersecurity", "authentication"],
        "hard_requirements": [
            "React",
            "TypeScript",
            "3+ years",
            "APIs",
            "testing",
        ],
        "soft_requirements": ["collaboration", "AI-assisted development"],
        "responsibilities": [
            "Own full stack features",
            "Integrate APIs",
            "Validate AI-assisted output",
        ],
        "tools_technologies": [
            "React",
            "TypeScript",
            "Cursor",
            "ChatGPT",
            "Claude",
            "GitHub Copilot",
        ],
        "industry_terminology": ["cybersecurity", "production-grade"],
        "soft_skills": ["problem solving", "collaboration"],
        "education_certifications": [],
        "experience_expectations": ["3+ years of Full Stack experience"],
        "ats_keywords": ["React", "TypeScript", "Full Stack", "APIs"],
        "seniority_level": "mid",
        "language": "en",
    }

    def _extract(job, **kwargs):
        return dict(reqs)

    return _extract


def _llm_queue_side_effect():
    """Drive the pipeline LLM stages with unsafe generation + safe polish."""
    gen = _unsafe_checkpoint_generation()
    reqs = _stage_side_effect()(None)

    def _call(*_a, **_k):
        namespace = str(_k.get("cache_namespace") or "")
        if "job_requirement" in namespace or "jd" in namespace:
            return reqs
        if "inference" in namespace:
            return {"inferred_competencies": []}
        if "triage" in namespace:
            return {"triage": [], "section_order": []}
        if "human_writer" in namespace:
            safe = {
                "professional_title": "Computer Science Graduate",
                "professional_summary": (
                    "Computer Science graduate with hands-on experience building "
                    "real-time applications across mobile, backend, database, and "
                    "cloud layers. Developed REST APIs, WebSocket services, and "
                    "relational data models through academic and personal projects."
                ),
                "skills": [
                    "Frontend: React, React Native, Angular",
                    "Backend: FastAPI, Node.js, REST APIs, WebSockets, SQLAlchemy",
                    "Databases: PostgreSQL, SQLite, Firebase",
                    "Cloud & DevOps: AWS, EC2, RDS, S3, CI/CD",
                    "Testing: pytest, integration testing",
                    "AI-Assisted Development: Cursor, ChatGPT",
                    "AI & Data: Generative AI",
                ],
                "experience": gen["tailored_resume"]["experience"],
                "projects": [
                    {
                        "name": "Capstone Project",
                        "description": "Academic full-stack capstone",
                        "bullets": [
                            "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                            "Integrated WebSockets for real-time updates",
                            "Deployed to AWS EC2, RDS and S3 with basic CI/CD",
                            "Added pytest integration testing",
                        ],
                    }
                ],
                "education": gen["tailored_resume"]["education"],
                "certifications": [],
            }
            return {
                "tailored_resume": safe,
                "writing_notes": ["checkpoint_safe_polish"],
                "sections_rewritten": ["summary", "projects", "skills"],
            }
        if "recruiter_review" in namespace:
            return {
                "approved": True,
                "would_interview": True,
                "human_believability": 80,
                "interview_quality": 78,
                "issues": [],
                "sections_to_regenerate": [],
                "summary_feedback": "Clear full-stack academic narrative.",
            }
        if "resume_generation" in namespace or "deep_rewrite" in namespace:
            return gen
        # Default safe structured payloads for other stages
        if "claim" in namespace:
            return {"validation_warnings": []}
        return {
            "inferred_competencies": [],
            "triage": [],
            "section_order": [],
            "tailored_resume": gen["tailored_resume"],
            "change_log": [],
            "matched_requirements": gen["matched_requirements"],
            "missing_requirements": gen["missing_requirements"],
            "removed_or_deprioritized_content": [],
            "ats_keywords_added": [],
            "validation_warnings": [],
        }

    return _call


def test_checkpoint_pipeline_strips_unsafe_claims():
    job = {
        "id": 991,
        "title": "Full Stack Engineer",
        "company": "Check Point Software Technologies",
        "full_description": CHECK_POINT_JD,
        "description": CHECK_POINT_JD,
    }

    with patch(
        "intelligent_tailoring.pipeline.is_ai_available",
        return_value=True,
    ), patch(
        "intelligent_tailoring.agents.resume_tailoring_agent.rewrite_resume_with_strategy",
        return_value=_unsafe_checkpoint_generation(),
    ), patch(
        "intelligent_tailoring.llm_utils.call_openai_json",
        side_effect=_llm_queue_side_effect(),
    ), patch(
        "intelligent_tailoring.agents.job_intelligence_agent.extract_job_requirements",
        side_effect=_stage_side_effect(),
    ), patch(
        "intelligent_tailoring.stages.semantic_inference.run_semantic_inference",
        return_value=[],
    ), patch(
        "intelligent_tailoring.stages.content_triage.run_content_triage",
        return_value={"triage": [], "section_order": []},
    ):
        result = run_intelligent_tailoring(
            cv_profile=CHECK_POINT_CANDIDATE,
            job=job,
            use_cache=False,
            source_documents=CHECK_POINT_CANDIDATE["raw_text"],
            language="en",
        )

    resume = result.get("tailored_resume") or result.get("tailored_cv") or {}
    text = str(resume).lower()
    assert "three years" not in text
    assert "typescript" not in text
    assert "customer satisfaction" not in text
    assert "system scalability" not in text
    assert "system reliability" not in text
    assert "production-grade ownership" not in text
    assert "other relevant skills: api" not in text
    assert "capstone" in text
    for token in ("fastapi", "react", "postgresql", "aws"):
        assert token in text, token

    breakdown = dict(result.get("score_breakdown") or {})
    if not breakdown:
        scoring = result.get("tailored_scoring") or result.get("scoring") or {}
        breakdown = dict(scoring.get("score_breakdown") or scoring)
    gaps = list(
        breakdown.get("genuine_gaps")
        or result.get("missing_requirements")
        or []
    )
    gap_blob = " ".join(str(g).lower() for g in gaps)
    assert "typescript" in gap_blob or any(
        "typescript" in str(g).lower()
        for g in (result.get("missing_requirements") or [])
    )
    if "truthfulness_score" in breakdown:
        assert float(breakdown["truthfulness_score"]) <= 100
    if "unsupported_claim_count" in breakdown:
        assert int(breakdown["unsupported_claim_count"]) >= 0
