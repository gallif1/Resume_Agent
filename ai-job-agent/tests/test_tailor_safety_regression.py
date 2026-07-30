"""Regression tests for release-blocking tailor safety defects.

Reproduces the Full Stack failure mode:
- Vue.js must not appear
- FastAPI projects must not become Node.js
- No unsupported impact claims
- Professional summary must survive into markdown/PDF parse path
- Structured change_log matches final resume
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from intelligent_tailoring.claim_validator import statement_supported_by_evidence
from intelligent_tailoring.knowledge_base import build_knowledge_base
from intelligent_tailoring.pipeline import run_intelligent_tailoring
from intelligent_tailoring.scope_validator import (
    has_unsupported_impact,
    neutralize_unsupported_impact,
    validate_bullet_tech_scope,
    validate_resume_tech_scope,
)
from intelligent_tailoring.change_log import build_deterministic_change_log
from tailor_cv_service import render_tailored_cv_markdown
from pdf_generator_service import parse_resume_markdown


FULL_STACK_SOURCE = {
    "contact": {"name": "Gal Lifshitz", "email": "gal@example.com"},
    "raw_text": (
        "Gal Lifshitz — Full Stack Developer. Skills: Python, FastAPI, SQLAlchemy, "
        "PostgreSQL, React, React Native, Angular, Node.js, AWS, Git, CI/CD, pytest, "
        "WebSockets, Generative AI, SQLite, Firebase.\n"
        "Projects:\n"
        "Capstone: Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL. "
        "Integrated WebSockets for real-time updates. Deployed to AWS EC2, RDS and S3. "
        "Added basic CI/CD and pytest integration testing. Integrated Generative AI features.\n"
        "Server Monitor: FastAPI service with ThreadPoolExecutor for concurrent health checks. "
        "AWS deployment with automated alerts.\n"
        "Restaurant App: React Native mobile UI with FastAPI backend, SQLite and Firebase."
    ),
    "skills": {
        "languages": ["Python", "JavaScript"],
        "frameworks": ["FastAPI", "React", "React Native", "Angular", "Node.js"],
        "databases": ["PostgreSQL", "SQLite", "Firebase", "SQLAlchemy"],
        "cloud": ["AWS", "CI/CD", "Git"],
        "other": ["WebSockets", "pytest", "Generative AI"],
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
                ],
            }
        ]
    },
    "projects": [
        {
            "name": "Capstone Project",
            "description": "Full-stack backend architecture",
            "technologies": [
                "FastAPI",
                "SQLAlchemy",
                "PostgreSQL",
                "WebSockets",
                "AWS",
                "CI/CD",
                "pytest",
                "Generative AI",
            ],
            "bullets": [
                "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
                "Integrated WebSockets for real-time updates",
                "Deployed to AWS EC2, RDS and S3 with basic CI/CD",
                "Added pytest integration testing",
                "Integrated Generative AI features",
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


def _unsafe_llm_generation() -> dict[str, Any]:
    """Simulates the defective LLM output the user observed."""
    return {
        "tailored_resume": {
            "professional_title": "Full Stack Developer",
            "professional_summary": (
                "Full Stack developer with FastAPI, React, and cloud deployment experience, "
                "building reliable APIs and responsive interfaces."
            ),
            "skills": [
                "Frontend: React, React Native, Angular, Vue.js",
                "Backend: Node.js, FastAPI",
                "Cloud: AWS",
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
                    "description": "Built with Node.js improving user engagement",
                    "bullets": [
                        "Built the project with Node.js enhancing system reliability",
                        "Used WebSockets improving user engagement",
                        "Deployed ensuring efficient data management",
                    ],
                },
                {
                    "name": "Server Monitor",
                    "description": "Node.js monitoring service",
                    "bullets": [
                        "Implemented ThreadPoolExecutor enhancing system reliability",
                    ],
                },
                {
                    "name": "Restaurant App",
                    "description": "Ordering application",
                    "bullets": [
                        "Built React Native mobile UI",
                        "Created FastAPI backend with SQLite and Firebase",
                    ],
                },
            ],
            "education": [
                {"institution": "Tel Hai University", "degree": "B.Sc. Computer Science"}
            ],
            "certifications": [],
        },
        "change_log": [
            {
                "original_text": "",
                "new_text": (
                    "Full Stack developer with FastAPI, React, and cloud deployment experience, "
                    "building reliable APIs and responsive interfaces."
                ),
                "reason": "Summary for full stack role",
                "supporting_evidence": "FastAPI React AWS",
                "related_job_requirement": "Full Stack",
                "inference_category": "Explicit",
                "confidence_score": 1.0,
            }
        ],
        "matched_requirements": ["FastAPI", "React", "AWS"],
        "missing_requirements": [],
        "removed_or_deprioritized_content": [],
        "ats_keywords_added": ["Vue.js", "Node.js"],
    }


def _stage_side_effect():
    reqs = {
        "required_skills": [
            "FastAPI",
            "React",
            "Node.js",
            "PostgreSQL",
            "AWS",
            "CI/CD",
        ],
        "preferred_skills": ["Vue.js"],
        "responsibilities": [
            "build full stack applications",
            "deploy to cloud",
            "write tests",
        ],
        "tools_technologies": ["FastAPI", "React", "Node.js", "AWS", "pytest"],
        "industry_terminology": ["full stack"],
        "seniority_level": "junior",
        "soft_skills": [],
        "education_certifications": [],
        "ats_keywords": ["FastAPI", "React", "Node.js", "Vue.js", "AWS", "CI/CD"],
        "hard_requirements": ["FastAPI", "React", "AWS"],
        "soft_requirements": ["Vue.js", "Node.js"],
        "language": "en",
    }
    gen = _unsafe_llm_generation()
    queue = [
        reqs,
        {"inferred_competencies": []},
        {"triage": [], "section_order": []},
        gen,
        {"validation_warnings": []},
    ]

    def _call(*_a, **_k):
        if queue:
            return queue.pop(0)
        return gen

    return _call


@pytest.fixture(autouse=True)
def _ai(monkeypatch):
    monkeypatch.setattr(
        "intelligent_tailoring.pipeline.is_ai_available", lambda: True
    )


class TestScopeValidatorUnit:
    def test_node_cannot_enter_fastapi_project(self):
        facts = [
            {
                "source_entry_id": "project_0",
                "source_section": "projects",
                "original_text": "Designed backend with FastAPI and PostgreSQL",
                "explicit_skills": ["FastAPI", "PostgreSQL"],
            },
            {
                "source_entry_id": "skill_node",
                "source_section": "skills",
                "original_text": "Node.js",
                "explicit_skills": ["Node.js"],
            },
        ]
        ok, reason, leaked = validate_bullet_tech_scope(
            "Built the project with Node.js",
            source_entry_id="project_0",
            facts=facts,
            entry_source_text="Designed backend with FastAPI and PostgreSQL",
        )
        assert ok is False
        assert "node" in ",".join(leaked)

    def test_impact_without_metric_rejected(self):
        assert has_unsupported_impact(
            "Used WebSockets improving user engagement",
            "Integrated WebSockets for real-time updates",
        )
        fixed = neutralize_unsupported_impact(
            "Used WebSockets improving user engagement"
        )
        assert "improving" not in fixed.lower()

    def test_claim_validator_rejects_vue(self):
        ok, reason = statement_supported_by_evidence(
            "Built interfaces with Vue.js",
            source_text=FULL_STACK_SOURCE["raw_text"],
        )
        assert ok is False
        assert "unsupported" in reason or "entities" in reason or "skill" in reason

    def test_claim_validator_rejects_impact(self):
        ok, reason = statement_supported_by_evidence(
            "Used ThreadPoolExecutor enhancing system reliability",
            source_text="Implemented FastAPI service with ThreadPoolExecutor",
        )
        assert ok is False
        assert "impact" in reason


class TestFullStackRegression:
    def test_unsafe_generation_is_sanitized(self):
        with patch(
            "intelligent_tailoring.llm_utils.call_openai_json",
            side_effect=_stage_side_effect(),
        ):
            result = run_intelligent_tailoring(
                cv_profile=FULL_STACK_SOURCE,
                job={
                    "id": 1,
                    "title": "Full Stack Developer",
                    "company": "TestCo",
                    "full_description": (
                        "Required: FastAPI, React, Node.js, PostgreSQL, AWS, CI/CD. "
                        "Preferred: Vue.js. Build full stack apps, deploy to cloud, write tests."
                    ),
                },
                use_cache=False,
            )

        resume = result["tailored_resume"]
        blob = str(resume).lower()

        # 1) Vue.js must not appear
        assert "vue" not in blob

        # 2) Capstone must not be rewritten as Node.js
        capstone = next(
            p for p in resume["projects"] if "capstone" in str(p.get("name") or "").lower()
        )
        capstone_blob = (
            str(capstone.get("description") or "")
            + " "
            + " ".join(str(b) for b in (capstone.get("bullets") or []))
        ).lower()
        assert "node" not in capstone_blob
        # FastAPI evidence should remain available somewhere in projects
        projects_blob = str(resume.get("projects")).lower()
        assert "fastapi" in projects_blob or "react" in projects_blob

        # 3) No unsupported impact phrasing
        for bad in (
            "improving user engagement",
            "enhancing system reliability",
            "ensuring efficient data management",
        ):
            assert bad not in blob

        # 4) Summary must exist
        summary = str(
            resume.get("professional_summary") or resume.get("summary") or ""
        ).strip()
        assert summary, "professional summary must survive validation"

        # 5) Summary must appear in markdown (PDF path)
        legacy = {
            **resume,
            "summary": summary,
            "professional_summary": summary,
        }
        md = render_tailored_cv_markdown(
            legacy, name="Gal Lifshitz", target_role="Full Stack Developer"
        )
        assert "## Professional Summary" in md
        assert summary.split(".")[0][:40] in md or summary[:40] in md

        parsed = parse_resume_markdown(md)
        parsed_summary = " ".join(
            " ".join(s.paragraphs)
            for s in (parsed.sections or [])
            if getattr(s, "kind", "") == "summary"
        ).strip()
        assert parsed_summary, "PDF parser must see the summary"

        # 6) Change log is structured and matches final resume
        for item in result.get("change_log") or []:
            assert isinstance(item, dict)
            assert item.get("section"), "change_log items must include section"
            assert item.get("change_type"), "change_log items must include change_type"
            assert "reason" in item
            new_text = str(item.get("new_text") or "")
            if item.get("change_type") in ("removed", "deprioritized", "reordered"):
                continue
            if new_text and item.get("change_type") == "rewritten":
                # Must not advertise removed unsafe content
                assert "vue.js" not in new_text.lower()
                # Must appear in final resume
                assert new_text.lower()[:40] in blob or new_text.lower() in blob

        # 7) Capstone must retain FastAPI evidence specifically
        assert "fastapi" in capstone_blob

        # 8) Quality gates present
        gates = result.get("quality_gates") or {}
        assert "passed" in gates
        hard = [
            f
            for f in (gates.get("failures") or [])
            if any(
                str(f).startswith(p)
                for p in (
                    "unsupported_impact",
                    "unsupported_entity",
                    "cross_entry_tech",
                    "unknown_skill",
                    "missing_professional_summary",
                )
            )
        ]
        assert not hard, f"hard gate failures remain: {hard}"

    def test_profession_agnostic_crm_scope(self):
        """CRM in general skills must not attach to an employer without evidence."""
        facts = [
            {
                "source_entry_id": "role_0",
                "source_section": "experience",
                "original_text": "Handled inbound customer complaints by phone",
                "explicit_skills": [],
            },
            {
                "source_entry_id": "skill_crm",
                "source_section": "skills",
                "original_text": "Salesforce CRM",
                "explicit_skills": ["Salesforce"],
            },
        ]
        ok, reason, leaked = validate_bullet_tech_scope(
            "Managed Salesforce CRM for the account team",
            source_entry_id="role_0",
            facts=facts,
            entry_source_text="Handled inbound customer complaints by phone",
        )
        assert ok is False
        assert leaked

    def test_quality_gates_block_unsafe_resume(self):
        from intelligent_tailoring.quality_gates import evaluate_quality_gates

        kb = build_knowledge_base(FULL_STACK_SOURCE)
        unsafe = _unsafe_llm_generation()["tailored_resume"]
        gates = evaluate_quality_gates(
            tailored_resume=unsafe,
            original_resume_text=FULL_STACK_SOURCE["raw_text"],
            facts=[f.to_dict() for f in kb.facts],
            change_log=[
                {
                    "section": "projects",
                    "change_type": "rewritten",
                    "new_text": "Built with Vue.js",
                    "reason": "hallucination",
                }
            ],
            original_projects=FULL_STACK_SOURCE["projects"],
            require_summary=True,
        )
        assert gates["passed"] is False
        assert any(
            "vue" in f.lower()
            or "unknown_skill" in f
            or "cross_entry" in f
            or "unsupported" in f
            or "change_log" in f
            for f in gates["failures"]
        )

    def test_kb_binds_project_technologies(self):
        kb = build_knowledge_base(FULL_STACK_SOURCE)
        capstone_techs = [
            f
            for f in kb.facts
            if f.source_entry_id == "project_0" and f.fact_type == "technology"
        ]
        values = {f.original_text.lower() for f in capstone_techs}
        assert "fastapi" in values
        assert "sqlalchemy" in values
        # Node.js is a general skill, not bound to capstone
        assert "node.js" not in values and "nodejs" not in values

    def test_scope_cleaner_fixes_unsafe_resume(self):
        kb = build_knowledge_base(FULL_STACK_SOURCE)
        unsafe = _unsafe_llm_generation()["tailored_resume"]
        result = validate_resume_tech_scope(
            unsafe,
            facts=[f.to_dict() for f in kb.facts],
            original_projects=FULL_STACK_SOURCE["projects"],
            original_roles=FULL_STACK_SOURCE["experience"]["roles"],
        )
        cleaned = result["cleaned_resume"]
        blob = str(cleaned).lower()
        assert "vue" not in blob
        capstone = next(
            p for p in cleaned["projects"] if "capstone" in str(p.get("name") or "").lower()
        )
        capstone_blob = str(capstone).lower()
        assert "node" not in capstone_blob
        assert "improving user engagement" not in blob
        assert cleaned.get("professional_summary") or cleaned.get("summary")

    def test_deterministic_change_log_excludes_rejected_text(self):
        baseline = {
            "professional_summary": "",
            "skills": ["FastAPI", "React", "Node.js"],
            "experience": [],
            "projects": FULL_STACK_SOURCE["projects"],
        }
        final = {
            "professional_summary": "Full Stack developer with FastAPI and React.",
            "skills": ["FastAPI", "React", "AWS"],
            "experience": [],
            "projects": [
                {
                    "name": "Capstone Project",
                    "bullets": [
                        "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL"
                    ],
                }
            ],
        }
        log = build_deterministic_change_log(
            baseline_resume=baseline,
            final_resume=final,
            prior_llm_change_log=[
                {
                    "new_text": "Built with Vue.js",
                    "reason": "hallucination",
                    "inference_category": "Explicit",
                    "confidence_score": 1.0,
                },
                {
                    "new_text": "Full Stack developer with FastAPI and React.",
                    "reason": "summary",
                    "inference_category": "Explicit",
                    "confidence_score": 1.0,
                },
            ],
        )
        joined = " ".join(str(i.get("new_text") or "") for i in log).lower()
        assert "vue" not in joined
        assert any("full stack developer" in str(i.get("new_text") or "").lower() for i in log)
