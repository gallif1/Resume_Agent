"""API tests for the honest match-report endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import api_server
import config
import cv_service
import db
import match_tailor_service
import pytest


@pytest.fixture
def report_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path):
    cvs_dir = tmp_path / "cvs"
    users_dir = tmp_path / "users"
    cvs_dir.mkdir()
    users_dir.mkdir()
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    monkeypatch.setattr(config, "USERS_DIR", users_dir)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)
    return {"db_path": db_path, "cvs_dir": cvs_dir}


def _openai_response() -> dict[str, Any]:
    """A model response that inflates the score against a Salesforce role."""
    return {
        "requirement_extraction": {
            "hard_requirements": [
                {
                    "requirement": "Salesforce Apex development",
                    "candidate_status": "MISSING",
                    "evidence_or_gap": "No Salesforce work on the resume",
                },
                {
                    "requirement": "Python scripting",
                    "candidate_status": "MATCH",
                    "evidence_or_gap": "Python backend services",
                },
            ],
            "soft_requirements": [
                {
                    "requirement": "AWS familiarity",
                    "candidate_status": "MATCH",
                    "evidence_or_gap": "AWS Lambda automation",
                }
            ],
        },
        "scoring": {
            "hard_score_pct": 50,
            "soft_score_pct": 100,
            "hard_cap_applied": False,
            "realistic_match_score": 83,
            "score_rationale": "Strong Python overlap.",
        },
        "key_matching_points": ["Python", "AWS"],
        "missing_critical_skills": [],
        "transferable_skills_framing": [
            {
                "gap": "Salesforce Apex",
                "how_to_honestly_frame_existing_experience": (
                    "Python and AWS automation transfers to custom business logic."
                ),
            }
        ],
        "tailored_cv": {
            "summary": "Backend engineer focused on Python automation.",
            "skills": ["Python", "AWS"],
            "experience": [
                {
                    "company": "Acme",
                    "title": "Backend Developer",
                    "dates": "2023-2025",
                    "bullets": ["Built Python services."],
                }
            ],
            "projects": [],
            "education": [],
        },
        "recommendation": "STRONG_APPLY",
    }


def test_workspace_match_report_caps_an_inflated_score(
    report_env, monkeypatch: pytest.MonkeyPatch
):
    db_path = report_env["db_path"]
    user_id = db.DEFAULT_USER_ID
    user_db = config.user_db_path(user_id)
    db.init_db(user_db)

    cv = cv_service.upload_cv("a.pdf", b"report-bytes", db_path=db_path)
    job_id = db.insert_job(
        title="Salesforce Developer (Apex, LWC)",
        job_url="https://example.com/job/report-1",
        company="Dot Compliance",
        description="Apex, LWC, plus Python/AWS scripting.",
        db_path=user_db,
    )
    assert job_id is not None

    profile_dir = config.user_data_dir(user_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps(
            {
                "contact": {"name": "Gal"},
                "raw_text": "Python, FastAPI, React, AWS Lambda developer",
                "skills": {"programming_languages": ["Python"]},
                "experience": {"job_titles": ["Backend Developer"]},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(match_tailor_service, "is_ai_available", lambda: True)
    monkeypatch.setattr(
        match_tailor_service,
        "call_openai_json",
        lambda *a, **kw: _openai_response(),
    )
    real_get_cv = db.get_cv
    monkeypatch.setattr(
        api_server.db,
        "get_cv",
        lambda cv_id, **kw: real_get_cv(cv_id, db_path=db_path),
    )

    from conftest import authed_client

    with authed_client() as client:
        res = client.post(
            f"/jobs/{job_id}/match-report",
            params={"source_cv_id": cv["id"]},
            json={"force": True},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["job_id"] == job_id
    assert body["title"] == "Salesforce Developer (Apex, LWC)"

    scoring = body["scoring"]
    assert scoring["hard_cap_applied"] is True
    assert scoring["realistic_match_score"] <= 55
    assert body["score_validation"]["model_reported_score"] == 83
    assert body["recommendation"] != "STRONG_APPLY"
    assert any(
        "salesforce" in skill.lower() for skill in body["missing_critical_skills"]
    )
    assert body["requirement_extraction"]["hard_requirements"][0]["evidence_or_gap"]


def test_match_report_returns_503_without_an_api_key(
    report_env, monkeypatch: pytest.MonkeyPatch
):
    db_path = report_env["db_path"]
    user_id = db.DEFAULT_USER_ID
    user_db = config.user_db_path(user_id)
    db.init_db(user_db)

    job_id = db.insert_job(
        title="Backend Engineer",
        job_url="https://example.com/job/report-2",
        company="Acme",
        db_path=user_db,
    )
    assert job_id is not None

    profile_dir = config.user_data_dir(user_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps({"raw_text": "Python developer"}), encoding="utf-8"
    )

    monkeypatch.setattr(match_tailor_service, "is_ai_available", lambda: False)

    from conftest import authed_client

    with authed_client() as client:
        res = client.post(f"/jobs/{job_id}/match-report", json={"force": True})

    assert res.status_code == 503
