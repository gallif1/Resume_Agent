"""Unit tests for field matching (no browser)."""

from __future__ import annotations

from job_apply.fields import build_profile_values, match_field_key, normalize_label


def test_normalize_label():
    assert normalize_label("  First Name  ") == "first name"


def test_match_field_key_english_and_hebrew():
    assert match_field_key("email address") == "email"
    assert match_field_key("שם פרטי") == "first_name"
    assert match_field_key("שם משפחה") == "last_name"
    assert match_field_key("טלפון נייד") == "phone"
    assert match_field_key("Upload Resume") == "cv_file"
    assert match_field_key("random xyz") is None


def test_build_profile_values_from_applicant_dict():
    values = build_profile_values(
        {
            "first_name": "Dana",
            "last_name": "Cohen",
            "email": "dana@example.com",
            "phone": "0501112233",
        }
    )
    assert values["first_name"] == "Dana"
    assert values["last_name"] == "Cohen"
    assert values["full_name"] == "Dana Cohen"
    assert values["email"] == "dana@example.com"
    assert values["phone"] == "0501112233"
