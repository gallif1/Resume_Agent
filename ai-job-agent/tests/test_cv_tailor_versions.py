"""Tests for tailored-CV version tracking and baseline scores."""

from __future__ import annotations

import json

import db
import tailor_cv_service as svc


def test_initial_score_set_on_first_match_and_preserved(db_path):
    db.init_db(db_path)
    job_id = db.insert_job(
        title="Engineer",
        job_url="https://example.com/job/1",
        company="Acme",
        description="Python",
        db_path=db_path,
    )
    db.upsert_cv_job_match(
        "cv-a",
        job_id,
        {"match_score": 76, "match_reason": "test"},
        db_path=db_path,
    )
    row = db.get_cv_job_match("cv-a", job_id, db_path=db_path)
    assert row["initial_score"] == 76

    db.upsert_cv_job_match(
        "cv-a",
        job_id,
        {"match_score": 82, "match_reason": "rescan"},
        db_path=db_path,
    )
    row = db.get_cv_job_match("cv-a", job_id, db_path=db_path)
    assert row["match_score"] == 82
    assert row["initial_score"] == 76


def test_get_match_baseline_score_prefers_initial_score(db_path):
    db.init_db(db_path)
    job_id = db.insert_job(
        title="Engineer",
        job_url="https://example.com/job/2",
        company="Acme",
        description="Python",
        db_path=db_path,
    )
    db.upsert_cv_job_match(
        "cv-b",
        job_id,
        {"match_score": 70, "match_reason": "first"},
        db_path=db_path,
    )
    db.upsert_cv_job_match(
        "cv-b",
        job_id,
        {"match_score": 85, "match_reason": "rescan"},
        db_path=db_path,
    )
    assert db.get_match_baseline_score("cv-b", job_id, db_path=db_path) == 70


def test_record_and_fetch_cv_tailor_versions(db_path):
    db.init_db(db_path)
    job_id = db.insert_job(
        title="Engineer",
        job_url="https://example.com/job/3",
        company="Acme",
        description="Python",
        db_path=db_path,
    )
    v1 = db.record_cv_tailor_version(
        "cv-c",
        job_id,
        score_before=76,
        score_after=82,
        tailored_cv_path="/tmp/1.md",
        db_path=db_path,
    )
    v2 = db.record_cv_tailor_version(
        "cv-c",
        job_id,
        score_before=82,
        score_after=88,
        tailored_cv_path="/tmp/2.md",
        db_path=db_path,
    )
    latest = db.get_latest_cv_tailor_version("cv-c", job_id, db_path=db_path)
    assert latest["id"] == v2
    assert latest["score_before"] == 82
    assert latest["score_after"] == 88
    history = db.list_cv_tailor_versions("cv-c", job_id, db_path=db_path)
    assert len(history) == 2
    assert history[0]["id"] == v2
    assert history[1]["id"] == v1


def test_tailor_cv_attaches_db_baseline_and_version(
    cvs_dir,
    db_path,
    monkeypatch,
):
    cv_id = "cv_score_flow"
    monkeypatch.setattr("config.CVS_DIR", cvs_dir)
    profile_dir = cvs_dir / cv_id
    profile_dir.mkdir(parents=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps(
            {
                "raw_text": "Name\nTechnical Support\nPython SQL",
                "experience": {
                    "job_titles": ["Technical Support"],
                    "years_of_experience_estimate": 2,
                    "seniority_level": "junior",
                },
                "skills": {"programming_languages": ["Python", "SQL"]},
                "universal_profile": {
                    "canonical_skills": ["Python", "SQL"],
                    "seniority_level": "junior",
                    "years_of_experience": 2,
                },
            }
        ),
        encoding="utf-8",
    )

    db.init_db(db_path)
    job_id = db.insert_job(
        title="Backend Engineer",
        job_url="https://example.com/job/4",
        company="Acme",
        description="Python SQL",
        db_path=db_path,
    )
    db.upsert_cv_job_match(
        cv_id,
        job_id,
        {"match_score": 76, "match_reason": "scan"},
        db_path=db_path,
    )

    def _fake_openai(*_args, **_kwargs):
        return {
            "changes_breakdown": ["Highlighted SQL"],
            "estimated_ats_score": 62,
            "cv_markdown": (
                "# Name\n\n## Skills\nPython | SQL\n\n"
                "## Experience\n### Technical Support\n- SQL troubleshooting\n"
            ),
            "highlights": ["SQL"],
            "caveats": [],
            "_from_cache": False,
        }

    monkeypatch.setattr(svc, "call_openai_json", _fake_openai)
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)

    job = db.get_job_by_id(job_id, db_path=db_path)
    result = svc.tailor_cv_for_job(
        cv_id,
        job,
        force=True,
        use_cache=False,
        db_path=db_path,
    )

    assert result["initial_match_score"] == 76
    assert result["score_before"] == 76
    assert result["score_after"] is not None
    # Must not trust the LLM-hallucinated 62 when deterministic score differs.
    assert result["estimated_ats_score"] == result["score_after"]
    assert result["version_id"] is not None
    latest = db.get_latest_cv_tailor_version(cv_id, job_id, db_path=db_path)
    assert latest["score_before"] == 76
    assert latest["score_after"] == result["score_after"]
