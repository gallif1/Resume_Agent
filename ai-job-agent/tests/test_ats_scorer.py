"""Tests for the deterministic ATS scoring engine."""

from __future__ import annotations

from ats_candidate import AtsCandidateProfile, build_ats_candidate
from ats_scorer import score, score_label_for
from job_analyzer import JobProfile


def _candidate(**kwargs) -> AtsCandidateProfile:
    defaults = dict(
        skills=["Python", "AWS", "Docker", "English"],
        technologies=["Python", "AWS", "Docker"],
        experience_years=3.0,
        previous_roles=["Backend Developer"],
        projects=["API platform"],
        education=["BSc Computer Science"],
        languages=["English", "Hebrew"],
        certifications=[],
        seniority="junior",
    )
    defaults.update(kwargs)
    return AtsCandidateProfile(**defaults)


def _job_profile(**kwargs) -> JobProfile:
    defaults = dict(
        title="Backend Developer",
        seniority="junior",
        required_skills=["Python", "AWS"],
        preferred_skills=["Docker"],
        mandatory_requirements=[],
        years_experience_min=2.0,
        languages=["English"],
        certifications=[],
        technologies=["Python", "AWS"],
    )
    defaults.update(kwargs)
    return JobProfile(**defaults)


def test_excellent_match_high_score():
    result = score(_candidate(), _job_profile())
    assert result.ats_score >= 70
    assert result.score_label in ("Excellent Match", "Good Match")
    assert "Python" in result.matched_required_skills


def test_missing_mandatory_caps_score():
    job = _job_profile(
        mandatory_requirements=["5+ years experience", "CISSP certification"],
        years_experience_min=5.0,
        certifications=["CISSP"],
    )
    result = score(_candidate(experience_years=2.0), job)
    assert result.mandatory_failed
    assert result.ats_score <= 49
    assert result.score_label == "Weak Match"
    assert len(result.missing_mandatory_requirements) > 0


def test_missing_required_skills_reduces_score():
    job = _job_profile(
        required_skills=["Python", "Kubernetes", "Terraform", "Go"],
        technologies=["Python", "Kubernetes", "Terraform", "Go"],
    )
    result = score(_candidate(), job)
    assert "Kubernetes" in result.missing_required_skills
    assert result.ats_score < 85


def test_score_labels():
    assert score_label_for(90) == "Excellent Match"
    assert score_label_for(75) == "Good Match"
    assert score_label_for(55) == "Partial Match"
    assert score_label_for(30) == "Weak Match"


def test_cv_improvements_generated():
    job = _job_profile(required_skills=["Python", "Rust", "Scala"])
    result = score(_candidate(), job)
    assert any("Rust" in imp or "skill" in imp.lower() for imp in result.cv_improvements)


def test_build_ats_candidate_from_cv_profile():
    cv_profile = {
        "skills": {
            "programming_languages": ["Python"],
            "cloud_devops_tools": ["AWS"],
            "languages": ["English"],
        },
        "experience": {
            "job_titles": ["Developer"],
            "years_of_experience_estimate": 2,
            "seniority_level": "junior",
        },
        "education": {"degrees": ["BSc"], "fields_of_study": [], "institutions": []},
        "projects": ["Web app"],
        "certifications": [],
    }
    candidate = build_ats_candidate(cv_profile)
    assert "Python" in candidate.skills
    assert candidate.experience_years == 2.0
    assert candidate.seniority == "junior"


def test_to_db_fields_includes_ats_columns():
    result = score(_candidate(), _job_profile())
    fields = result.to_db_fields(strategy_hash="abc")
    assert fields["match_method"] == "ats"
    assert fields["match_score"] == result.ats_score
    assert fields["ats_score_label"] == result.score_label
    assert fields["ats_reasons"]
