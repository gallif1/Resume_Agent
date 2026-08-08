"""Structured output schema + deterministic validation layer regressions.

Covers the consolidated fullness / preservation / raw-data / contact / summary
requirements across strong-match, weak-match (TypeScript), and fullstack JDs.
"""

from __future__ import annotations

from copy import deepcopy

from intelligent_tailoring.stages.resume_extraction import extract_structured_resume
from intelligent_tailoring.structured_resume import (
    assign_stable_ids,
    base_source_ids,
    count_content_units,
    stamp_ids_on_resume,
    structured_to_pipeline_resume,
    to_structured_resume,
    validate_structured_schema,
)
from intelligent_tailoring.structured_validation import (
    repair_structured_resume,
    validate_structured_resume,
)
from intelligent_tailoring.services.resume_rebuilder import rebuild_resume_structure


def _gal_base_profile() -> dict:
    return {
        "contact": {
            "name": "Gal Lifshitz",
            "email": "gal@example.com",
            "phone": "+972-50-000-0000",
            "linkedin": "https://linkedin.com/in/gallifshitz",
            "github": "https://github.com/gallif",
            "location": "Israel",
        },
        "skills": {
            "languages": ["Python", "JavaScript", "SQL", "Go"],
            "frameworks": ["FastAPI", "React", "Node.js"],
            "other": ["pytest", "WebSockets", "AWS", "Git"],
        },
        "experience": {
            "job_titles": ["Capstone Project Lead", "Python Programming Tutor"],
            "companies": ["SCE", "Tel Hai University"],
        },
        "projects": [
            "REST API Development: Built FastAPI services with SQLAlchemy. [FastAPI, pytest]",
            "Server Monitor System: Concurrent health checks with ThreadPoolExecutor. [Python, AWS]",
        ],
        "sections": {
            "experience": (
                "Capstone Project Lead @ SCE (2024 – 2025)\n"
                "• Implemented automated testing using pytest including integration "
                "tests and reusable testing utilities.\n"
                "• Built React and Angular views for core user workflows.\n"
                "• Coordinated frontend/backend integration and debugging.\n\n"
                "Python Programming Tutor @ Tel Hai University (2022 – 2023)\n"
                "• Tutored algorithms, data structures, and debugging techniques.\n"
                "• Helped students implement Python assignments."
            ),
        },
        "master_profile": {
            "work_experience": [
                {
                    "title": "Capstone Project Lead",
                    "company": "SCE",
                    "start_date": "2024",
                    "end_date": "2025",
                    "bullet_points": [
                        "Implemented automated testing using pytest including "
                        "integration tests and reusable testing utilities.",
                        "Built React and Angular views for core user workflows.",
                        "Coordinated frontend/backend integration and debugging.",
                    ],
                },
                {
                    "title": "Python Programming Tutor",
                    "company": "Tel Hai University",
                    "start_date": "2022",
                    "end_date": "2023",
                    "bullet_points": [
                        "Tutored algorithms, data structures, and debugging techniques.",
                        "Helped students implement Python assignments.",
                    ],
                },
            ],
            "projects": [
                {
                    "name": "REST API Development",
                    "description": "Built FastAPI services with SQLAlchemy.",
                    "bullet_points": [
                        "Implemented REST endpoints with FastAPI and SQLAlchemy.",
                        "Added pytest coverage for integration paths.",
                    ],
                    "technologies": ["FastAPI", "SQLAlchemy", "pytest"],
                },
                {
                    "name": "Server Monitor System",
                    "description": "Concurrent health checks with ThreadPoolExecutor.",
                    "bullet_points": [
                        "Built concurrent health checks using ThreadPoolExecutor.",
                        "Deployed monitoring workers on AWS.",
                    ],
                    "technologies": ["Python", "AWS"],
                },
            ],
            "education": [
                {
                    "degree": "B.Sc",
                    "institution": "SCE",
                    "field_of_study": "Software Engineering",
                    "dates": "2021 – 2025",
                }
            ],
        },
    }


def _strong_backend_strategy() -> dict:
    return {
        "job_family": "backend",
        "job_title": "Backend Engineer",
        "primary_role": "Backend Engineer",
        "skill_category_order": [
            "Languages",
            "Backend",
            "Databases",
            "Cloud",
            "Testing",
            "Other",
        ],
        "must_highlight_in_summary": ["Python", "FastAPI", "REST APIs"],
        "shared_technologies": ["Python", "FastAPI", "pytest", "AWS"],
    }


def _weak_typescript_strategy() -> dict:
    return {
        "job_family": "fullstack",
        "job_title": "Full Stack Engineer",
        "primary_role": "Full Stack Engineer",
        "seniority": "3+ years",
        "skill_category_order": ["Languages", "Frontend", "Backend", "Other"],
        "must_highlight_in_summary": ["TypeScript", "NestJS"],
        "shared_technologies": ["JavaScript", "Node.js", "React"],
        "genuine_gaps": ["TypeScript", "NestJS"],
    }


def _fullstack_data_strategy() -> dict:
    return {
        "job_family": "fullstack",
        "job_title": "Full Stack / Data Engineer",
        "primary_role": "Full Stack Engineer",
        "skill_category_order": [
            "Languages",
            "Backend",
            "Frontend",
            "Data",
            "Other",
        ],
        "must_highlight_in_summary": ["Python", "SQL", "APIs"],
        "shared_technologies": ["Python", "SQL", "React", "AWS"],
    }


def test_extraction_assigns_stable_ids_and_contact():
    facts = extract_structured_resume(_gal_base_profile())
    ids = base_source_ids(facts)
    assert ids["experience_ids"] == {"role_0", "role_1"}
    assert ids["project_ids"] == {"project_0", "project_1"}
    assert facts["contact"]["github"] == "https://github.com/gallif"
    assert facts["contact"]["linkedin"] == "https://linkedin.com/in/gallifshitz"
    for role in facts["experience_roles"]:
        assert role["id"] == role["source_entry_id"]
        assert role["id"].startswith("role_")


def test_rebuilder_preserves_stable_ids():
    facts = extract_structured_resume(_gal_base_profile())
    rebuilt = rebuild_resume_structure(
        resume_facts=facts,
        scores={},
        strategy=_strong_backend_strategy(),
    )
    exp_ids = {e["id"] for e in rebuilt["experience"]}
    proj_ids = {p["id"] for p in rebuilt["projects"]}
    assert "role_0" in exp_ids and "role_1" in exp_ids
    assert "project_0" in proj_ids and "project_1" in proj_ids
    assert rebuilt["contact"]["email"] == "gal@example.com"


def test_structured_schema_roundtrip():
    facts = extract_structured_resume(_gal_base_profile())
    rebuilt = rebuild_resume_structure(
        resume_facts=facts, scores={}, strategy=_strong_backend_strategy()
    )
    rebuilt["professional_summary"] = (
        "Backend-focused software engineer with experience building production "
        "REST APIs, automated tests, and cloud-hosted services."
    )
    structured = to_structured_resume(rebuilt, source_facts=facts)
    validate_structured_schema(structured)
    assert structured["experience"][0]["id"] in {"role_0", "role_1"}
    assert "position" in structured["experience"][0]
    assert isinstance(structured["skills"], dict)
    pipeline = structured_to_pipeline_resume(structured)
    assert pipeline["experience"][0]["title"]
    assert pipeline["contact"]["github"]


def test_validation_rejects_missing_experience_id():
    facts = extract_structured_resume(_gal_base_profile())
    rebuilt = rebuild_resume_structure(
        resume_facts=facts, scores={}, strategy=_strong_backend_strategy()
    )
    rebuilt["professional_summary"] = (
        "Software engineer experienced with Python services, APIs, and tutoring."
    )
    # Drop one experience entry
    rebuilt["experience"] = [rebuilt["experience"][0]]
    report = validate_structured_resume(
        rebuilt, source_facts=facts, enforce_fullness=False, require_summary=True
    )
    assert not report.passed
    assert "missing_experience_id" in report.error_codes()
    assert "role_1" in report.feedback_for_agent()


def test_validation_rejects_raw_data_and_duplicate_entries():
    facts = extract_structured_resume(_gal_base_profile())
    rebuilt = rebuild_resume_structure(
        resume_facts=facts, scores={}, strategy=_strong_backend_strategy()
    )
    rebuilt["professional_summary"] = (
        "Software engineer with backend and tutoring experience across APIs."
    )
    # Inject raw structure into a bullet (must never reach rendered text)
    rebuilt["experience"][0]["bullets"] = list(rebuilt["experience"][0]["bullets"]) + [
        "{'degrees': ['B.Sc'], 'institutions': ['SCE'], 'fields_of_study': ['CS']}"
    ]
    # Duplicate experience
    rebuilt["experience"] = rebuilt["experience"] + [deepcopy(rebuilt["experience"][0])]
    report = validate_structured_resume(
        rebuilt, source_facts=facts, enforce_fullness=False, require_summary=False
    )
    codes = set(report.error_codes())
    assert "raw_data_in_string" in codes or "schema_invalid" in codes
    assert "duplicate_experience_entry" in codes or "duplicate_experience_id" in codes


def test_validation_rejects_near_duplicate_bullets_and_broken_summary():
    facts = extract_structured_resume(_gal_base_profile())
    rebuilt = rebuild_resume_structure(
        resume_facts=facts, scores={}, strategy=_strong_backend_strategy()
    )
    rebuilt["professional_summary"] = (
        "Frontend Engineer Frontend Developer building interfaces and APIs."
    )
    rebuilt["experience"][0]["bullets"] = [
        "Built React and Angular views for core user workflows.",
        "Built React and Angular views for core user workflows and dashboards.",
    ]
    report = validate_structured_resume(
        rebuilt, source_facts=facts, enforce_fullness=False, require_summary=True
    )
    codes = set(report.error_codes())
    assert "near_duplicate_bullet" in codes
    assert "summary_competing_lead_ins" in codes


def test_validation_rejects_missing_contact_links():
    facts = extract_structured_resume(_gal_base_profile())
    rebuilt = rebuild_resume_structure(
        resume_facts=facts, scores={}, strategy=_strong_backend_strategy()
    )
    rebuilt["professional_summary"] = (
        "Software engineer with experience in Python APIs and student mentoring."
    )
    rebuilt["contact"] = {"email": "gal@example.com"}  # drop github/linkedin
    report = validate_structured_resume(
        rebuilt, source_facts=facts, enforce_fullness=False, require_summary=True
    )
    assert "missing_contact_field" in report.error_codes()
    assert "github" in report.feedback_for_agent()
    assert "linkedin" in report.feedback_for_agent()


def test_validation_rejects_sparse_content_volume():
    facts = extract_structured_resume(_gal_base_profile())
    base_units = count_content_units(
        {
            "experience": facts["experience_roles"],
            "projects": facts["projects"],
        }
    )["total_units"]
    assert base_units >= 5

    sparse = {
        "contact": dict(facts["contact"]),
        "professional_title": "Backend Engineer",
        "professional_summary": (
            "Software engineer with Python experience building services and mentoring."
        ),
        "skills": ["Languages: Python"],
        "experience": [
            {
                "id": "role_0",
                "title": "Capstone Project Lead",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": ["Built React views."],
            },
            {
                "id": "role_1",
                "title": "Python Programming Tutor",
                "company": "Tel Hai University",
                "dates": "2022 – 2023",
                "bullets": ["Tutored algorithms."],
            },
        ],
        "projects": [
            {
                "id": "project_0",
                "name": "REST API Development",
                "description": "Built FastAPI services.",
                "bullets": [],
            },
            {
                "id": "project_1",
                "name": "Server Monitor System",
                "description": "Health checks.",
                "bullets": [],
            },
        ],
        "education": [],
        "certifications": [],
    }
    report = validate_structured_resume(
        sparse, source_facts=facts, enforce_fullness=True, require_summary=True
    )
    assert "content_volume_too_low" in report.error_codes()


def _assert_full_resume_invariants(resume: dict, facts: dict) -> None:
    report = validate_structured_resume(
        resume,
        source_facts=facts,
        enforce_fullness=True,
        require_summary=True,
    )
    assert report.passed, report.feedback_for_agent()
    ids = base_source_ids(facts)
    present_exp = {e.get("id") for e in resume.get("experience") or []}
    present_proj = {p.get("id") for p in resume.get("projects") or []}
    assert ids["experience_ids"] <= present_exp
    assert ids["project_ids"] <= present_proj
    contact = resume.get("contact") or {}
    assert contact.get("github")
    assert contact.get("linkedin")
    assert contact.get("email")


def test_repair_restores_invariants_for_strong_weak_and_fullstack():
    facts = extract_structured_resume(_gal_base_profile())
    strategies = (
        _strong_backend_strategy(),
        _weak_typescript_strategy(),
        _fullstack_data_strategy(),
    )
    full_summary = (
        "Backend-oriented software engineer with experience building production "
        "REST APIs, automated tests, and mentoring students in Python fundamentals."
    )
    for strategy in strategies:
        rebuilt = rebuild_resume_structure(
            resume_facts=facts, scores={}, strategy=strategy
        )
        # Simulate a broken Agent-2 output: drop a project, strip links, thin bullets
        broken = deepcopy(rebuilt)
        broken["projects"] = broken["projects"][:1]
        broken["contact"] = {"email": "gal@example.com"}
        broken["experience"] = [
            {**broken["experience"][0], "bullets": broken["experience"][0]["bullets"][:1]},
            {**broken["experience"][1], "bullets": broken["experience"][1]["bullets"][:1]},
        ]
        broken["professional_summary"] = full_summary
        broken["summary"] = full_summary
        repaired = repair_structured_resume(broken, source_facts=facts)
        if len(str(repaired.get("professional_summary") or "").split()) < 12:
            repaired["professional_summary"] = full_summary
            repaired["summary"] = full_summary
        repaired = stamp_ids_on_resume(repaired, source_facts=facts)
        _assert_full_resume_invariants(repaired, facts)


def test_assign_stable_ids_is_idempotent():
    facts = extract_structured_resume(_gal_base_profile())
    again = assign_stable_ids(facts)
    assert [r["id"] for r in facts["experience_roles"]] == [
        r["id"] for r in again["experience_roles"]
    ]
    assert [p["id"] for p in facts["projects"]] == [
        p["id"] for p in again["projects"]
    ]
