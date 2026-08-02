"""Integration tests for Intelligent Resume Tailoring across professions.

LLM stages are stubbed with profession-specific payloads so we can assert
truthfulness, differentiation, change_log categorization, and low-evidence
warnings without live OpenAI calls.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from intelligent_tailoring.pipeline import run_intelligent_tailoring
from intelligent_tailoring.schemas import validate_tailoring_result

SOFTWARE_RESUME = {
    "contact": {"name": "Alex Dev", "email": "alex@example.com"},
    "raw_text": (
        "Alex Dev — Backend Engineer at Acme (2021-2025). Built REST APIs in "
        "Python/FastAPI, stored data in PostgreSQL, deployed services to AWS EC2, "
        "wrote automation scripts, and documented runbooks. Project: Campus Scheduler."
    ),
    "skills": {"programming_languages": ["Python"], "databases": ["PostgreSQL"]},
    "experience": {
        "job_titles": ["Backend Engineer"],
        "years_of_experience_estimate": 4,
        "roles": [
            {
                "company": "Acme",
                "title": "Backend Engineer",
                "dates": "2021-2025",
                "bullets": [
                    "Built REST APIs in Python/FastAPI",
                    "Deployed services to AWS EC2",
                    "Stored data in PostgreSQL",
                ],
            }
        ],
    },
    "projects": [
        {
            "name": "Campus Scheduler",
            "description": "Scheduling tool",
            "bullets": ["Built scheduling UI"],
        }
    ],
}

OPS_RESUME = {
    "contact": {"name": "Sam Ops", "email": "sam@example.com"},
    "raw_text": (
        "Sam Ops — Operations Coordinator at RetailCo (2019-2024). Managed staff "
        "schedules, handled customer complaints, prepared Excel reports, managed stock "
        "levels, prepared invoices, and trained new employees."
    ),
    "skills": {"tools": ["Excel"], "soft_skills": ["customer service"]},
    "experience": {
        "job_titles": ["Operations Coordinator"],
        "years_of_experience_estimate": 5,
        "roles": [
            {
                "company": "RetailCo",
                "title": "Operations Coordinator",
                "dates": "2019-2024",
                "bullets": [
                    "Managed staff schedules",
                    "Handled customer complaints",
                    "Prepared Excel reports",
                    "Managed stock levels",
                    "Prepared invoices",
                    "Trained new employees",
                ],
            }
        ],
    },
}

SOFTWARE_JD = {
    "id": 1,
    "title": "Backend Engineer",
    "company": "CloudCo",
    "full_description": (
        "Required: Python, REST APIs, PostgreSQL, AWS. Preferred: Docker, CI/CD. "
        "Responsibilities: design backend services, deploy to cloud, write docs."
    ),
}

SALES_JD = {
    "id": 2,
    "title": "Sales Operations Specialist",
    "company": "SellWell",
    "full_description": (
        "Required: CRM hygiene, Excel reporting, workforce coordination, inventory "
        "awareness, billing operations, customer conflict resolution. Preferred: "
        "onboarding experience. Responsibilities: coordinate schedules, prepare "
        "reports, resolve complaints, manage stock data."
    ),
}


def _writer_passthrough_from(responses: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> dict[str, Any]:
    for r in reversed(list(responses)):
        if isinstance(r, dict) and "tailored_resume" in r:
            return {
                "tailored_resume": r["tailored_resume"],
                "writing_notes": ["test_stub_passthrough"],
                "sections_rewritten": ["summary", "experience", "projects"],
            }
    return {
        "tailored_resume": {
            "professional_title": "",
            "professional_summary": "Professional with relevant experience.",
            "skills": [],
            "experience": [],
            "projects": [],
            "education": [],
            "certifications": [],
        },
        "writing_notes": ["test_stub_empty"],
        "sections_rewritten": ["summary"],
    }


def _recruiter_approve() -> dict[str, Any]:
    return {
        "approved": True,
        "human_believability": 88,
        "interview_quality": 86,
        "issues": [],
        "sections_to_regenerate": [],
        "summary_feedback": "Reads as a professionally written human resume.",
    }


def _stage_sequence(*responses: dict[str, Any]):
    """Return a side_effect that yields each response then replays generation-shaped ones."""
    queue = list(responses)

    def _call(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        namespace = str(_kwargs.get("cache_namespace") or "")
        if "human_writer" in namespace:
            return _writer_passthrough_from(responses)
        if "recruiter_review" in namespace:
            return _recruiter_approve()
        if queue:
            return queue.pop(0)
        for r in reversed(responses):
            if isinstance(r, dict) and "tailored_resume" in r:
                return r
        return responses[-1] if responses else {}

    return _call


def _software_stage_responses() -> list[dict[str, Any]]:
    requirements = {
        "required_skills": ["Python", "REST APIs", "PostgreSQL", "AWS"],
        "preferred_skills": ["Docker", "CI/CD"],
        "responsibilities": ["design backend services", "deploy to cloud"],
        "tools_technologies": ["Python", "PostgreSQL", "AWS"],
        "industry_terminology": ["backend", "API"],
        "seniority_level": "mid",
        "soft_skills": ["documentation"],
        "education_certifications": [],
        "ats_keywords": ["Python", "REST", "PostgreSQL", "AWS"],
        "hard_requirements": ["Python", "REST APIs", "PostgreSQL", "AWS"],
        "soft_requirements": ["Docker", "CI/CD"],
        "language": "en",
    }
    inference = {
        "inferred_competencies": [
            {
                "statement": "Experience building and integrating HTTP-based backend APIs",
                "supporting_evidence": "Built REST APIs in Python/FastAPI",
                "reasoning": "REST API work strongly implies backend/API design",
                "confidence_score": 0.9,
                "related_requirement": "REST APIs",
                "ontology_rule_id": "rest-backend",
                "inference_category": "Strongly Inferred",
            }
        ]
    }
    triage = {
        "triage": [
            {
                "element_type": "experience_bullet",
                "original_text": "Built REST APIs in Python/FastAPI",
                "action": "Expand",
                "reason": "Core hard requirement",
                "related_job_requirement": "REST APIs",
            }
        ],
        "section_order": [
            "professional_summary",
            "skills",
            "experience",
            "projects",
            "education",
        ],
    }
    generation = {
        "tailored_resume": {
            "professional_title": "Backend Engineer",
            "professional_summary": (
                "Backend engineer with Python/FastAPI, PostgreSQL, and AWS EC2 deployment experience."
            ),
            "skills": [
                "Languages: Python",
                "Databases: PostgreSQL",
                "Cloud & DevOps: AWS",
            ],
            "experience": [
                {
                    "company": "Acme",
                    "title": "Backend Engineer",
                    "dates": "2021-2025",
                    "bullets": [
                        "Built REST APIs in Python/FastAPI for production services",
                        "Deployed services to AWS EC2",
                        "Stored application data in PostgreSQL",
                    ],
                }
            ],
            "projects": [
                {
                    "name": "Campus Scheduler",
                    "description": "Scheduling tool",
                    "bullets": ["Built scheduling UI"],
                }
            ],
            "education": [],
            "certifications": [],
        },
        "change_log": [
            {
                "original_text": "Built REST APIs in Python/FastAPI",
                "new_text": "Built REST APIs in Python/FastAPI for production services",
                "reason": "Expanded with production context already implied by role",
                "supporting_evidence": "Built REST APIs in Python/FastAPI",
                "related_job_requirement": "REST APIs",
                "inference_category": "Explicit",
                "confidence_score": 1.0,
            },
            {
                "original_text": "",
                "new_text": "Experience building and integrating HTTP-based backend APIs",
                "reason": "Ontology-backed competency from REST API evidence",
                "supporting_evidence": "Built REST APIs in Python/FastAPI",
                "related_job_requirement": "REST APIs",
                "inference_category": "Strongly Inferred",
                "confidence_score": 0.9,
            },
        ],
        "matched_requirements": ["Python", "REST APIs", "PostgreSQL", "AWS"],
        "missing_requirements": [],
        "removed_or_deprioritized_content": [],
        "ats_keywords_added": ["REST", "AWS"],
    }
    claim_llm = {"validation_warnings": []}
    return [requirements, inference, triage, generation, claim_llm]


def _sales_stage_responses(*, include_unsupported: bool = False) -> list[dict[str, Any]]:
    requirements = {
        "required_skills": [
            "Excel reporting",
            "workforce coordination",
            "inventory management",
            "conflict resolution",
            "billing",
        ],
        "preferred_skills": ["onboarding"],
        "responsibilities": [
            "coordinate schedules",
            "prepare reports",
            "resolve complaints",
        ],
        "tools_technologies": ["Excel"],
        "industry_terminology": ["operations", "sales ops"],
        "seniority_level": "mid",
        "soft_skills": ["customer service"],
        "education_certifications": [],
        "ats_keywords": ["Excel", "scheduling", "inventory", "billing"],
        "hard_requirements": [
            "Excel reporting",
            "workforce coordination",
            "inventory management",
            "conflict resolution",
        ],
        "soft_requirements": ["onboarding"],
        "language": "en",
    }
    inference = {
        "inferred_competencies": [
            {
                "statement": "Experience coordinating workforce schedules and staffing",
                "supporting_evidence": "Managed staff schedules",
                "reasoning": "Ontology scheduling-workforce",
                "confidence_score": 0.88,
                "related_requirement": "workforce coordination",
                "ontology_rule_id": "scheduling-workforce",
                "inference_category": "Strongly Inferred",
            },
            {
                "statement": "Experience with inventory management",
                "supporting_evidence": "Managed stock levels",
                "reasoning": "Ontology stock-inventory",
                "confidence_score": 0.9,
                "related_requirement": "inventory management",
                "ontology_rule_id": "stock-inventory",
                "inference_category": "Strongly Inferred",
            },
        ]
    }
    triage = {
        "triage": [
            {
                "element_type": "experience_bullet",
                "original_text": "Managed staff schedules",
                "action": "Rewrite",
                "reason": "Align with workforce coordination wording",
                "related_job_requirement": "workforce coordination",
            }
        ],
        "section_order": ["professional_summary", "skills", "experience"],
    }
    bullets = [
        "Coordinated workforce schedules for retail staff",
        "Resolved customer complaints with clear follow-up",
        "Prepared Excel reports for operations leadership",
        "Managed stock levels and inventory counts",
        "Prepared invoices for billing operations",
        "Trained new employees during onboarding",
    ]
    if include_unsupported:
        bullets.append(
            "Owned enterprise Salesforce CRM architecture across 12 regions"
        )
    generation = {
        "tailored_resume": {
            "professional_title": "Operations Coordinator",
            "professional_summary": (
                "Operations coordinator experienced in workforce scheduling, "
                "Excel reporting, inventory management, and customer conflict resolution."
            ),
            "skills": [
                "Excel",
                "workforce coordination",
                "inventory management",
                "customer service",
            ],
            "experience": [
                {
                    "company": "RetailCo",
                    "title": "Operations Coordinator",
                    "dates": "2019-2024",
                    "bullets": bullets,
                }
            ],
            "projects": [],
            "education": [],
            "certifications": [],
        },
        "change_log": [
            {
                "original_text": "Managed staff schedules",
                "new_text": "Coordinated workforce schedules for retail staff",
                "reason": "JD terminology for workforce coordination",
                "supporting_evidence": "Managed staff schedules",
                "related_job_requirement": "workforce coordination",
                "inference_category": "Strongly Inferred",
                "confidence_score": 0.88,
            },
            {
                "original_text": "Prepared Excel reports",
                "new_text": "Prepared Excel reports for operations leadership",
                "reason": "Emphasized reporting relevance",
                "supporting_evidence": "Prepared Excel reports",
                "related_job_requirement": "Excel reporting",
                "inference_category": "Explicit",
                "confidence_score": 1.0,
            },
        ],
        "matched_requirements": [
            "Excel reporting",
            "workforce coordination",
            "inventory management",
            "conflict resolution",
        ],
        "missing_requirements": [],
        "removed_or_deprioritized_content": [],
        "ats_keywords_added": ["workforce coordination", "inventory management"],
    }
    claim_llm = {
        "validation_warnings": (
            [
                {
                    "statement": (
                        "Owned enterprise Salesforce CRM architecture across 12 regions"
                    ),
                    "reason": "No Salesforce evidence on resume",
                    "inference_category": "Unsupported",
                }
            ]
            if include_unsupported
            else []
        )
    }
    return [requirements, inference, triage, generation, claim_llm]


@pytest.fixture(autouse=True)
def _ai_available(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "intelligent_tailoring.pipeline.is_ai_available", lambda: True
    )


def test_software_and_ops_tailoring_differ_and_stay_truthful(monkeypatch: pytest.MonkeyPatch):
    # Software path
    with patch(
        "intelligent_tailoring.llm_utils.call_openai_json",
        side_effect=_stage_sequence(*_software_stage_responses()),
    ):
        software = run_intelligent_tailoring(
            cv_profile=SOFTWARE_RESUME,
            job=SOFTWARE_JD,
            use_cache=False,
        )

    # Sales/ops path — same candidate family, different profession JD + resume
    with patch(
        "intelligent_tailoring.llm_utils.call_openai_json",
        side_effect=_stage_sequence(*_sales_stage_responses()),
    ):
        sales = run_intelligent_tailoring(
            cv_profile=OPS_RESUME,
            job=SALES_JD,
            use_cache=False,
        )

    validate_tailoring_result(software)
    validate_tailoring_result(sales)

    # (a) no fabricated employers/tech
    soft_blob = json.dumps(software["tailored_resume"], ensure_ascii=False)
    sales_blob = json.dumps(sales["tailored_resume"], ensure_ascii=False)
    assert "Salesforce" not in soft_blob
    assert "Acme" in soft_blob
    assert "RetailCo" in sales_blob
    assert "FakeCorp" not in sales_blob

    # (b) meaningfully different emphasis
    soft_skills = " ".join(software["tailored_resume"]["skills"]).lower()
    sales_skills = " ".join(sales["tailored_resume"]["skills"]).lower()
    assert "python" in soft_skills or "postgresql" in soft_skills
    assert "excel" in sales_skills or "inventory" in sales_skills or "workforce" in sales_skills
    assert soft_skills != sales_skills

    soft_bullets = " ".join(
        b
        for e in software["tailored_resume"]["experience"]
        for b in e.get("bullets") or []
    ).lower()
    sales_bullets = " ".join(
        b
        for e in sales["tailored_resume"]["experience"]
        for b in e.get("bullets") or []
    ).lower()
    assert "rest" in soft_bullets or "api" in soft_bullets
    assert "schedule" in sales_bullets or "complaint" in sales_bullets or "inventory" in sales_bullets
    assert soft_bullets != sales_bullets

    # (c) change_log categories valid
    for item in software["change_log"] + sales["change_log"]:
        assert item["inference_category"] in (
            "Explicit",
            "Strongly Inferred",
            "Weakly Inferred",
            "Unsupported",
        )
        if item["inference_category"] == "Strongly Inferred":
            assert item["supporting_evidence"]
            assert item["reason"]

    # Scores present and reproducible fields
    assert isinstance(software["original_match_score"], int)
    assert isinstance(software["tailored_match_score"], int)
    assert software["claim_validator_passed"] is True
    assert sales["claim_validator_passed"] is True


def test_low_evidence_triggers_validation_warnings(monkeypatch: pytest.MonkeyPatch):
    with patch(
        "intelligent_tailoring.llm_utils.call_openai_json",
        side_effect=_stage_sequence(
            *_sales_stage_responses(include_unsupported=True)
        ),
    ):
        result = run_intelligent_tailoring(
            cv_profile=OPS_RESUME,
            job=SALES_JD,
            use_cache=False,
        )

    blob = json.dumps(result["tailored_resume"], ensure_ascii=False)
    assert "Salesforce" not in blob
    assert result["validation_warnings"], "expected validation warnings for low-evidence claim"
    assert any(
        "Salesforce" in str(w.get("statement") or w)
        for w in result["validation_warnings"]
    )


def test_pipeline_cache_reuses_result(monkeypatch: pytest.MonkeyPatch, tmp_path):
    import intelligent_tailoring.cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path / "it_cache")
    calls = {"n": 0}

    def counting_call(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        namespace = str(kwargs.get("cache_namespace") or "")
        seq = _software_stage_responses()
        if "human_writer" in namespace:
            return _writer_passthrough_from(seq)
        if "recruiter_review" in namespace:
            return _recruiter_approve()
        # Map by call count within a pipeline run (core LLM stages)
        core_calls = calls["n"]
        # Count only non-writing namespaces toward the core sequence index
        idx = (core_calls - 1) % len(seq)
        return seq[idx]

    with patch(
        "intelligent_tailoring.llm_utils.call_openai_json",
        side_effect=counting_call,
    ):
        first = run_intelligent_tailoring(
            cv_profile=SOFTWARE_RESUME, job=SOFTWARE_JD, use_cache=True
        )
        first_calls = calls["n"]
        second = run_intelligent_tailoring(
            cv_profile=SOFTWARE_RESUME, job=SOFTWARE_JD, use_cache=True
        )

    assert first_calls >= 4  # multiple stage calls on first run
    assert second.get("from_cache") is True
    assert calls["n"] == first_calls  # no additional LLM calls
    assert second["tailored_match_score"] == first["tailored_match_score"]


def test_no_generation_path_skips_claim_validator():
    """Guard: pipeline always invokes claim validation + merged writing agent."""
    import inspect
    import intelligent_tailoring.pipeline as pipeline_mod

    # Production entry delegates to the four-agent implementation.
    entry = inspect.getsource(pipeline_mod.run_intelligent_tailoring)
    assert "run_intelligent_tailoring_agents" in entry

    source = inspect.getsource(pipeline_mod.run_intelligent_tailoring_agents)
    assert "ClaimValidationAgent" in source or "run_claim_validation" in source
    assert "claim_validator_passed" in source
    assert "run_merged_writing_review" in source
    assert "writing_report" in source
    assert (
        "single_agent" in source
        or "resume_generation_agent" in source
        or "four_agent" in source
        or "candidate_opportunity_intelligence" in source
    )
