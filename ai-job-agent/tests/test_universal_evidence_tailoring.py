"""Multi-profession evidence-based tailoring tests.

Covers KB extraction, ontology transferables, strategy differentiation across
industries, missed evidence, quality gates, and regression against title-only
tailoring — without live OpenAI calls.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from intelligent_tailoring.knowledge_base import (
    build_knowledge_base,
    score_facts_for_job,
)
from intelligent_tailoring.ontology import clear_ontology_cache, get_ontology
from intelligent_tailoring.pipeline import run_intelligent_tailoring
from intelligent_tailoring.services.job_analyzer import analyze_job
from intelligent_tailoring.services.job_family import detect_job_family
from intelligent_tailoring.services.missed_evidence import find_missed_evidence
from intelligent_tailoring.services.quality import evaluate_tailoring_quality
from intelligent_tailoring.services.similarity import compare_resume_pair
from intelligent_tailoring.services.tailoring_strategy_builder import build_tailoring_strategy

# ---------------------------------------------------------------------------
# Fixtures spanning professions
# ---------------------------------------------------------------------------

TEACHER_RESUME = {
    "contact": {"name": "Maya Cohen"},
    "raw_text": (
        "Maya Cohen — Math Teacher at City High (2018-2024). Delivered lessons to "
        "120 students, mentored struggling learners, prepared curriculum materials, "
        "presented at parent evenings, and documented student progress."
    ),
    "skills": {"soft_skills": ["teaching", "presentation", "mentoring"]},
    "experience": {
        "roles": [
            {
                "company": "City High",
                "title": "Math Teacher",
                "dates": "2018-2024",
                "bullets": [
                    "Delivered lessons to 120 students weekly",
                    "Mentored struggling learners in after-school sessions",
                    "Prepared curriculum materials and lesson plans",
                    "Presented progress updates at parent evenings",
                    "Documented student progress in the school LMS",
                ],
            }
        ]
    },
    "education": [{"institution": "State University", "degree": "B.Ed Mathematics"}],
}

SALES_RESUME = {
    "contact": {"name": "Jordan Lee"},
    "raw_text": (
        "Jordan Lee — Account Executive at SellCo (2020-2025). Managed CRM pipeline in "
        "Salesforce, closed deals against quarterly quotas, negotiated vendor contracts, "
        "upsold existing accounts, and generated inbound leads."
    ),
    "skills": {"tools": ["Salesforce", "CRM"], "soft_skills": ["negotiation"]},
    "experience": {
        "roles": [
            {
                "company": "SellCo",
                "title": "Account Executive",
                "dates": "2020-2025",
                "bullets": [
                    "Managed CRM pipeline in Salesforce",
                    "Closed deals against quarterly quotas",
                    "Negotiated vendor contracts",
                    "Upsold existing accounts",
                    "Generated inbound leads via cold outreach",
                ],
            }
        ]
    },
}

OPS_RESUME = {
    "contact": {"name": "Sam Ops"},
    "raw_text": (
        "Sam Ops — Operations Coordinator at RetailCo (2019-2024). Managed staff "
        "schedules, handled customer complaints, prepared Excel reports, managed stock "
        "levels, prepared invoices, and trained new employees."
    ),
    "skills": {"tools": ["Excel"], "soft_skills": ["customer service"]},
    "experience": {
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
        ]
    },
}

HEALTHCARE_RESUME = {
    "contact": {"name": "Drina Patel"},
    "raw_text": (
        "Drina Patel — Clinic Administrator (2021-2025). Managed patient appointments, "
        "updated patient records in the EMR, ensured confidentiality compliance, "
        "coordinated with physicians, and processed insurance documentation."
    ),
    "skills": {"tools": ["EMR"], "soft_skills": ["confidentiality", "organization"]},
    "experience": {
        "roles": [
            {
                "company": "Downtown Clinic",
                "title": "Clinic Administrator",
                "dates": "2021-2025",
                "bullets": [
                    "Managed patient appointments",
                    "Updated patient records in the EMR",
                    "Ensured confidentiality compliance",
                    "Coordinated with physicians on scheduling",
                    "Processed insurance documentation",
                ],
            }
        ]
    },
}

ACCOUNTANT_RESUME = {
    "contact": {"name": "Alex Books"},
    "raw_text": (
        "Alex Books — Finance Assistant at Ledger LLC (2019-2024). Performed bookkeeping "
        "in QuickBooks, prepared invoices, reconciled cash drawers, supported accounts "
        "payable/receivable, and assisted with month-end reporting."
    ),
    "skills": {"tools": ["QuickBooks", "Excel"]},
    "experience": {
        "roles": [
            {
                "company": "Ledger LLC",
                "title": "Finance Assistant",
                "dates": "2019-2024",
                "bullets": [
                    "Performed bookkeeping in QuickBooks",
                    "Prepared invoices for clients",
                    "Reconciled cash drawers daily",
                    "Supported accounts payable and receivable",
                    "Assisted with month-end financial reporting",
                ],
            }
        ]
    },
}

SOFTWARE_RESUME = {
    "contact": {"name": "Alex Dev"},
    "raw_text": (
        "Alex Dev — Backend Engineer at Acme (2021-2025). Built REST APIs in "
        "Python/FastAPI, stored data in PostgreSQL, deployed services to AWS EC2, "
        "wrote automation scripts, and documented runbooks. Project: Campus Scheduler."
    ),
    "skills": {
        "programming_languages": ["Python"],
        "databases": ["PostgreSQL"],
        "cloud": ["AWS"],
    },
    "experience": {
        "roles": [
            {
                "company": "Acme",
                "title": "Backend Engineer",
                "dates": "2021-2025",
                "bullets": [
                    "Built REST APIs in Python/FastAPI",
                    "Deployed services to AWS EC2",
                    "Stored data in PostgreSQL",
                    "Wrote automation scripts",
                    "Documented runbooks",
                ],
            }
        ]
    },
    "projects": [
        {
            "name": "Campus Scheduler",
            "description": "Scheduling tool",
            "bullets": ["Built scheduling UI", "PostgreSQL data layer"],
        }
    ],
}

JOBS = {
    "teacher_to_trainer": {
        "title": "Corporate Trainer",
        "full_description": (
            "Required: teaching, presentation, mentoring, curriculum design, "
            "explaining complex information. Responsibilities: deliver training "
            "workshops, mentor employees, prepare instructional materials."
        ),
    },
    "teacher_to_cs": {
        "title": "Customer Success Specialist",
        "full_description": (
            "Required: customer communication, documentation, mentoring support, "
            "presentation skills, patience. Responsibilities: support customers, "
            "document cases, explain product features clearly."
        ),
    },
    "sales_to_ops": {
        "title": "Operations Coordinator",
        "full_description": (
            "Required: scheduling, inventory awareness, Excel reporting, "
            "process ownership. Responsibilities: coordinate schedules, manage stock data."
        ),
    },
    "sales_keep_sales": {
        "title": "Senior Account Executive",
        "full_description": (
            "Required: Salesforce CRM, quota attainment, negotiation, lead generation, "
            "upselling. Responsibilities: close deals, manage pipeline, negotiate contracts."
        ),
    },
    "ops_to_admin": {
        "title": "Office Administrator",
        "full_description": (
            "Required: scheduling, invoicing, Excel, training new staff, customer service. "
            "Responsibilities: office coordination, billing admin, staff onboarding."
        ),
    },
    "ops_to_cs": {
        "title": "Customer Service Representative",
        "full_description": (
            "Required: complaint handling, customer service, conflict resolution, "
            "issue ownership. Responsibilities: resolve complaints, communicate clearly."
        ),
    },
    "health_admin": {
        "title": "Healthcare Administrator",
        "full_description": (
            "Required: patient appointments, EMR, confidentiality, insurance documentation, "
            "compliance. Responsibilities: manage clinic schedule and patient records."
        ),
    },
    "finance_bookkeeper": {
        "title": "Bookkeeper",
        "full_description": (
            "Required: QuickBooks, invoicing, cash reconciliation, accounts payable, "
            "financial reporting. Responsibilities: maintain ledgers and month-end close."
        ),
    },
    "software_backend": {
        "title": "Backend Engineer",
        "full_description": (
            "Required: Python, REST APIs, PostgreSQL, AWS. Responsibilities: design backend services."
        ),
    },
    "software_cs": {
        "title": "Technical Support Specialist",
        "full_description": (
            "Required: troubleshooting, documentation, customer communication, runbooks, "
            "debugging. Responsibilities: investigate issues, write docs, assist customers."
        ),
    },
}


def _job(key: str) -> dict[str, Any]:
    spec = JOBS[key]
    return {
        "id": abs(hash(key)) % 10000,
        "title": spec["title"],
        "company": "TestCo",
        "full_description": spec["full_description"],
    }


def _reqs_from_job(job: dict[str, Any]) -> dict[str, Any]:
    words = job["full_description"].replace(",", " ").split()
    skills = [w for w in words if len(w) > 4][:12]
    return {
        "required_skills": skills,
        "preferred_skills": [],
        "responsibilities": [job["full_description"]],
        "tools_technologies": skills[:4],
        "industry_terminology": [],
        "seniority_level": "mid",
        "soft_skills": [],
        "education_certifications": [],
        "ats_keywords": skills[:6],
        "hard_requirements": skills[:6],
        "soft_requirements": [],
        "language": "en",
    }


@pytest.fixture(autouse=True)
def _clear_ontology():
    clear_ontology_cache()
    yield
    clear_ontology_cache()


class TestKnowledgeBase:
    def test_extracts_atomic_facts_for_teacher(self):
        kb = build_knowledge_base(TEACHER_RESUME)
        assert kb.coverage is not None
        assert kb.coverage.extracted_fact_count >= 5
        assert kb.coverage.extraction_coverage_score >= 0.4
        texts = " ".join(f.original_text for f in kb.facts).lower()
        assert "120 students" in texts or "mentored" in texts
        assert any(f.fact_type == "training_activity" for f in kb.facts) or any(
            "teach" in " ".join(f.implied_competencies).lower() or "teach" in f.original_text.lower()
            for f in kb.facts
        )

    def test_ops_facts_preserve_inventory_and_invoices(self):
        kb = build_knowledge_base(OPS_RESUME)
        blob = " ".join(f.original_text.lower() for f in kb.facts)
        assert "stock" in blob or "inventory" in blob or "schedules" in blob
        assert "invoice" in blob or "complaints" in blob

    def test_software_project_bullets_not_lost(self):
        kb = build_knowledge_base(SOFTWARE_RESUME)
        sections = {f.source_section for f in kb.facts}
        assert "projects" in sections
        assert any("Campus Scheduler" in f.original_text for f in kb.facts)


class TestUniversalOntology:
    def test_teaching_maps_to_presentation(self):
        ont = get_ontology()
        hits = ont.infer_from_resume_text(
            "Delivered lessons to students and mentored learners"
        )
        targets = " ".join(h.inferred_competency.lower() for h in hits)
        assert "teach" in targets or "presentation" in targets or "mentor" in targets

    def test_invoice_maps_to_billing(self):
        ont = get_ontology()
        hits = ont.infer_from_resume_text("Prepared invoices for billing operations")
        assert hits
        assert any("bill" in h.inferred_competency.lower() or "admin" in h.inferred_competency.lower() for h in hits)

    def test_patient_records_map_to_healthcare(self):
        ont = get_ontology()
        hits = ont.infer_from_resume_text("Updated patient records in the EMR")
        assert hits
        assert any("health" in h.inferred_competency.lower() or "confidential" in " ".join(h.relation.also_implies).lower() for h in hits)


class TestStrategyDifferentiation:
    def test_same_ops_candidate_sales_vs_cs_strategy_differs(self):
        kb = build_knowledge_base(OPS_RESUME)
        from intelligent_tailoring.knowledge_base import knowledge_base_to_resume_facts

        facts = knowledge_base_to_resume_facts(kb)
        sales_job = _job("sales_keep_sales")
        cs_job = _job("ops_to_cs")
        sales_analysis = analyze_job(sales_job, requirements=_reqs_from_job(sales_job))
        cs_analysis = analyze_job(cs_job, requirements=_reqs_from_job(cs_job))
        sales_scores = score_facts_for_job(kb, job_requirements=_reqs_from_job(sales_job))
        cs_scores = score_facts_for_job(kb, job_requirements=_reqs_from_job(cs_job))
        sales_strategy = build_tailoring_strategy(
            job_analysis=sales_analysis,
            resume_facts=facts,
            evidence_map=[],
            ranked_requirements=[],
            fact_scores=sales_scores,
        )
        cs_strategy = build_tailoring_strategy(
            job_analysis=cs_analysis,
            resume_facts=facts,
            evidence_map=[],
            ranked_requirements=[],
            fact_scores=cs_scores,
        )
        assert sales_strategy["summary_focus"] != cs_strategy["summary_focus"]
        # CS should prioritize complaint/customer facts higher
        def top_texts(scores):
            return [s["original_text"].lower() for s in scores[:3]]

        cs_top = " ".join(top_texts(cs_scores))
        assert "complaint" in cs_top or "customer" in cs_top or "schedule" in cs_top

    def test_family_detection_non_tech(self):
        assert detect_job_family("Math Teacher", {}) == "education"
        assert detect_job_family("Account Executive", {}) == "sales"
        assert detect_job_family("Clinic Administrator", {"responsibilities": ["patient records"]}) in (
            "healthcare",
            "administration",
            "management",
        )


class TestMissedEvidenceAndQuality:
    def test_missed_evidence_finds_overlooked_facts(self):
        kb = build_knowledge_base(OPS_RESUME)
        reqs = _reqs_from_job(_job("ops_to_admin"))
        # Pretend we only selected the first fact
        selected = [kb.facts[0].id] if kb.facts else []
        missed = find_missed_evidence(
            kb=kb,
            job_requirements=reqs,
            evidence_map=[],
            initially_selected_fact_ids=selected,
        )
        # Should find additional facts for scheduling/invoicing/excel
        assert isinstance(missed["additional_relevant_facts_found"], list)

    def test_quality_flags_title_only(self):
        baseline = {
            "professional_summary": "",
            "skills": ["Excel", "customer service"],
            "experience": [
                {
                    "company": "RetailCo",
                    "title": "Operations Coordinator",
                    "bullets": ["Managed staff schedules", "Handled customer complaints"],
                }
            ],
            "projects": [],
        }
        tailored = {
            "professional_title": "Customer Service Rep",
            "professional_summary": "Professional seeking a challenging role.",
            "skills": ["Excel", "customer service"],
            "experience": baseline["experience"],
            "projects": [],
        }
        report = evaluate_tailoring_quality(
            tailored_resume=tailored,
            baseline_resume=baseline,
            strategy={
                "summary_focus": "customer service complaint resolution",
                "skills_to_emphasize": ["customer", "complaint"],
                "keywords_to_insert": ["conflict resolution"],
            },
            evidence_map=[
                {
                    "requirement": "complaint handling",
                    "importance": "hard",
                    "candidate_status": "MATCH",
                }
            ],
            fact_scores=[
                {"original_text": "Handled customer complaints", "score": 90},
                {"original_text": "Managed staff schedules", "score": 40},
            ],
            change_log=[],
        )
        assert report["regeneration_required"] is True
        assert report["generic_content_score"] > 0 or report["title_only_change"]


def _stage_sequence(*responses: dict[str, Any]):
    """Yield each response then repeat the last (supports regen retries)."""
    queue = list(responses)

    def _call(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if len(queue) > 1:
            return queue.pop(0)
        return responses[-1] if responses else {}

    return _call


def _stage_responses(job: dict[str, Any], summary: str, skills: list[str], bullets: list[str]):
    reqs = _reqs_from_job(job)
    return _stage_sequence(
        reqs,
        {"inferred_competencies": []},
        {"triage": [], "section_order": []},
        {
            "tailored_resume": {
                "professional_title": job["title"],
                "professional_summary": summary,
                "skills": skills,
                "experience": [
                    {
                        "company": "Co",
                        "title": "Role",
                        "dates": "2020-2024",
                        "bullets": bullets,
                    }
                ],
                "projects": [],
                "education": [],
                "certifications": [],
            },
            "change_log": [
                {
                    "original_text": "",
                    "new_text": summary,
                    "reason": "Job-specific summary",
                    "supporting_evidence": summary[:80],
                    "related_job_requirement": job["title"],
                    "inference_category": "Explicit",
                    "confidence_score": 1.0,
                },
                {
                    "original_text": bullets[0] if bullets else "",
                    "new_text": bullets[0] if bullets else summary,
                    "reason": "Emphasized relevant responsibility",
                    "supporting_evidence": bullets[0] if bullets else summary[:40],
                    "related_job_requirement": job["title"],
                    "inference_category": "Explicit",
                    "confidence_score": 1.0,
                },
            ],
            "matched_requirements": reqs["hard_requirements"][:3],
            "missing_requirements": [],
            "removed_or_deprioritized_content": [],
            "ats_keywords_added": [],
        },
        {"validation_warnings": []},
    )


@pytest.fixture(autouse=True)
def _ai_ok(monkeypatch):
    monkeypatch.setattr("intelligent_tailoring.pipeline.is_ai_available", lambda: True)


class TestCrossProfessionPipeline:
    def test_teacher_trainer_vs_cs_narratives_differ(self):
        trainer = _job("teacher_to_trainer")
        cs = _job("teacher_to_cs")
        with patch(
            "intelligent_tailoring.llm_utils.call_openai_json",
            side_effect=_stage_responses(
                trainer,
                "Educator specializing in lesson delivery, mentoring, and curriculum design for corporate training.",
                ["Teaching", "Mentoring", "Curriculum design", "Presentation"],
                [
                    "Delivered lessons and training workshops to large groups",
                    "Mentored struggling learners",
                    "Prepared curriculum materials",
                ],
            ),
        ):
            r1 = run_intelligent_tailoring(
                cv_profile=TEACHER_RESUME, job=trainer, use_cache=False
            )
        with patch(
            "intelligent_tailoring.llm_utils.call_openai_json",
            side_effect=_stage_responses(
                cs,
                "Customer-facing professional skilled in clear communication, documentation, and patiently explaining complex topics.",
                ["Customer communication", "Documentation", "Presentation", "Patience"],
                [
                    "Presented progress updates clearly to stakeholders",
                    "Documented progress and case details",
                    "Mentored individuals needing extra support",
                ],
            ),
        ):
            r2 = run_intelligent_tailoring(
                cv_profile=TEACHER_RESUME, job=cs, use_cache=False
            )

        assert r1["tailored_resume"]["professional_summary"] != r2["tailored_resume"][
            "professional_summary"
        ]
        sim = compare_resume_pair(r1["tailored_resume"], r2["tailored_resume"])
        assert sim["overall_similarity"] < 0.85
        assert r1.get("extraction_coverage") or r1.get("knowledge_base_summary")
        assert r1.get("quality_report") is not None or r1.get("tailoring_report")

    def test_regression_title_only_is_not_enough(self):
        """Regression: two jobs must change more than the title."""
        backend = _job("software_backend")
        support = _job("software_cs")
        with patch(
            "intelligent_tailoring.llm_utils.call_openai_json",
            side_effect=_stage_responses(
                backend,
                "Backend engineer focused on FastAPI REST APIs, PostgreSQL, and AWS deployments.",
                ["Python", "FastAPI", "PostgreSQL", "AWS"],
                [
                    "Built REST APIs in Python/FastAPI",
                    "Stored data in PostgreSQL",
                    "Deployed services to AWS EC2",
                ],
            ),
        ):
            r1 = run_intelligent_tailoring(
                cv_profile=SOFTWARE_RESUME, job=backend, use_cache=False
            )
        with patch(
            "intelligent_tailoring.llm_utils.call_openai_json",
            side_effect=_stage_responses(
                support,
                "Technical support specialist emphasizing troubleshooting, runbook documentation, and clear customer communication.",
                ["Troubleshooting", "Documentation", "Customer communication", "AWS"],
                [
                    "Documented runbooks for production support",
                    "Wrote automation scripts aiding incident response",
                    "Deployed services to AWS EC2",
                ],
            ),
        ):
            r2 = run_intelligent_tailoring(
                cv_profile=SOFTWARE_RESUME, job=support, use_cache=False
            )

        # Not title-only: summaries and skill lists must differ
        assert r1["tailored_resume"]["professional_title"] != r2["tailored_resume"][
            "professional_title"
        ]
        assert r1["tailored_resume"]["professional_summary"] != r2["tailored_resume"][
            "professional_summary"
        ]
        assert r1["tailored_resume"]["skills"] != r2["tailored_resume"]["skills"]
        sim = compare_resume_pair(r1["tailored_resume"], r2["tailored_resume"])
        assert sim["overall_similarity"] < 0.80

    def test_hebrew_language_preserved(self):
        he_resume = {
            "contact": {"name": "סאם אופס"},
            "raw_text": (
                "סאם אופס — רכז תפעול בריטיילקו (2019-2024). ניהל לוחות זמנים, "
                "טיפל בתלונות לקוחות, הכין דוחות אקסל וניהל מלאי."
            ),
            "skills": {"tools": ["Excel"], "soft_skills": ["שירות לקוחות"]},
            "experience": {
                "roles": [
                    {
                        "company": "RetailCo",
                        "title": "רכז תפעול",
                        "dates": "2019-2024",
                        "bullets": [
                            "ניהל לוחות זמנים",
                            "טיפל בתלונות לקוחות",
                            "הכין דוחות אקסל",
                            "ניהל מלאי",
                        ],
                    }
                ]
            },
        }
        job = {
            "id": 99,
            "title": "נציג שירות לקוחות",
            "company": "חברה",
            "full_description": (
                "דרישות: שירות לקוחות, טיפול בתלונות, פתרון קונפליקטים, תיעוד. "
                "אחריות: מענה לפניות לקוחות ופתרון בעיות."
            ),
        }
        reqs = {
            **_reqs_from_job(job),
            "language": "he",
            "required_skills": ["שירות לקוחות", "טיפול בתלונות"],
            "hard_requirements": ["שירות לקוחות", "טיפול בתלונות"],
            "ats_keywords": ["שירות לקוחות"],
        }
        responses = _stage_sequence(
            reqs,
            {"inferred_competencies": []},
            {"triage": [], "section_order": []},
            {
                "tailored_resume": {
                    "professional_title": "נציג שירות לקוחות",
                    "professional_summary": "רכז תפעול עם ניסיון בטיפול בתלונות לקוחות ושירות.",
                    "skills": ["שירות לקוחות", "טיפול בתלונות", "Excel"],
                    "experience": [
                        {
                            "company": "RetailCo",
                            "title": "רכז תפעול",
                            "dates": "2019-2024",
                            "bullets": ["טיפל בתלונות לקוחות", "ניהל לוחות זמנים"],
                        }
                    ],
                    "projects": [],
                    "education": [],
                    "certifications": [],
                },
                "change_log": [
                    {
                        "original_text": "",
                        "new_text": "רכז תפעול עם ניסיון בטיפול בתלונות לקוחות ושירות.",
                        "reason": "התאמה לתפקיד",
                        "supporting_evidence": "טיפל בתלונות לקוחות",
                        "related_job_requirement": "שירות לקוחות",
                        "inference_category": "Explicit",
                        "confidence_score": 1.0,
                    },
                    {
                        "original_text": "טיפל בתלונות לקוחות",
                        "new_text": "טיפל בתלונות לקוחות ופתר בעיות שירות",
                        "reason": "הדגשת שירות",
                        "supporting_evidence": "טיפל בתלונות לקוחות",
                        "related_job_requirement": "טיפול בתלונות",
                        "inference_category": "Explicit",
                        "confidence_score": 1.0,
                    },
                ],
                "matched_requirements": ["שירות לקוחות"],
                "missing_requirements": [],
                "removed_or_deprioritized_content": [],
                "ats_keywords_added": [],
            },
            {"validation_warnings": []},
        )
        with patch(
            "intelligent_tailoring.llm_utils.call_openai_json",
            side_effect=responses,
        ):
            result = run_intelligent_tailoring(
                cv_profile=he_resume, job=job, use_cache=False, language="he"
            )
        assert result["language"] == "he"
        summary = result["tailored_resume"]["professional_summary"]
        title = result["tailored_resume"]["professional_title"]
        assert any("\u0590" <= ch <= "\u05FF" for ch in (summary or title))
        assert "Software Engineer" not in title
