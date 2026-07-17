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


def test_web_vs_mobile_domain_penalty():
    """Web/eCommerce JD vs Mobile-heavy CV drops score by at least 20 vs aligned CV."""
    job = _job_profile(
        title="Frontend Web Developer",
        required_skills=["React", "TypeScript", "CSS"],
        preferred_skills=["Responsive Design", "eCommerce"],
        technologies=["React", "TypeScript", "CSS"],
        years_experience_min=2.0,
    )
    job_dict = {
        "title": "Frontend Web Developer",
        "description": "Build responsive eCommerce storefronts and web apps",
        "full_description": "Shopify / WooCommerce experience preferred. Frontend web team.",
    }
    mobile_cv = _candidate(
        skills=["React Native", "Flutter", "Swift", "Kotlin", "Expo", "English"],
        technologies=["React Native", "Flutter", "Swift", "Kotlin", "Expo"],
        previous_roles=["Mobile Developer", "iOS Engineer"],
        projects=["Android shopping app", "React Native mobile client"],
        experience_years=3.0,
        seniority="mid",
    )
    web_cv = _candidate(
        skills=["React", "TypeScript", "CSS", "HTML", "Next.js", "English"],
        technologies=["React", "TypeScript", "CSS", "HTML", "Next.js"],
        previous_roles=["Frontend Web Developer"],
        projects=["Responsive eCommerce website"],
        experience_years=3.0,
        seniority="mid",
    )
    mobile_score = score(mobile_cv, job, job_dict).ats_score
    web_score = score(web_cv, job, job_dict).ats_score
    assert web_score - mobile_score >= 20
    mobile_result = score(mobile_cv, job, job_dict)
    assert any("Domain misalignment" in r for r in mobile_result.score_reasons)


def test_missing_critical_keywords_penalized():
    job = _job_profile(
        title="Frontend Engineer",
        required_skills=["React", "TypeScript"],
        preferred_skills=["Responsive Design"],
        technologies=["React", "TypeScript"],
    )
    job_dict = {
        "title": "Frontend Engineer",
        "description": "Must have Responsive Design and eCommerce experience",
    }
    weak = _candidate(
        skills=["React", "TypeScript", "English"],
        technologies=["React", "TypeScript"],
        previous_roles=["Developer"],
        projects=["Internal tools"],
        experience_years=3.0,
    )
    strong = _candidate(
        skills=["React", "TypeScript", "English"],
        technologies=["React", "TypeScript"],
        previous_roles=["Frontend Developer"],
        projects=["Responsive Design eCommerce storefront"],
        experience_years=3.0,
    )
    weak_result = score(weak, job, job_dict)
    strong_result = score(strong, job, job_dict)
    assert weak_result.ats_score < strong_result.ats_score
    assert any("Critical JD keywords" in r for r in weak_result.score_reasons)


def test_job_match_system_uses_strict_rubric():
    from job_matcher import JOB_MATCH_SYSTEM

    assert "highly cynical Technical Recruiter" in JOB_MATCH_SYSTEM
    assert "Do NOT give points for 'potential'" in JOB_MATCH_SYSTEM
    assert "drop the score by at least 20 points" in JOB_MATCH_SYSTEM
    assert "cannot score above 70%" in JOB_MATCH_SYSTEM
