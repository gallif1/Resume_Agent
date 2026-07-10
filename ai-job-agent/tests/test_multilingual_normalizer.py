"""Tests for multilingual_normalizer canonical concept mapping."""

from __future__ import annotations

from multilingual_normalizer import (
    clear_profile_terms,
    expand_synonyms,
    register_profile_terms,
    terms_overlap,
    title_similarity,
    to_canonical,
)


def setup_function():
    clear_profile_terms()


def test_hebrew_nurse_maps_to_canonical():
    assert to_canonical("אחות") == "Nurse"


def test_developer_hebrew_synonym():
    assert to_canonical("מפתח") == "Developer"


def test_terms_overlap_bilingual():
    matched, missing = terms_overlap(
        ["Python", "AWS", "תמיכה טכנית"],
        "We need a python developer with AWS. תמיכה טכנית required.",
    )
    assert "Python" in matched
    assert "AWS" in matched
    assert "Technical Support" in matched or "תמיכה טכנית" in matched


def test_register_profile_terms():
    register_profile_terms({
        "canonical_skills": ["Epic EMR"],
        "search_keywords_he": ["אחות טיפולית"],
    })
    assert to_canonical("Epic EMR") == "Epic EMR"
    assert to_canonical("אחות טיפולית") == "אחות טיפולית"


def test_title_similarity():
    score = title_similarity("Python Backend Developer", "Backend Developer")
    assert score > 0.3


def test_expand_synonyms_includes_hebrew():
    variants = expand_synonyms("Nurse")
    assert any("אחות" in v for v in variants)
