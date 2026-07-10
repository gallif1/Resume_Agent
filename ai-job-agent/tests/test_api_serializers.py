"""Tests for the API response serializers in api_server."""

from __future__ import annotations

import json

import api_server
import db


def test_cv_public_extracts_profile_summary():
    cv = {
        "id": "x",
        "file_name": "a.pdf",
        "display_name": "A",
        "file_ext": ".pdf",
        "file_size": 10,
        "created_at": "t",
        "updated_at": "t",
        "last_scan_at": None,
        "match_count": 3,
        "scan_count": 1,
        "parsed_profile": json.dumps(
            {
                "contact": {"name": "Gal"},
                "experience": {"seniority_level": "junior"},
                "best_fit_roles": ["Backend", "IT"],
                "skills": {"prog": ["python", "sql"], "cloud": ["aws"]},
            }
        ),
    }
    out = api_server._cv_public(cv)
    assert out["id"] == "x"
    assert out["profile"]["name"] == "Gal"
    assert out["profile"]["seniority"] == "junior"
    assert out["profile"]["skills_count"] == 3
    assert "parsed_profile" not in out


def test_match_public_parses_skill_lists_and_defaults_status():
    match = {
        "match_id": 1,
        "job_id": 2,
        "title": "Dev",
        "matched_skills": '["python"]',
        "missing_skills": None,
        "ai_missing_skills": '["docker"]',
        "application_status": None,
        "match_updated_at": "u",
    }
    out = api_server._match_public(match)
    assert out["matched_skills"] == ["python"]
    assert out["missing_skills"] == ["docker"]  # falls back to ai_missing_skills
    assert out["application_status"] == db.CV_APP_NOT_SENT


def test_match_public_includes_ats_fields():
    match = {
        "match_id": 1,
        "job_id": 2,
        "title": "Dev",
        "ats_score_label": "Good Match",
        "ats_missing_mandatory": '["5+ years experience"]',
        "ats_relevant_experience": '["Backend Developer"]',
        "ats_reasons": '["Required skills: 3/4 matched"]',
        "ats_improvements": '["Add skill: Rust"]',
        "application_status": "not_sent",
        "match_updated_at": "u",
    }
    out = api_server._match_public(match)
    assert out["score_label"] == "Good Match"
    assert out["missing_mandatory"] == ["5+ years experience"]
    assert out["relevant_experience"] == ["Backend Developer"]
    assert out["score_reasons"] == ["Required skills: 3/4 matched"]
    assert out["cv_improvements"] == ["Add skill: Rust"]


def test_reshape_match_row_maps_id_to_match_id():
    row = {"id": 9, "updated_at": "u", "application_status": "sent"}
    reshaped = api_server._reshape_match_row(row)
    out = api_server._match_public(reshaped)
    assert out["match_id"] == 9
    assert out["application_status"] == "sent"
