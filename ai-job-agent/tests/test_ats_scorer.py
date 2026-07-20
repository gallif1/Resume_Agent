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
        core_professional_domain="Software Development",
        domain_keywords=["backend", "APIs"],
    )
    defaults.update(kwargs)
    return AtsCandidateProfile(**defaults)


def _job_profile(**kwargs) -> JobProfile:
    defaults = dict(
        title="Backend Developer",
        professional_domain="Software Development",
        seniority="junior",
        required_skills=["Python", "AWS"],
        preferred_skills=["Docker"],
        mandatory_requirements=[],
        hard_constraints=[],
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
        seniority="senior",
        required_skills=["Kubernetes", "Terraform"],
        technologies=["Kubernetes", "Terraform"],
    )
    result = score(
        _candidate(experience_years=2.0, skills=["Python"], technologies=["Python"]),
        job,
    )
    assert result.mandatory_failed
    assert result.ats_score <= 49
    assert result.score_label == "Weak Match"
    assert not result.is_potential_junior_match
    assert len(result.missing_mandatory_requirements) > 0


def test_junior_potential_match_skips_hard_cap():
    """Junior with foundational skills + ≤3y job should not be hard-capped to ≤49."""
    job = _job_profile(
        title="Software Engineer",
        seniority="junior",
        years_experience_min=3.0,
        mandatory_requirements=["3+ years experience"],
        required_skills=["Python", "SQL", "AWS"],
        technologies=["Python", "SQL", "AWS"],
        certifications=[],
        languages=["English"],
    )
    candidate = _candidate(
        experience_years=1.5,
        seniority="junior",
        previous_roles=["Junior Software Developer", "Technical Support"],
        skills=["Python", "SQL", "AWS", "English"],
        technologies=["Python", "SQL", "AWS"],
    )
    result = score(candidate, job)
    assert result.mandatory_failed
    assert result.is_potential_junior_match
    assert result.ats_score > 49
    assert result.score_label != "Weak Match"
    fields = result.to_db_fields()
    assert fields["is_potential_junior_match"] == 1
    assert fields["rejection_reason"] is None


def test_junior_potential_label_when_score_below_partial():
    job = _job_profile(
        title="Software Engineer",
        seniority="junior",
        years_experience_min=2.0,
        mandatory_requirements=["2+ years experience"],
        required_skills=["Python", "SQL", "AWS"],
        technologies=["Python", "SQL", "AWS"],
        languages=["English"],
    )
    candidate = _candidate(
        experience_years=0.5,
        seniority="junior",
        previous_roles=["Technical Support"],
        skills=["Python", "SQL", "English"],
        technologies=["Python", "SQL"],
    )
    result = score(candidate, job)
    assert result.is_potential_junior_match
    if result.ats_score < 50:
        assert result.score_label == "Potential Match"


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


def test_junior_underqualified_capped_at_70():
    """0–1y candidate vs 3y+ role cannot score above 70."""
    job = _job_profile(
        title="Backend Developer",
        seniority="mid",
        years_experience_min=3.0,
        mandatory_requirements=[],
        required_skills=["Python", "AWS", "Docker"],
        technologies=["Python", "AWS", "Docker"],
        preferred_skills=[],
        languages=["English"],
    )
    candidate = _candidate(
        experience_years=0.5,
        seniority="junior",
        skills=["Python", "AWS", "Docker", "English"],
        technologies=["Python", "AWS", "Docker"],
        previous_roles=["Backend Developer"],
        projects=["API platform"],
    )
    result = score(candidate, job)
    assert result.ats_score <= 70
    assert any("Early-career" in r for r in result.score_reasons)


def test_dynamic_domain_mismatch_penalized():
    """Marketing JD vs Software-core CV is heavily penalized (no hardcoded industries)."""
    job = _job_profile(
        title="Digital Marketing Manager",
        professional_domain="Digital Marketing",
        required_skills=["SEO", "Google Ads", "Content Strategy", "English"],
        preferred_skills=["Analytics"],
        technologies=[],
        years_experience_min=3.0,
        hard_constraints=[],
        mandatory_requirements=[],
        languages=["English"],
    )
    job_dict = {
        "title": "Digital Marketing Manager",
        "description": "Own SEO, paid media, and content for B2B growth",
    }
    mismatched = _candidate(
        skills=["Python", "AWS", "Docker", "English", "Communication"],
        technologies=["Python", "AWS", "Docker"],
        previous_roles=["Backend Developer", "Software Engineer"],
        projects=["API platform"],
        experience_years=4.0,
        seniority="mid",
        core_professional_domain="Software Development",
        domain_keywords=["backend", "APIs", "cloud"],
    )
    aligned = _candidate(
        skills=["SEO", "Google Ads", "Content Strategy", "English", "Analytics"],
        technologies=[],
        previous_roles=["Digital Marketing Specialist", "Content Marketer"],
        projects=["B2B SEO campaign"],
        experience_years=4.0,
        seniority="mid",
        core_professional_domain="Digital Marketing",
        domain_keywords=["SEO", "paid media", "content"],
    )
    mismatch_result = score(mismatched, job, job_dict)
    aligned_result = score(aligned, job, job_dict)
    assert mismatch_result.domain_mismatch
    assert mismatch_result.ats_score <= 40
    assert aligned_result.ats_score - mismatch_result.ats_score >= 20
    assert any("domain mismatch" in r.lower() for r in mismatch_result.score_reasons)


def test_hard_constraint_failure_caps_at_30():
    """Unmet critical must-have caps score at 30 despite soft-skill overlap."""
    job = _job_profile(
        title="SOC Analyst",
        professional_domain="Cybersecurity Operations",
        required_skills=["SIEM", "English", "Communication"],
        preferred_skills=["Python"],
        technologies=["SIEM"],
        years_experience_min=2.0,
        certifications=["CompTIA Security+"],
        hard_constraints=[
            "Must have CompTIA Security+ certification",
            "SOC shift work experience required",
        ],
        mandatory_requirements=["2+ years experience"],
        languages=["English"],
        seniority="junior",
    )
    candidate = _candidate(
        skills=["Python", "English", "Communication", "Teamwork"],
        technologies=["Python"],
        previous_roles=["IT Helpdesk"],
        projects=["Home lab monitoring"],
        experience_years=2.5,
        seniority="junior",
        core_professional_domain="IT Support",
        domain_keywords=["helpdesk", "support"],
        certifications=[],
    )
    result = score(candidate, job)
    assert result.hard_constraint_failed
    assert result.ats_score <= 30
    assert not result.is_potential_junior_match
    assert any("Hard constraints unmet" in r for r in result.score_reasons)


def test_job_match_system_uses_strict_rubric():
    from job_matcher import JOB_MATCH_SYSTEM

    assert "industry-agnostic AI Career Agent" in JOB_MATCH_SYSTEM
    assert "DYNAMIC DOMAIN ALIGNMENT" in JOB_MATCH_SYSTEM
    assert "HARD CONSTRAINTS" in JOB_MATCH_SYSTEM
    assert "capped at 30" in JOB_MATCH_SYSTEM
    assert "POTENTIAL AND CAPABILITY" in JOB_MATCH_SYSTEM
    assert "Employment History Bias" in JOB_MATCH_SYSTEM
    assert "PROJECT-TO-EXPERIENCE TRANSLATION" in JOB_MATCH_SYSTEM
    assert "Technical Recruiter" not in JOB_MATCH_SYSTEM
    assert "tech company" not in JOB_MATCH_SYSTEM.lower()
    # No hardcoded industry taxonomy in the matching prompt.
    assert "WEB_ECOMMERCE" not in JOB_MATCH_SYSTEM
    assert "Shopify" not in JOB_MATCH_SYSTEM
