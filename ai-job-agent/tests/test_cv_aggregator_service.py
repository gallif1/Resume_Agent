"""Tests for multi-CV aggregation into a Master Candidate Profile."""

from __future__ import annotations

from cv_aggregator_service import (
    MasterCandidateProfile,
    PersonalInfo,
    aggregate_cv_texts,
    master_profile_to_cv_profile,
)


def test_rule_based_aggregate_single_cv():
    text = "Python developer with FastAPI and PostgreSQL experience."
    master = aggregate_cv_texts([text], use_ai=False)
    assert master.source_cv_count == 1
    assert master.aggregated_with == "rules"
    assert "Python" in str(master.master_skills)


def test_rule_based_aggregate_multiple_without_ai():
    texts = [
        "Backend engineer: Python, Django, REST APIs.",
        "QA engineer: Selenium, pytest, test automation.",
    ]
    master = aggregate_cv_texts(texts, use_ai=False)
    assert master.source_cv_count == 2
    cv_profile = master_profile_to_cv_profile(master)
    assert cv_profile["master_profile"]["source_cv_count"] == 2
    assert isinstance(cv_profile["skills"], dict)


def test_master_profile_to_cv_profile_maps_contact_and_experience():
    master = MasterCandidateProfile(
        personal_info=PersonalInfo(
            name="Jane Doe",
            email="jane@example.com",
            location="Tel Aviv",
        ),
        unified_summary="Full-stack engineer with QA and backend experience.",
        master_skills={"programming_languages": ["Python", "JavaScript"]},
        work_experience=[
            {
                "title": "Backend Developer",
                "company": "Acme",
                "start_date": "2020",
                "end_date": "2023",
                "description": "Built APIs",
                "bullet_points": ["Designed REST services", "Owned PostgreSQL schema"],
            }
        ],
        projects=[{"name": "Portfolio", "description": "Personal site", "technologies": ["React"]}],
        education=[{"degree": "BSc CS", "institution": "TAU", "field": "CS", "year": "2019"}],
        languages=["Hebrew", "English"],
        source_cv_count=2,
    )
    cv_profile = master_profile_to_cv_profile(master)
    assert cv_profile["contact"]["name"] == "Jane Doe"
    assert cv_profile["contact"]["email"] == "jane@example.com"
    assert "Backend Developer" in cv_profile["experience"]["job_titles"]
    assert "Acme" in cv_profile["experience"]["companies"]
    assert any("Portfolio" in p for p in cv_profile["projects"])
    assert "BSc CS" in cv_profile["education"]["degrees"]
