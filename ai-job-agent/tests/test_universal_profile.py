"""Tests for universal_profile module."""

from __future__ import annotations

from universal_profile import (
    apply_universal_profile_to_cv,
    build_matching_strategy_from_profile,
    build_universal_profile_fallback,
    normalize_universal_profile,
)


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
