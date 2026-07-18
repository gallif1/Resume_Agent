"""Regression tests for workspace tailor-CV API (500 on match tailor)."""

from __future__ import annotations

import json
from pathlib import Path

import api_server
import config
import cv_service
import db
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tailor_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path):
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
    return {"db_path": db_path, "cvs_dir": cvs_dir, "users_dir": users_dir}


def test_workspace_tailor_endpoint_does_not_500(tailor_env, monkeypatch):
    """Passing the tailor result dict into extract_cv_markdown used to 500."""
    db_path = tailor_env["db_path"]
    user_id = db.DEFAULT_USER_ID
    user_db = config.user_db_path(user_id)
    db.init_db(user_db)

    cv = cv_service.upload_cv("a.pdf", b"tailor-bytes", db_path=db_path)
    job_id = db.insert_job(
        title="Backend",
        job_url="https://example.com/job/tailor-1",
        company="Acme",
        db_path=user_db,
    )
    assert job_id is not None
    scan_id = db.create_scan(db.WORKSPACE_CV_ID, db_path=user_db)
    db.upsert_cv_job_match(
        db.WORKSPACE_CV_ID,
        job_id,
        {
            "match_score": 70,
            "match_reason": "ok",
            "match_method": "local",
            "match_category": "backend",
            "matched_skills": "[]",
            "missing_skills": "[]",
            "candidate_strategy_hash": "h",
        },
        scan_id=scan_id,
        db_path=user_db,
    )

    profile_dir = config.user_data_dir(user_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps(
            {
                "contact": {"name": "Gal"},
                "raw_text": "Python developer",
                "skills": {"programming_languages": ["Python"]},
                "experience": {"job_titles": ["Backend Developer"]},
            }
        ),
        encoding="utf-8",
    )

    def fake_tailor(cv_id, job, **kwargs):
        return {
            "markdown": (
                "## פירוט שינויים\n- Highlighted Python\n\n---\n\n"
                "## קורות החיים המעודכנים\n\n# Gal\n\n## Skills\nPython\n"
            ),
            "cv_markdown": "# Gal\n\n## Skills\nPython\n",
            "changes_breakdown": ["Highlighted Python"],
            "estimated_ats_score": 72,
            "highlights": ["Python"],
            "caveats": [],
            "from_cache": False,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "regenerated": False,
            "no_improvement": False,
        }

    monkeypatch.setattr(api_server, "tailor_cv_for_job", fake_tailor)
    real_get_cv = db.get_cv
    monkeypatch.setattr(
        api_server.db,
        "get_cv",
        lambda cv_id, **kw: real_get_cv(cv_id, db_path=db_path),
    )

    from conftest import authed_client

    with authed_client() as client:
        res = client.post(
            f"/jobs/{job_id}/tailor-cv",
            params={"source_cv_id": cv["id"]},
            json={"force": True},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["job_id"] == job_id
    assert body["cv_markdown"].startswith("# Gal")
    assert "פירוט שינויים" not in body["cv_markdown"]
    assert body["markdown"]
    assert body["from_cache"] is False

    # Metadata recorded on workspace match.
    match = db.get_cv_job_match(db.WORKSPACE_CV_ID, job_id, db_path=user_db)
    assert match is not None
    assert match.get("tailored_cv_path")


def test_workspace_tailor_old_bug_path_would_have_crashed():
    """Document the exact failure mode that produced HTTP 500."""
    from tailor_cv_service import extract_cv_markdown_for_copy

    # New helper accepts dicts; previously .strip() on a dict raised AttributeError.
    out = extract_cv_markdown_for_copy(
        {"markdown": "# Name\n", "cv_markdown": "# Name\n"}
    )
    assert out.startswith("# Name")
