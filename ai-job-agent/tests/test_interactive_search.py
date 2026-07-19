"""Tests for interactive analyze → domain select → incremental search flow."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import api_server
import config
import cv_service
import db
import pytest
from collect_jobs import filter_plan_by_domains
from conftest import register_test_user


SAMPLE_CV_TEXT = """
Jane Doe
jane@example.com | Tel Aviv

Summary
Fullstack developer with React, Node.js, Python and Docker.

Experience
Fullstack Developer @ Acme (2021 – Present)
- Built web apps with React and FastAPI
- Operated Docker on AWS

Skills
Python, JavaScript, React, FastAPI, Docker, AWS

Education
BSc Computer Science, Tel Aviv University
"""


def _sample_strategy() -> dict[str, Any]:
    return {
        "analyzed_at": "2026-01-01T00:00:00+00:00",
        "source": "test",
        "candidate_summary": "Fullstack engineer with React and Python.",
        "career_notes": "Strong fit for fullstack and devops-adjacent roles.",
        "best_fit_roles": [
            {
                "role": "Fullstack Developer",
                "score": 92,
                "reason": "Matches React + Python stack",
                "missing_skills": [],
                "realistic_for_application": True,
            },
            {
                "role": "Backend Developer",
                "score": 80,
                "reason": "Strong Python/API background",
                "missing_skills": [],
                "realistic_for_application": True,
            },
        ],
        "job_categories": [
            {
                "category": "fullstack",
                "titles": ["Fullstack Developer", "Full Stack Engineer"],
                "must_have_keywords": ["react", "python"],
                "nice_to_have_keywords": ["docker"],
                "negative_keywords": ["senior"],
                "score_weight": 1.0,
            }
        ],
        "collection_queries": [
            {
                "category": "fullstack",
                "priority": 90,
                "primary_role": "Fullstack Developer",
                "search_queries": ["Fullstack Developer", "React Developer"],
                "hebrew_search_queries": ["מפתח פולסטאק"],
                "queries": ["Fullstack Developer", "React Developer"],
                "queries_en": ["Fullstack Developer", "React Developer"],
                "queries_he": ["מפתח פולסטאק"],
                "exclude_keywords": ["senior"],
            },
            {
                "category": "backend",
                "priority": 75,
                "primary_role": "Backend Developer",
                "search_queries": ["Backend Developer", "Python Developer"],
                "hebrew_search_queries": ["מפתח Backend"],
                "queries": ["Backend Developer", "Python Developer"],
                "queries_en": ["Backend Developer", "Python Developer"],
                "queries_he": ["מפתח Backend"],
                "exclude_keywords": ["senior"],
            },
        ],
    }


@pytest.fixture
def interactive_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path):
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
    monkeypatch.setattr(api_server, "_workspace_match_count", lambda *a, **k: 0)
    monkeypatch.setattr(api_server, "_persist_scan_state", lambda: None)

    orig_upload = cv_service.upload_cv
    orig_list_cvs = db.list_cvs
    orig_get_cv = db.get_cv
    orig_analyze = cv_service.analyze_cv
    orig_search = cv_service.run_search

    def _upload(filename, data, **kwargs):
        kwargs.pop("db_path", None)
        return orig_upload(filename, data, db_path=db_path, **kwargs)

    def _list_cvs(**kwargs):
        kwargs.pop("db_path", None)
        return orig_list_cvs(db_path=db_path, **kwargs)

    def _get_cv(cv_id, **kwargs):
        kwargs.pop("db_path", None)
        return orig_get_cv(cv_id, db_path=db_path, **kwargs)

    def fake_analyze(cv_id: str, **kwargs):
        kwargs.pop("db_path", None)
        cv_dir = config.cv_data_dir(cv_id)
        cv_dir.mkdir(parents=True, exist_ok=True)
        strategy = _sample_strategy()
        (cv_dir / "ai_matching_strategy.json").write_text(
            json.dumps(strategy, ensure_ascii=False), encoding="utf-8"
        )
        (cv_dir / "cv_profile.json").write_text(
            json.dumps(
                {
                    "raw_text": SAMPLE_CV_TEXT,
                    "best_fit_roles": ["Fullstack Developer", "Backend Developer"],
                    "contact": {"name": "Jane Doe", "email": "jane@example.com"},
                    "skills": {"programming_languages": ["Python", "JavaScript"]},
                    "experience": {"seniority_level": "mid"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {
            "cv_id": cv_id,
            "domains": cv_service.extract_recommended_domains(strategy),
            "candidate_summary": strategy["candidate_summary"],
            "career_notes": strategy["career_notes"],
            "best_fit_roles": strategy["best_fit_roles"],
        }

    monkeypatch.setattr(api_server.cv_service, "upload_cv", _upload)
    monkeypatch.setattr(api_server.db, "list_cvs", _list_cvs)
    monkeypatch.setattr(api_server.db, "get_cv", _get_cv)
    monkeypatch.setattr(api_server.cv_service, "analyze_cv", fake_analyze)

    with api_server._scan_lock:
        api_server._scan_state.update(
            {
                "running": False,
                "mode": None,
                "cv_id": None,
                "user_id": None,
                "steps": [],
                "log": [],
                "warnings": [],
                "error": None,
                "finished_at": None,
            }
        )

    from conftest import authed_client, default_auth_user

    # Ensure default user exists for ownership.
    db.init_registry_db(db_path)
    if db.get_user_by_id(db.DEFAULT_USER_ID, db_path=db_path) is None:
        register_test_user(email="default@local", db_path=db_path)

    return {
        "db_path": db_path,
        "cvs_dir": cvs_dir,
        "authed_client": authed_client,
        "fake_analyze": fake_analyze,
        "orig_search": orig_search,
    }


def test_extract_recommended_domains_dedupes():
    domains = cv_service.extract_recommended_domains(_sample_strategy())
    assert "Fullstack Developer" in domains
    assert "Backend Developer" in domains
    assert len(domains) == len({d.casefold() for d in domains})


def test_extract_recommended_domains_includes_search_and_hebrew_titles():
    strategy = _sample_strategy()
    strategy["collection_queries"][0]["search_queries"] = [
        "React Developer",
        "FastAPI Developer",
    ]
    strategy["collection_queries"][0]["hebrew_search_queries"] = ["מפתח פייתון"]
    strategy["best_fit_roles"].append({
        "role": "IT Support",
        "score": 70,
        "reason": "support track",
        "missing_skills": [],
        "realistic_for_application": True,
    })
    domains = cv_service.extract_recommended_domains(strategy)
    assert "React Developer" in domains or "FastAPI Developer" in domains
    assert "מפתח פייתון" in domains
    assert "IT Support" in domains


def test_filter_plan_by_domains_keeps_matches_and_adds_custom():
    plan = _sample_strategy()["collection_queries"]
    filtered = filter_plan_by_domains(plan, ["Fullstack Developer", "DevOps"])
    labels = " ".join(
        f"{e.get('primary_role', '')} {e.get('category', '')}" for e in filtered
    ).lower()
    assert "fullstack" in labels
    assert any("devops" in str(e.get("primary_role", "")).lower() for e in filtered)


def test_analyze_endpoint_returns_domains(interactive_env):
    with interactive_env["authed_client"]() as client:
        upload = client.post(
            "/cvs/upload",
            files={"file": ("resume.txt", SAMPLE_CV_TEXT.encode("utf-8"), "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        cv_id = upload.json()["cv"]["id"]

        res = client.post(f"/cvs/{cv_id}/analyze")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["cv_id"] == cv_id
        assert "Fullstack Developer" in body["domains"]
        assert body["candidate_summary"]


def test_search_preserves_previous_jobs(interactive_env, monkeypatch):
    db_path = interactive_env["db_path"]

    def fake_search(
        cv_id_arg: str,
        *,
        domains: list[str],
        skip_enrich: bool = False,
        job_sites: list[str] | None = None,
        log=None,
        set_step_status=None,
        db_path_arg=db.REGISTRY_DB_PATH,
    ):
        assert domains == ["Fullstack Developer", "DevOps"]
        cv_db = config.cv_db_path(cv_id_arg)
        db.init_db(cv_db)
        new_id = db.insert_job(
            title="New Fullstack Role",
            job_url="https://www.drushim.co.il/job/222/def",
            company="NewCo",
            location="Tel Aviv",
            source="drushim",
            db_path=cv_db,
        )
        ignored = db.insert_job(
            title="Existing Role",
            job_url="https://www.drushim.co.il/job/111/abc",
            company="OldCo",
            location="Tel Aviv",
            source="drushim",
            db_path=cv_db,
        )
        assert ignored is None
        scan_id = db.create_scan(cv_id_arg, db_path=cv_db)
        if set_step_status:
            for key, *_ in cv_service.SEARCH_STEPS:
                set_step_status(key, "success")
        if new_id is not None:
            db.upsert_cv_job_match(
                cv_id_arg,
                new_id,
                {
                    "match_score": 77,
                    "match_reason": "new",
                    "match_method": "test",
                },
                scan_id=scan_id,
                db_path=cv_db,
            )
        db.finish_scan(
            scan_id,
            db.SCAN_SUCCESS,
            summary=json.dumps({"matches": 2, "domains": domains}),
            db_path=cv_db,
        )
        return db.get_scan(scan_id, db_path=cv_db) or {"status": db.SCAN_SUCCESS}

    monkeypatch.setattr(api_server.cv_service, "run_search", fake_search)
    monkeypatch.setattr(api_server, "begin_scan", lambda: None)
    monkeypatch.setattr(api_server, "is_cancelled", lambda: False)

    with interactive_env["authed_client"]() as client:
        upload = client.post(
            "/cvs/upload",
            files={"file": ("resume.txt", SAMPLE_CV_TEXT.encode("utf-8"), "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        cv_id = upload.json()["cv"]["id"]
        assert client.post(f"/cvs/{cv_id}/analyze").status_code == 200

        cv_db = config.cv_db_path(cv_id)
        db.init_db(cv_db)
        existing_id = db.insert_job(
            title="Existing Role",
            job_url="https://www.drushim.co.il/job/111/abc",
            company="OldCo",
            location="Tel Aviv",
            source="drushim",
            db_path=cv_db,
        )
        assert existing_id is not None
        scan1 = db.create_scan(cv_id, db_path=cv_db)
        db.upsert_cv_job_match(
            cv_id,
            existing_id,
            {
                "match_score": 88,
                "match_reason": "prior",
                "match_method": "test",
            },
            scan_id=scan1,
            db_path=cv_db,
        )
        db.finish_scan(scan1, db.SCAN_SUCCESS, summary='{"matches":1}', db_path=cv_db)

        with api_server._scan_lock:
            api_server._scan_state["running"] = False

        started = client.post(
            f"/cvs/{cv_id}/search",
            json={
                "domains": ["Fullstack Developer", "DevOps"],
                "job_sites": ["drushim"],
            },
        )
        assert started.status_code == 200, started.text
        assert started.json()["started"] is True

        for _ in range(50):
            status = client.get(f"/cvs/{cv_id}/scan-status")
            assert status.status_code == 200
            if not status.json().get("running"):
                break
            time.sleep(0.05)

        matches = client.get(f"/cvs/{cv_id}/matches")
        assert matches.status_code == 200
        rows = matches.json()["matches"]
        urls = {m.get("job_url") for m in rows}
        assert "https://www.drushim.co.il/job/111/abc" in urls
        assert "https://www.drushim.co.il/job/222/def" in urls
        assert len(rows) >= 2


def test_search_requires_domains(interactive_env):
    with interactive_env["authed_client"]() as client:
        upload = client.post(
            "/cvs/upload",
            files={"file": ("resume.txt", SAMPLE_CV_TEXT.encode("utf-8"), "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        cv_id = upload.json()["cv"]["id"]
        with api_server._scan_lock:
            api_server._scan_state["running"] = False
        res = client.post(f"/cvs/{cv_id}/search", json={"domains": []})
        assert res.status_code == 400


def test_analyze_cv_reads_per_cv_strategy_not_legacy_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regression: analyze_cv must not return the committed legacy Gal strategy.

    Subprocesses write under data/cvs/<cv_id>/ with AGENT_CV_ID set, but the API
    process has no AGENT_CV_ID so the module-level default strategy path still
    points at the global file. Loading without an explicit per-CV path used to
    always surface Junior Backend / SOC Analyst / Server Monitor System.
    """
    db_path = tmp_path / "registry.db"
    cvs_dir = tmp_path / "cvs"
    data_dir = tmp_path / "data"
    cvs_dir.mkdir()
    data_dir.mkdir()
    db.init_registry_db(db_path)

    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)

    legacy_strategy = {
        "analyzed_at": "2026-07-02T17:18:35.398009+00:00",
        "source": "openai",
        "candidate_summary": (
            "Name: Gal Lifshitz\nProjects: Server Monitor System with ThreadPoolExecutor"
        ),
        "career_notes": "legacy",
        "best_fit_roles": [
            {
                "role": "Junior Backend Developer",
                "score": 90,
                "reason": "legacy",
                "missing_skills": [],
                "realistic_for_application": True,
            },
            {
                "role": "SOC Analyst",
                "score": 80,
                "reason": "legacy",
                "missing_skills": [],
                "realistic_for_application": True,
            },
            {
                "role": "IT Support Specialist",
                "score": 85,
                "reason": "legacy",
                "missing_skills": [],
                "realistic_for_application": True,
            },
        ],
        "job_categories": [],
        "collection_queries": [
            {"primary_role": "Junior Backend Developer"},
            {"primary_role": "SOC Analyst"},
            {"primary_role": "IT Support Specialist"},
        ],
    }
    legacy_path = data_dir / "ai_matching_strategy.json"
    legacy_path.write_text(json.dumps(legacy_strategy), encoding="utf-8")

    # Simulate the API process: no AGENT_CV_ID → default load path is legacy.
    import role_analyzer

    monkeypatch.setattr(role_analyzer, "AI_MATCHING_STRATEGY_PATH", legacy_path)
    monkeypatch.setattr(role_analyzer, "AI_ROLES_PATH", data_dir / "ai_roles.json")

    cv = cv_service.upload_cv(
        "fresh_resume.txt",
        SAMPLE_CV_TEXT.encode("utf-8"),
        db_path=db_path,
    )
    cv_id = cv["id"]

    per_cv_strategy = _sample_strategy()

    def fake_subprocess(cmd, *, env=None, log=None):
        assert env and env.get("AGENT_CV_ID") == cv_id
        cv_dir = config.cv_data_dir(cv_id)
        cv_dir.mkdir(parents=True, exist_ok=True)
        script = Path(cmd[1]).name if len(cmd) > 1 else ""
        if script == "parse_cv.py":
            (cv_dir / "cv_profile.json").write_text(
                json.dumps(
                    {
                        "raw_text": SAMPLE_CV_TEXT,
                        "best_fit_roles": ["Fullstack Developer", "Backend Developer"],
                        "contact": {"name": "Jane Doe"},
                        "skills": {},
                        "experience": {},
                    }
                ),
                encoding="utf-8",
            )
        elif script == "analyze_roles.py":
            (cv_dir / "ai_matching_strategy.json").write_text(
                json.dumps(per_cv_strategy), encoding="utf-8"
            )
            (cv_dir / "ai_roles.json").write_text(
                json.dumps(
                    {
                        "candidate_summary": per_cv_strategy["candidate_summary"],
                        "best_fit_roles": per_cv_strategy["best_fit_roles"],
                    }
                ),
                encoding="utf-8",
            )
        return 0

    monkeypatch.setattr(cv_service, "_run_logged_subprocess", fake_subprocess)
    monkeypatch.setattr(cv_service, "sync_parsed_profile", lambda *a, **k: None)

    result = cv_service.analyze_cv(cv_id, db_path=db_path)

    assert result["cv_id"] == cv_id
    assert "Fullstack Developer" in result["domains"]
    assert "Backend Developer" in result["domains"]
    assert "Junior Backend Developer" not in result["domains"]
    assert "SOC Analyst" not in result["domains"]
    assert "IT Support Specialist" not in result["domains"]
    assert "Server Monitor System" not in result["candidate_summary"]
    assert "ThreadPoolExecutor" not in result["candidate_summary"]
    assert "Fullstack engineer" in result["candidate_summary"]
