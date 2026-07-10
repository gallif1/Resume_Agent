"""Tests for profile_matcher deterministic scoring."""

from __future__ import annotations

from job_analyzer import analyze_job_fallback
from multilingual_normalizer import clear_profile_terms, register_profile_terms
from profile_matcher import score


def setup_function():
    clear_profile_terms()


def _nurse_profile() -> dict:
    return {
        "preferred_role_titles": ["Registered Nurse", "ICU Nurse"],
        "alternative_role_titles": ["Staff Nurse"],
        "canonical_skills": ["patient care", "ICU", "vitals monitoring"],
        "technologies_tools": [],
        "domain_keywords": ["healthcare", "hospital"],
        "seniority_level": "mid",
        "search_keywords_en": ["nurse", "RN"],
        "search_keywords_he": ["אחות", "סיעוד"],
        "exclusion_keywords": ["software", "developer"],
        "location_preferences": {"preferred_locations": ["Israel"], "remote_ok": True},
    }


def test_nurse_job_scores_well():
    register_profile_terms(_nurse_profile())
    job = {
        "title": "Registered Nurse - ICU",
        "description": "",
        "full_description": (
            "Hospital seeking registered nurse for ICU. "
            "Patient care experience required. Hebrew and English."
        ),
        "location": "Tel Aviv, Israel",
    }
    job_profile = analyze_job_fallback(job)
    result = score(_nurse_profile(), job, job_profile)
    assert result.score >= 50
    assert not result.exclusion_hit


def test_exclusion_keyword_caps_score():
    register_profile_terms(_nurse_profile())
    job = {
        "title": "Senior Software Developer",
        "description": "developer role",
        "full_description": "Looking for a senior software developer with Python.",
        "location": "Tel Aviv",
    }
    job_profile = analyze_job_fallback(job)
    result = score(_nurse_profile(), job, job_profile)
    assert result.exclusion_hit or result.score <= 49


def test_bilingual_title_match():
    register_profile_terms(_nurse_profile())
    job = {
        "title": "אחות/אח - מחלקה פנימית",
        "description": "",
        "full_description": "דרוש/ה אחות עם ניסיון בסיעוד. אחות מוסמכת.",
        "location": "ישראל",
    }
    job_profile = analyze_job_fallback(job)
    result = score(_nurse_profile(), job, job_profile)
    assert result.score > 0
