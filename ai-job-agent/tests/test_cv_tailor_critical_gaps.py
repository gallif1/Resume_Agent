"""Critical gaps workflow tests for CV Tailor."""

from __future__ import annotations

from unittest.mock import patch

import api_server
import db
from conftest import auth_header_for, register_test_user
from cv_tailor.models import CandidateFact, JobAnalysis, RegenerateCvRequest
from cv_tailor.service import CvTailorError, generate_tailored_cv, regenerate_tailored_cv
from cv_tailor.validation import apply_factual_guards, parse_regenerate_response
from fastapi.testclient import TestClient

GAL_TEL_HAI_SOURCE = """
Gal Lifshitz
Junior Software Developer
Backend: FastAPI, Node.js, REST APIs, WebSockets, React
Cloud & Tools: AWS (EC2, RDS, S3), Git, CI/CD, pytest, MongoDB
Frontend: React, HTML, CSS
Capstone Project Lead – Tribe Platform | Tel Hai University | 2024 – 2025
• Deployed backend infrastructure using AWS (EC2, RDS, S3) and implemented basic CI/CD workflows
• Implemented automated testing using pytest including integration tests and reusable testing utilities
Server Monitor System
• Implemented background worker performing parallel health checks (HTTP, FTP, SSH)
Education
B.Sc. Computer Science – Tel Hai University (Mar 2022 – Aug 2025)
"""

MINUTE_MEDIA_FULLSTACK_JD = """
Minute Media — Full-Stack Developer

Requirements:
- 4+ years of full-stack development experience
- Strong vanilla JavaScript and React experience
- REST APIs and backend services
- MySQL database experience
- Go programming experience
- MongoDB
- AWS cloud deployment
- CI/CD pipelines
- Git version control
- HTML/CSS
- B.Sc. in Computer Science or equivalent
- Experience using AI coding assistant workflows (Cursor, Copilot, etc.)

Nice to have:
- GraphQL
"""

INITIAL_MINUTE_MEDIA_RESPONSE = {
    "tailored_cv": {
        "name": "Gal Lifshitz",
        "contact": "Israel | gal@example.com",
        "professional_title": "Full-Stack Developer",
        "summary": "Full-stack developer with React, REST APIs, AWS, CI/CD, and MongoDB experience.",
        "skill_groups": [
            {"category": "Frontend", "skills": ["React", "HTML", "CSS"]},
            {"category": "Backend", "skills": ["FastAPI", "REST APIs", "Node.js"]},
            {"category": "Cloud & DevOps", "skills": ["AWS (EC2, RDS, S3)", "CI/CD", "Git"]},
            {"category": "Databases", "skills": ["MongoDB", "PostgreSQL"]},
        ],
        "experience": [
            {
                "company": "Tel Hai University",
                "role": "Capstone Project Lead – Tribe Platform",
                "dates": "2024 – 2025",
                "bullets": [
                    "Deployed backend infrastructure using AWS (EC2, RDS, S3) and implemented basic CI/CD workflows",
                    "Implemented automated testing using pytest including integration tests and reusable testing utilities",
                ],
            }
        ],
        "projects": [],
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
        "target_job_title": "Full-Stack Developer",
        "seniority_required": "4+ years full-stack",
        "must_have_technologies": [
            "JavaScript",
            "React",
            "REST APIs",
            "MySQL",
            "Go",
            "MongoDB",
            "AWS",
            "CI/CD",
            "Git",
            "HTML/CSS",
            "AI coding assistants",
        ],
        "nice_to_have": ["GraphQL"],
        "key_phrases": [],
        "strong_matches": [
            "React",
            "REST APIs",
            "MongoDB",
            "AWS",
            "CI/CD",
            "Git",
            "HTML/CSS",
            "B.Sc. Computer Science",
        ],
        "gaps": [
            {
                "gap_id": "full-stack-years",
                "title": "Full-stack experience",
                "requirement": "4+ years full-stack development",
                "job_requirement_text": "The position requires 4+ years of full-stack development experience.",
                "cv_evidence": "The CV shows recent university and project work but does not document 4+ years of full-stack experience.",
                "confirmation_text": "I confirm that I have at least 4 years of full-stack development experience.",
                "status": "UNSUPPORTED",
                "explanation": "No dated evidence for 4+ years of full-stack development.",
            },
            {
                "gap_id": "vanilla-javascript",
                "title": "Vanilla JavaScript",
                "requirement": "Strong vanilla JavaScript",
                "job_requirement_text": "The position requires strong JavaScript experience.",
                "cv_evidence": "React is listed, but JavaScript experience is not explicitly documented.",
                "confirmation_text": "I confirm that I have hands-on JavaScript development experience.",
                "status": "UNSUPPORTED",
                "explanation": "JavaScript is implied via React but not explicitly documented.",
            },
            {
                "gap_id": "mysql",
                "title": "MySQL",
                "requirement": "MySQL database experience",
                "job_requirement_text": "The position requires MySQL database experience.",
                "cv_evidence": "MongoDB and PostgreSQL appear in the CV, but MySQL is not listed.",
                "confirmation_text": "I confirm that I have hands-on experience with MySQL.",
                "status": "UNSUPPORTED",
                "explanation": "MySQL was not found in the source CV.",
            },
            {
                "gap_id": "go",
                "title": "Go",
                "requirement": "Go programming experience",
                "job_requirement_text": "The position requires Go programming experience.",
                "cv_evidence": "Go is not mentioned anywhere in the CV.",
                "confirmation_text": "I confirm that I have hands-on Go development experience.",
                "status": "UNSUPPORTED",
                "explanation": "Go was not found in the source CV.",
            },
            {
                "gap_id": "ai-coding-tools",
                "title": "AI coding assistant workflow",
                "requirement": "AI coding assistant workflows",
                "job_requirement_text": "The position expects experience using AI coding assistant workflows.",
                "cv_evidence": "No AI coding assistant tools are mentioned in the CV.",
                "confirmation_text": "I confirm that I have experience using AI coding assistant workflows in development.",
                "status": "UNSUPPORTED",
                "explanation": "AI coding assistant usage was not found in the source CV.",
            },
        ],
        "resolved_requirements": [],
    },
}

REGENERATED_MINUTE_MEDIA_RESPONSE = {
    "normalized_new_facts": [
        {
            "fact": "I used JavaScript mainly with React in university and personal projects.",
            "normalized_fact": "Hands-on JavaScript development experience through React-based university and personal projects.",
            "source": "user_confirmed",
            "gap_id": "vanilla-javascript",
        },
        {
            "fact": "I played around with MySQL a little.",
            "normalized_fact": "Basic familiarity with MySQL.",
            "source": "user_confirmed",
            "gap_id": "mysql",
        },
    ],
    "tailored_cv": INITIAL_MINUTE_MEDIA_RESPONSE["tailored_cv"]
    | {
        "summary": "Full-stack developer with React, JavaScript, REST APIs, AWS, CI/CD, MongoDB, and basic MySQL familiarity.",
        "skill_groups": [
            {"category": "Frontend", "skills": ["React", "JavaScript", "HTML", "CSS"]},
            {"category": "Backend", "skills": ["FastAPI", "REST APIs", "Node.js"]},
            {"category": "Cloud & DevOps", "skills": ["AWS (EC2, RDS, S3)", "CI/CD", "Git"]},
            {"category": "Databases", "skills": ["MongoDB", "PostgreSQL", "MySQL"]},
        ],
    },
    "job_analysis": {
        **INITIAL_MINUTE_MEDIA_RESPONSE["job_analysis"],
        "strong_matches": INITIAL_MINUTE_MEDIA_RESPONSE["job_analysis"]["strong_matches"] + ["JavaScript"],
        "resolved_requirements": [
            {
                "requirement": "JavaScript",
                "title": "Vanilla JavaScript",
                "status": "USER_CONFIRMED",
                "note": "Supported by user-confirmed JavaScript experience.",
            },
            {
                "requirement": "MySQL",
                "title": "MySQL",
                "status": "USER_CONFIRMED",
                "note": "Supported by user-confirmed basic MySQL familiarity.",
            },
        ],
        "gaps": [
            gap
            for gap in INITIAL_MINUTE_MEDIA_RESPONSE["job_analysis"]["gaps"]
            if gap["gap_id"] not in {"vanilla-javascript", "mysql"}
        ],
    },
}


def test_job_analysis_parses_extended_gap_fields():
    analysis = JobAnalysis.from_llm_dict(INITIAL_MINUTE_MEDIA_RESPONSE["job_analysis"])
    assert len(analysis.gaps) == 5
    js_gap = next(g for g in analysis.gaps if g.gap_id == "vanilla-javascript")
    assert js_gap.title == "Vanilla JavaScript"
    assert js_gap.confirmation_text.startswith("I confirm that I have hands-on")
    assert js_gap.status == "UNSUPPORTED"
    assert "React" in analysis.strong_matches[0] or "React" in analysis.strong_matches


def test_minute_media_gap_topics_are_material():
    analysis = JobAnalysis.from_llm_dict(INITIAL_MINUTE_MEDIA_RESPONSE["job_analysis"])
    gap_titles = {gap.title.lower() for gap in analysis.gaps}
    assert any("javascript" in title for title in gap_titles)
    assert any("mysql" in title for title in gap_titles)
    assert any("go" in title for title in gap_titles)
    assert any("full-stack" in title for title in gap_titles)
    assert any("ai" in title for title in gap_titles)

    matches = " ".join(analysis.strong_matches).lower()
    for token in ("react", "rest", "mongodb", "aws", "ci/cd", "git", "html", "computer science"):
        assert token in matches


def test_user_confirmed_skills_allowed_in_output():
    cv, _, _ = parse_regenerate_response(REGENERATED_MINUTE_MEDIA_RESPONSE)
    confirmed = [
        CandidateFact(
            fact="Hands-on JavaScript development experience.",
            normalized_fact="Hands-on JavaScript development experience.",
            source="user_confirmed",
            gap_id="vanilla-javascript",
        ),
        CandidateFact(
            fact="Basic familiarity with MySQL.",
            normalized_fact="Basic familiarity with MySQL.",
            source="user_confirmed",
            gap_id="mysql",
        ),
    ]
    repaired = apply_factual_guards(
        GAL_TEL_HAI_SOURCE,
        cv,
        user_confirmed_facts=confirmed,
    )
    flat_skills = [skill for group in repaired.skill_groups for skill in group.skills]
    assert "JavaScript" in flat_skills
    assert "MySQL" in flat_skills
    assert "Go" not in flat_skills


def test_regenerate_service_merges_facts_and_updates_gaps():
    mock_initial = INITIAL_MINUTE_MEDIA_RESPONSE
    mock_regen = REGENERATED_MINUTE_MEDIA_RESPONSE
    fake_pdf = b"%PDF-1.4 regen"

    with patch("cv_tailor.service.call_openai_json", side_effect=[mock_initial, mock_regen]):
        with patch(
            "cv_tailor.parser.extract_text_from_resume",
            return_value=(GAL_TEL_HAI_SOURCE, "pdf:test"),
        ):
            with patch("cv_tailor.service.render_tailored_cv_pdf", return_value=fake_pdf):
                initial = generate_tailored_cv(
                    file_bytes=b"pdf-bytes",
                    filename="cv.pdf",
                    job_description=MINUTE_MEDIA_FULLSTACK_JD,
                    user_id="user-1",
                )

    assert len(initial.job_analysis.gaps) == 5

    js_gap = next(g for g in initial.job_analysis.gaps if g.gap_id == "vanilla-javascript")
    request = RegenerateCvRequest(
        gap_confirmations=[
            {"gap_id": js_gap.gap_id, "confirmed": True, "details": ""},
            {
                "gap_id": "mysql",
                "confirmed": False,
                "details": "I played around with MySQL a little.",
            },
            {
                "gap_id": "vanilla-javascript",
                "confirmed": False,
                "details": "I used JavaScript mainly with React in university and personal projects.",
            },
        ],
        general_additional_info="",
    )

    with patch("cv_tailor.service.call_openai_json", return_value=mock_regen):
        with patch("cv_tailor.service.render_tailored_cv_pdf", return_value=fake_pdf):
            updated = regenerate_tailored_cv(
                result_id=initial.result_id,
                user_id="user-1",
                request=request,
            )

    assert updated.result_id == initial.result_id
    assert len(updated.user_confirmed_facts) >= 2
    gap_ids = {gap.gap_id for gap in updated.job_analysis.gaps}
    assert "vanilla-javascript" not in gap_ids
    assert "mysql" not in gap_ids
    assert any(item.status == "USER_CONFIRMED" for item in updated.job_analysis.resolved_requirements)


def test_regenerate_requires_input():
    mock_initial = INITIAL_MINUTE_MEDIA_RESPONSE
    fake_pdf = b"%PDF-1.4 regen"

    with patch("cv_tailor.service.call_openai_json", return_value=mock_initial):
        with patch(
            "cv_tailor.parser.extract_text_from_resume",
            return_value=(GAL_TEL_HAI_SOURCE, "pdf:test"),
        ):
            with patch("cv_tailor.service.render_tailored_cv_pdf", return_value=fake_pdf):
                initial = generate_tailored_cv(
                    file_bytes=b"pdf-bytes",
                    filename="cv.pdf",
                    job_description=MINUTE_MEDIA_FULLSTACK_JD,
                    user_id="user-1",
                )

    try:
        regenerate_tailored_cv(
            result_id=initial.result_id,
            user_id="user-1",
            request=RegenerateCvRequest(),
        )
        assert False, "expected CvTailorError"
    except CvTailorError as exc:
        assert "confirm" in str(exc).lower() or "additional" in str(exc).lower()


def test_api_cv_tailor_regenerate_endpoint(db_path, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)

    user = register_test_user(email="gaps@example.com", db_path=db_path)
    client = TestClient(api_server.app)
    fake_pdf = b"%PDF-1.4 tailored"

    with patch("cv_tailor.service.call_openai_json", side_effect=[INITIAL_MINUTE_MEDIA_RESPONSE, REGENERATED_MINUTE_MEDIA_RESPONSE]):
        with patch(
            "cv_tailor.parser.extract_text_from_resume",
            return_value=(GAL_TEL_HAI_SOURCE, "pdf:test"),
        ):
            with patch("cv_tailor.service.render_tailored_cv_pdf", return_value=fake_pdf):
                generate_res = client.post(
                    "/api/cv-tailor/generate",
                    headers=auth_header_for(user),
                    files={"file": ("resume.pdf", b"pdf-bytes", "application/pdf")},
                    data={"job_description": MINUTE_MEDIA_FULLSTACK_JD},
                )

                assert generate_res.status_code == 200, generate_res.text
                result_id = generate_res.json()["result_id"]
                gaps = generate_res.json()["job_analysis"]["gaps"]
                assert len(gaps) >= 3

                js_gap = next(g for g in gaps if "javascript" in g["gap_id"])
                regen_res = client.post(
                    f"/api/cv-tailor/regenerate/{result_id}",
                    headers=auth_header_for(user),
                    json={
                        "gap_confirmations": [
                            {"gap_id": js_gap["gap_id"], "confirmed": True, "details": ""},
                            {
                                "gap_id": "mysql",
                                "confirmed": False,
                                "details": "I played around with MySQL a little.",
                            },
                        ],
                        "general_additional_info": "Also used Cursor for AI-assisted coding.",
                    },
                )

    assert regen_res.status_code == 200, regen_res.text
    body = regen_res.json()
    assert body["result_id"] == result_id
    assert body["user_confirmed_facts"]
    remaining_ids = {gap["gap_id"] for gap in body["job_analysis"]["gaps"]}
    assert js_gap["gap_id"] not in remaining_ids


def test_requirement_gap_slug_fallback():
    analysis = JobAnalysis.from_llm_dict(
        {
            "strong_matches": [],
            "gaps": [
                {
                    "requirement": "3+ years Node.js",
                    "status": "insufficient_evidence",
                    "explanation": "Insufficient evidence.",
                }
            ],
        }
    )
    assert analysis.gaps[0].gap_id
    assert analysis.gaps[0].status == "UNSUPPORTED"


def test_legacy_gap_status_mapping():
    analysis = JobAnalysis.from_llm_dict(
        {"gaps": [{"requirement": "Java", "status": "not_found", "explanation": "Missing."}]}
    )
    assert analysis.gaps[0].status == "UNSUPPORTED"
