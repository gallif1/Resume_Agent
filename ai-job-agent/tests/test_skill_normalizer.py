"""Tests for skill normalization (English + Hebrew equivalences)."""

from __future__ import annotations

from skill_normalizer import (
    find_matching_skills,
    normalize_skill,
    normalize_skill_set,
    skills_match,
)


def test_office_365_normalizes_to_microsoft_365():
    assert normalize_skill("Office 365") == "Microsoft 365"
    assert normalize_skill("o365") == "Microsoft 365"


def test_ad_normalizes_to_active_directory():
    assert normalize_skill("AD") == "Active Directory"
    assert normalize_skill("active directory") == "Active Directory"


def test_help_desk_normalizes_to_technical_support():
    assert normalize_skill("Help Desk") == "Technical Support"
    assert normalize_skill("helpdesk") == "Technical Support"
    assert normalize_skill("תמיכה טכנית") == "Technical Support"


def test_soc_analyst_in_cyber_context():
    assert normalize_skill("SOC Analyst", domain="cyber") == "Security Analyst"
    assert skills_match("SOC Analyst", "Security Analyst", domain="cyber")


def test_hebrew_language_aliases():
    assert normalize_skill("אנגלית") == "English"
    assert normalize_skill("עברית") == "Hebrew"


def test_find_matching_skills():
    cv_skills = {"Python", "AWS", "Docker", "English"}
    required = ["python", "kubernetes", "אנגלית"]
    matched, missing = find_matching_skills(cv_skills, required)
    assert "Python" in matched
    assert "English" in matched
    assert "Kubernetes" in missing


def test_normalize_skill_set_deduplicates():
    result = normalize_skill_set(["python", "Python", "PYTHON"])
    assert result == {"Python"}
