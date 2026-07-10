"""Integration tests for ATS match pipeline (isolated, no production DB)."""

from __future__ import annotations

import json

import db
from ats_candidate import build_ats_candidate
from ats_scorer import score
from conftest import insert_job
from job_analyzer import analyze_job_fallback
from match_jobs import _ensure_job_profile


def _cv_profile() -> dict:
    return {
        "skills": {
            "programming_languages": ["Python"],
            "cloud_devops_tools": ["AWS", "Docker"],
            "languages": ["English"],
        },
        "experience": {
            "job_titles": ["Backend Developer"],
            "years_of_experience_estimate": 3,
            "seniority_level": "junior",
        },
        "education": {"degrees": ["BSc"], "fields_of_study": [], "institutions": []},
        "projects": [],
        "certifications": [],
    }


def test_ensure_job_profile_and_ats_score(db_path, monkeypatch):
    """Job analysis + scoring stores structured profile and produces a score (no AI)."""
    monkeypatch.setattr(db, "DB_PATH", db_path)

    job_id = insert_job(
        db_path,
        title="Python Developer",
        url="https://example.com/job/ats-1",
        company="TechCo",
    )
    db.update_full_description(
        job_id,
        "Junior Python developer with 2+ years. Must know Python and AWS. English required.",
        db_path=db_path,
    )
    job = db.get_all_jobs(db_path=db_path)[0]

    job_profile = _ensure_job_profile(job, use_ai=False)
    assert job_profile.analyzed_with == "rules"
    assert "Python" in job_profile.required_skills or "Python" in job_profile.technologies

    candidate = build_ats_candidate(_cv_profile())
    result = score(candidate, job_profile, job)
    assert result.ats_score > 0
    assert result.score_label in (
        "Excellent Match", "Good Match", "Partial Match", "Weak Match"
    )

    fields = result.to_db_fields(strategy_hash="test-hash")
    db.update_match_result(job_id, fields, db_path=db_path)

    updated = db.get_all_jobs(db_path=db_path)[0]
    assert updated["match_method"] == "ats"
    assert updated["match_score"] == result.ats_score
    assert updated.get("is_analyzed") == 1
    stored = json.loads(updated["job_profile"])
    assert stored["title"] == "Python Developer"


def test_ensure_job_profile_never_uses_ai_by_default(db_path, monkeypatch):
    """_ensure_job_profile uses rule-based extraction only."""
    monkeypatch.setattr(db, "DB_PATH", db_path)

    job_id = insert_job(
        db_path,
        title="Data Analyst",
        url="https://example.com/job/no-ai",
        company="DataCo",
    )
    job = db.get_all_jobs(db_path=db_path)[0]
    job_profile = _ensure_job_profile(job)
    assert job_profile.analyzed_with == "rules"


def test_combined_db_fields_serializes_lists(db_path, monkeypatch):
    """Combined match fields must JSON-serialize lists for SQLite binding."""
    from ats_scorer import score as ats_score
    from match_jobs import _combined_db_fields
    from profile_matcher import score as profile_score

    monkeypatch.setattr(db, "DB_PATH", db_path)

    cv_profile = _cv_profile()
    cv_profile["universal_profile"] = {
        "preferred_role_titles": ["Backend Developer"],
        "canonical_skills": ["Python", "AWS"],
        "search_keywords_en": ["python"],
        "search_keywords_he": [],
        "exclusion_keywords": [],
        "seniority_level": "junior",
        "location_preferences": {"preferred_locations": ["Israel"], "remote_ok": True},
    }

    job_id = insert_job(
        db_path,
        title="Python Developer",
        url="https://example.com/job/combined",
        company="TechCo",
    )
    db.update_full_description(
        job_id,
        "Junior Python developer. Python and AWS required.",
        db_path=db_path,
    )
    job = db.get_all_jobs(db_path=db_path)[0]
    job_profile = _ensure_job_profile(job, use_ai=False)
    candidate = build_ats_candidate(cv_profile)

    pm = profile_score(cv_profile["universal_profile"], job, job_profile)
    ats = ats_score(candidate, job_profile, job)
    fields = _combined_db_fields(
        final_score=70,
        score_label="Good Match",
        profile_result=pm,
        ats_result=ats,
        strategy_hash="test-hash",
        fallback_score=50,
    )

    for key in ("matched_keywords", "missing_keywords", "ai_strengths", "ai_missing_skills"):
        assert isinstance(fields[key], str), f"{key} must be JSON string, got {type(fields[key])}"

    db.upsert_cv_job_match("test-cv", job_id, fields, db_path=db_path)
    row = db.get_cv_job_match("test-cv", job_id, db_path=db_path)
    assert row is not None
    assert row["match_method"] == "profile_ats"


def test_analyze_job_fallback_integration():
    """Rule-based job analysis extracts skills from posting text."""
    job = {
        "title": "SOC Analyst",
        "description": "",
        "full_description": "SOC analyst with Splunk. 3+ years. English required.",
    }
    profile = analyze_job_fallback(job)
    assert profile.years_experience_min == 3.0
    assert "English" in profile.languages
