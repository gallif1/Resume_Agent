"""Tests for universal_profile module."""

from __future__ import annotations

from universal_profile import (
    apply_universal_profile_to_cv,
    build_matching_strategy_from_profile,
    build_universal_profile_fallback,
    normalize_universal_profile,
)


def test_normalize_keeps_all_preferred_role_titles():
    many = [f"Role {i}" for i in range(1, 25)]
    profile = normalize_universal_profile({
        "preferred_role_titles": many,
        "alternative_role_titles": ["Adjacent A", "Adjacent B"],
        "seniority_level": "mid",
    })
    assert len(profile["preferred_role_titles"]) == 24
    assert profile["preferred_role_titles"][0] == "Role 1"
    assert profile["preferred_role_titles"][-1] == "Role 24"
    assert "Adjacent A" in profile["alternative_role_titles"]


def test_build_universal_profile_fallback():
    rule_based = {
        "best_fit_roles": ["Accountant", "Bookkeeper"],
        "experience": {
            "job_titles": ["Junior Accountant"],
            "seniority_level": "junior",
            "years_of_experience_estimate": 2,
        },
        "education": {"degrees": ["B.A."], "fields_of_study": ["Accounting"], "institutions": []},
        "certifications": [],
        "skills": {"finance_accounting": ["Excel", "SAP"]},
        "contact": {"location": "Tel Aviv"},
    }
    profile = build_universal_profile_fallback(rule_based)
    assert profile["source"] == "rules_fallback"
    assert profile["preferred_role_titles"]
    assert profile["collection_queries"]
    assert profile["seniority_level"] == "junior"


def test_apply_universal_profile_to_cv():
    cv = {"experience": {}, "ai_insights": {}, "best_fit_roles": []}
    universal = normalize_universal_profile({
        "preferred_role_titles": ["Nurse"],
        "alternative_role_titles": ["ICU Nurse"],
        "seniority_level": "mid",
        "years_of_experience": 5,
        "candidate_summary": "Experienced nurse.",
        "canonical_skills": ["patient care"],
        "search_keywords_en": ["nurse"],
        "search_keywords_he": ["אחות"],
    })
    merged = apply_universal_profile_to_cv(cv, universal)
    assert merged["universal_profile"]["preferred_role_titles"] == ["Nurse"]
    assert "Nurse" in merged["best_fit_roles"]
    assert merged["experience"]["seniority_level"] == "mid"
    assert merged["ai_insights"]["professional_summary"] == "Experienced nurse."


def test_apply_universal_profile_keeps_rule_based_secondary_tracks():
    """AI preferred titles must not erase other rule-based / past-title tracks."""
    cv = {
        "experience": {"job_titles": ["Technical Support Specialist"]},
        "ai_insights": {},
        "best_fit_roles": ["IT Support", "SOC Analyst", "Backend Developer"],
    }
    universal = normalize_universal_profile({
        "preferred_role_titles": ["Backend Developer", "Python Developer"],
        "alternative_role_titles": ["Full Stack Developer"],
        "seniority_level": "junior",
        "candidate_summary": "Backend engineer passionate about cybersecurity.",
        "canonical_skills": ["Python", "FastAPI"],
    })
    merged = apply_universal_profile_to_cv(cv, universal)
    roles = {r.casefold() for r in merged["best_fit_roles"]}
    assert "backend developer" in roles
    assert "it support" in roles
    assert "soc analyst" in roles
    assert "technical support specialist" in roles


def test_build_matching_strategy_includes_secondary_tracks_from_cv():
    universal = normalize_universal_profile({
        "preferred_role_titles": ["Backend Developer"],
        "alternative_role_titles": [],
        "canonical_skills": ["Python", "FastAPI"],
        "search_keywords_en": ["backend", "python"],
        "search_keywords_he": ["מפתח Backend"],
        "seniority_level": "junior",
        "candidate_summary": "Backend + support background.",
    })
    cv_profile = {
        "best_fit_roles": ["IT Support", "SOC Analyst", "Python Developer"],
        "experience": {"job_titles": ["Technical Support Specialist"]},
    }
    strategy = build_matching_strategy_from_profile(universal, {}, cv_profile)
    roles = {
        (r.get("role") if isinstance(r, dict) else r)
        for r in strategy["best_fit_roles"]
    }
    roles_l = {str(r).casefold() for r in roles}
    assert "backend developer" in roles_l
    assert "it support" in roles_l or "technical support specialist" in roles_l
    assert "soc analyst" in roles_l


def test_build_matching_strategy_from_profile():
    universal = normalize_universal_profile({
        "preferred_role_titles": ["Marketing Manager"],
        "canonical_skills": ["digital marketing", "SEO"],
        "search_keywords_en": ["marketing manager"],
        "search_keywords_he": ["מנהל שיווק"],
        "seniority_level": "mid",
        "candidate_summary": "Marketing professional.",
    })
    strategy = build_matching_strategy_from_profile(universal, {}, {})
    assert strategy["best_fit_roles"]
    assert strategy["job_categories"]
    assert strategy["collection_queries"]
    assert strategy["source"] in ("openai", "rules_fallback", "universal_profile")
