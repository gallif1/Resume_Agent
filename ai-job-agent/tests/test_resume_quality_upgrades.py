"""Tests for resume writing quality upgrades (no new agents)."""

from __future__ import annotations

from typing import Any

import pytest

from intelligent_tailoring.services.evidence_amplifier import (
    apply_evidence_amplification,
    build_evidence_inventory,
    build_highlight_plan,
    ensure_skill_propagation,
    score_requirement_support,
)
from intelligent_tailoring.skill_taxonomy import (
    category_order_for_role,
    normalize_skill_lines,
)
from intelligent_tailoring.summary_builder import (
    build_professional_summary,
    build_summary_plan,
    summary_passes_checks,
)
from intelligent_tailoring.writing.ai_phrases import AI_CLICHE_PHRASES
from intelligent_tailoring.writing.resume_quality_score import evaluate_resume_quality
from intelligent_tailoring.writing.writing_pipeline import (
    _compose_writer_feedback,
    run_human_writing_stage,
)


RESUME_TEXT = """
Backend Engineer
Acme Corp — Software Engineer (2021-2025)
- Built REST APIs in Python serving production traffic.
- Designed relational PostgreSQL schemas for request tracking.
- Deployed services on AWS with Docker.
Projects
Order Service
- Created database schema
- Implemented request validation endpoints
Skills: Python, PostgreSQL, Docker, AWS, REST APIs, React
"""


def test_summary_avoids_ai_filler_and_answers_fit():
    strategy = {
        "honest_title": "Backend Developer",
        "job_family": "backend",
        "skills_to_emphasize": ["Python", "PostgreSQL", "Docker", "REST APIs"],
        "must_highlight_in_summary": ["Python", "PostgreSQL"],
        "summary_focus": "Explain why this candidate fits Backend Developer roles.",
        "primary_role": "Backend Developer",
    }
    facts = {
        "skills": ["Python", "PostgreSQL", "Docker", "AWS", "REST APIs"],
        "projects": [
            {
                "name": "Order Service",
                "bullets": [
                    "Designed relational PostgreSQL schemas for request tracking.",
                    "Built REST APIs in Python serving production traffic.",
                ],
            }
        ],
        "experience_roles": [],
    }
    result = build_professional_summary(
        strategy=strategy,
        resume_facts=facts,
        resume_text=RESUME_TEXT,
        output_language="en",
        existing_summary="Professional with Knowledge of Docker and Web Experience.",
    )
    summary = result["summary"]
    assert summary
    low = summary.lower()
    assert "professional with knowledge" not in low
    assert "passionate about" not in low
    assert "highly motivated" not in low
    assert "strong understanding" not in low
    assert "python" in low or "postgresql" in low
    ok, errs = summary_passes_checks(summary, resume_text=RESUME_TEXT)
    assert ok, errs


def test_summary_plan_uses_must_highlight_and_evidence():
    plan = build_summary_plan(
        strategy={
            "primary_role": "Backend Developer",
            "job_family": "backend",
            "must_highlight_in_summary": ["PostgreSQL", "Python"],
            "skills_to_emphasize": ["Docker"],
        },
        resume_facts={
            "projects": [
                {
                    "name": "Order Service",
                    "bullets": ["Designed relational PostgreSQL schemas for request tracking."],
                }
            ]
        },
        resume_text=RESUME_TEXT,
    )
    assert "PostgreSQL" in plan["top_supported_competencies"]
    assert "postgresql" in plan["strongest_evidence"].lower()


def test_skill_category_order_differs_by_role():
    backend = category_order_for_role("backend")
    frontend = category_order_for_role("frontend")
    data = category_order_for_role("data")
    sales = category_order_for_role("sales")
    assert backend[0] == "Backend"
    assert frontend[0] == "Frontend"
    assert data[0] == "AI & Data"
    assert sales[0] == "Sales"
    assert backend != frontend

    skills = [
        "React",
        "Python",
        "PostgreSQL",
        "Docker",
        "AWS",
        "HTML",
        "CSS",
    ]
    backend_lines = normalize_skill_lines(
        skills, emphasize=["Python", "PostgreSQL"], job_family="backend"
    )
    frontend_lines = normalize_skill_lines(
        skills, emphasize=["React", "HTML"], job_family="frontend"
    )
    assert backend_lines[0].startswith("Backend") or "Python" in backend_lines[0]
    assert frontend_lines[0].startswith("Frontend")


def test_highlight_plan_marks_supported_requirements():
    evidence_map = [
        {
            "requirement": "Python",
            "importance": "hard",
            "candidate_status": "MATCH",
            "inference_category": "Explicit",
            "evidence_strength": "Explicit Evidence",
            "supporting_evidence": "Built REST APIs in Python",
        },
        {
            "requirement": "Kubernetes",
            "importance": "hard",
            "candidate_status": "MISSING",
            "inference_category": "Unsupported",
            "evidence_strength": "No Evidence",
        },
        {
            "requirement": "Docker",
            "importance": "soft",
            "candidate_status": "MATCH",
            "inference_category": "Explicit",
            "evidence_strength": "Explicit Evidence",
        },
    ]
    support = score_requirement_support(evidence_map)
    by_req = {s["requirement"]: s for s in support}
    assert by_req["Python"]["support"] == "Explicit"
    assert by_req["Python"]["must_highlight"] is True
    assert by_req["Kubernetes"]["support"] == "Unsupported"
    plan = build_highlight_plan(
        evidence_map=evidence_map, skills_to_emphasize=["Python", "Docker"]
    )
    assert "Python" in plan["must_highlight"]
    assert "Kubernetes" in plan["unsupported_hard"]


def test_evidence_inventory_and_thin_project_expansion():
    class _Fact:
        def __init__(self, text, section, entry_id, org=""):
            self.original_text = text
            self.source_section = section
            self.source_entry_id = entry_id
            self.organization = org
            self.fact_type = "task"

    class _KB:
        facts = [
            _Fact(
                "Designed relational PostgreSQL schemas supporting validation and request tracking.",
                "projects",
                "project_0",
                "Order Service",
            ),
            _Fact(
                "Implemented REST validation endpoints for incoming orders.",
                "projects",
                "project_0",
                "Order Service",
            ),
        ]

    facts = {
        "raw_text": RESUME_TEXT,
        "skills": ["Python", "React"],
        "projects": [
            {
                "name": "Order Service",
                "description": "Created database schema",
                "bullets": ["Created database schema"],
            }
        ],
        "experience_roles": [
            {
                "company": "Acme",
                "title": "Engineer",
                "bullets": ["Built REST APIs in Python serving production traffic."],
            }
        ],
    }
    inventory = build_evidence_inventory(facts)
    assert inventory["thin_projects"] == ["Order Service"]
    assert "python" in inventory["all_technologies"]

    updated, enrichment = apply_evidence_amplification(
        resume_facts=facts,
        evidence_map=[
            {
                "requirement": "PostgreSQL",
                "importance": "hard",
                "candidate_status": "MATCH",
                "inference_category": "Explicit",
                "evidence_strength": "Explicit Evidence",
            }
        ],
        strategy={"skills_to_emphasize": ["PostgreSQL", "Python"]},
        kb=_KB(),
        resume_text=RESUME_TEXT,
    )
    bullets = updated["projects"][0]["bullets"]
    assert len(bullets) >= 2
    assert any("PostgreSQL" in b or "REST" in b for b in bullets)
    assert "PostgreSQL" in enrichment["propagate_terms"] or "Python" in enrichment[
        "propagate_terms"
    ]


def test_skill_propagation_only_adds_evidenced_terms():
    skills = ["Languages: Python"]
    out = ensure_skill_propagation(
        skills,
        propagate_terms=["Docker", "Kubernetes", "AWS"],
        resume_text="Used Python and Docker on AWS daily.",
    )
    blob = " ".join(out).lower()
    assert "docker" in blob
    assert "aws" in blob
    assert "kubernetes" not in blob  # not in resume text


def test_quality_score_flags_ai_summary_and_weak_coverage():
    resume = {
        "professional_summary": "Professional with Knowledge of Docker and highly motivated attitude.",
        "skills": ["Tools & Version Control: Git"],
        "experience": [
            {"company": "Acme", "title": "Dev", "bullets": ["Worked on stuff."]}
        ],
        "projects": [{"name": "App", "bullets": ["Created database schema"]}],
    }
    score = evaluate_resume_quality(
        resume,
        strategy={
            "job_family": "backend",
            "skills_to_emphasize": ["Python", "PostgreSQL", "Docker"],
            "summary_focus": "backend python postgresql",
        },
        highlight_plan={
            "must_highlight": ["Python", "PostgreSQL"],
            "propagate_terms": ["Python", "PostgreSQL", "Docker"],
        },
        threshold=72,
    )
    assert score["overall_score"] < 72
    assert score["passed"] is False
    assert "summary" in score["weak_sections"]
    assert score["dimensions"]["naturalness"] < 70


def test_quality_score_rewards_role_aligned_resume():
    resume = {
        "professional_summary": (
            "Backend developer with hands-on experience building scalable Python services, "
            "cloud-based applications, and relational database solutions. Designed PostgreSQL "
            "schemas supporting request tracking and delivered reliable REST APIs."
        ),
        "skills": [
            "Backend: REST APIs, FastAPI",
            "Languages: Python",
            "Databases: PostgreSQL",
            "Cloud & DevOps: Docker, AWS",
        ],
        "experience": [
            {
                "company": "Acme",
                "title": "Software Engineer",
                "bullets": [
                    "Built REST APIs in Python serving production traffic.",
                    "Designed relational PostgreSQL schemas for request tracking.",
                    "Deployed services on AWS with Docker.",
                ],
            }
        ],
        "projects": [
            {
                "name": "Order Service",
                "description": "Backend service for order intake.",
                "bullets": [
                    "Designed relational PostgreSQL schemas supporting validation and tracking.",
                    "Implemented REST validation endpoints for incoming orders.",
                ],
            }
        ],
    }
    score = evaluate_resume_quality(
        resume,
        strategy={
            "job_family": "backend",
            "skills_to_emphasize": ["Python", "PostgreSQL", "Docker", "REST APIs"],
            "summary_focus": "backend python postgresql docker",
        },
        highlight_plan={
            "must_highlight": ["Python", "PostgreSQL"],
            "propagate_terms": ["Python", "PostgreSQL", "Docker", "REST APIs"],
        },
        threshold=72,
    )
    assert score["overall_score"] >= 72
    assert score["dimensions"]["role_differentiation"] >= 60


def test_writer_feedback_includes_validator_guidance():
    feedback = _compose_writer_feedback(
        review={
            "approved": False,
            "issues": [],
            "summary_feedback": "Summary is generic.",
            "sections_to_regenerate": ["summary"],
        },
        grammar={
            "issues": [
                {
                    "section": "experience",
                    "patterns": ["keyword_stuffing"],
                }
            ]
        },
        style={"weak_dimensions": {"naturalness": 40}},
        ai={"signals": ["summary contains AI cliché"]},
        quality={
            "overall_score": 55,
            "weak_sections": ["summary", "projects"],
            "dimensions": {"naturalness": 40},
        },
    )
    assert feedback["issues"]
    assert feedback["quality_score"]["overall_score"] == 55
    assert "Quality score" in feedback["summary_feedback"]


def test_writing_pipeline_accepts_hm_feedback(monkeypatch):
    resume = {
        "professional_summary": "Backend developer with hands-on experience in Python and PostgreSQL.",
        "skills": ["Languages: Python", "Databases: PostgreSQL"],
        "experience": [
            {
                "company": "Acme",
                "title": "Engineer",
                "bullets": ["Built REST APIs in Python serving production traffic."],
            }
        ],
        "projects": [],
    }

    def _fake_write(**kwargs):
        assert kwargs.get("hiring_manager_feedback") is not None or kwargs.get(
            "review_feedback"
        ) is not None or True
        out = dict(resume)
        if kwargs.get("hiring_manager_feedback"):
            out["professional_summary"] = (
                "Backend developer with hands-on experience building scalable Python services "
                "and relational PostgreSQL solutions for production systems."
            )
        return {
            "tailored_resume": out,
            "mode": "deterministic",
            "writing_notes": [],
            "fact_lock": {"passed": True, "reverted": False, "violations": []},
        }

    monkeypatch.setattr(
        "intelligent_tailoring.writing.writing_pipeline.write_human_resume",
        _fake_write,
    )
    monkeypatch.setattr(
        "intelligent_tailoring.writing.writing_pipeline.review_resume",
        lambda **kwargs: {
            "approved": True,
            "human_believability": 80,
            "interview_quality": 78,
            "issues": [],
            "sections_to_regenerate": [],
            "summary_feedback": "ok",
        },
    )
    result = run_human_writing_stage(
        validated_resume=resume,
        strategy={"job_family": "backend", "skills_to_emphasize": ["Python"]},
        allow_llm=False,
        hiring_manager_feedback={
            "overall_fit": 55,
            "weakest_sections": ["summary"],
            "actionable_feedback": [
                "I still don't understand why this candidate fits."
            ],
        },
        max_review_cycles=1,
    )
    assert "quality_score" in result
    assert "tailored_resume" in result


def test_banned_phrases_cover_requested_examples():
    joined = " | ".join(AI_CLICHE_PHRASES)
    for phrase in (
        "professional with knowledge",
        "strong understanding",
        "passionate about",
        "highly motivated",
    ):
        assert phrase in joined
