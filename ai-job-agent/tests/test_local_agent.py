"""Tests for local agent bundle export and job ingest."""

from __future__ import annotations

import json
from pathlib import Path

import db
from local_agent import (
    export_local_agent_bundle,
    ingest_collected_jobs,
    write_local_agent_bundle,
)


def test_export_and_ingest_jobs(tmp_path, monkeypatch):
    cv_id = "test-cv-local"
    cv_dir = tmp_path / "cvs" / cv_id
    cv_dir.mkdir(parents=True)
    (cv_dir / "profile.json").write_text(
        json.dumps({"target_roles": ["developer"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (cv_dir / "ai_matching_strategy.json").write_text(
        json.dumps({"collection_queries": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr("local_agent.cv_data_dir", lambda cid: tmp_path / "cvs" / cid)
    monkeypatch.setattr("local_agent.cv_db_path", lambda cid: tmp_path / "cvs" / cid / "jobs.db")

    bundle = export_local_agent_bundle(cv_id)
    assert bundle["cv_id"] == cv_id
    assert "profile.json" in bundle["files"]

    write_local_agent_bundle("other-cv", bundle)
    assert (tmp_path / "cvs" / "other-cv" / "profile.json").is_file()

    jobs = [
        {
            "title": "Python Dev",
            "job_url": "https://www.drushim.co.il/job/1/abc/",
            "company": "Acme",
            "location": "Tel Aviv",
            "source": "drushim",
            "description": "Short",
            "full_description": "Long description here",
        }
    ]
    db.init_db(tmp_path / "cvs" / cv_id / "jobs.db")
    summary = ingest_collected_jobs(cv_id, jobs)
    assert summary["inserted"] == 1

    rows = db.get_all_jobs(db_path=tmp_path / "cvs" / cv_id / "jobs.db")
    assert len(rows) == 1
    assert rows[0]["full_description"] == "Long description here"
