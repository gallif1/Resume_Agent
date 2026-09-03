"""Tests for tailored-CV version tracking and baseline scores."""

from __future__ import annotations

import json

import db
import match_tailor_service
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


def test_persist_tailored_cv_markdown_records_version(cvs_dir, db_path, monkeypatch):
    db.init_db(db_path)
    monkeypatch.setattr("config.CVS_DIR", cvs_dir)
    cv_id = "cv_persist"
    job_id = db.insert_job(
        title="Engineer",
        job_url="https://example.com/job/persist",
        company="Acme",
        description="Python",
        db_path=db_path,
    )
    db.upsert_cv_job_match(
        cv_id,
        job_id,
        {"match_score": 61, "match_reason": "test"},
        db_path=db_path,
    )
    saved = svc.persist_tailored_cv_markdown(
        cv_id,
        job_id,
        "# MVP CV\n\nTailored body",
        db_path=db_path,
        pdf_bytes=b"%PDF-1.4 test",
    )
    assert saved["version_id"] is not None
    history = db.list_cv_tailor_versions(cv_id, job_id, db_path=db_path)
    assert len(history) == 1
    match = db.get_cv_job_match(cv_id, job_id, db_path=db_path)
    assert match.get("tailored_cv_path")
    loaded = svc.load_saved_tailored_cv_pdf(
        cv_id, job_id, version_id=saved["version_id"]
    )
    assert loaded == b"%PDF-1.4 test"


def test_record_version_archives_markdown_per_version(
    cvs_dir, db_path, monkeypatch
):
    db.init_db(db_path)
    monkeypatch.setattr("config.CVS_DIR", cvs_dir)
    cv_id = "cv_archive"
    job_id = 7

    svc.save_tailored_cv(cv_id, job_id, "# Version A")
    version_id = svc._record_version(
        cv_id,
        job_id,
        score_before=70,
        score_after=75,
        path=svc.tailored_cv_path(cv_id, job_id),
        db_path=db_path,
    )
    assert version_id is not None
    archive_a = svc.tailored_cv_version_path(cv_id, job_id, version_id)
    assert archive_a.exists()
    assert "Version A" in archive_a.read_text(encoding="utf-8")

    svc.save_tailored_cv(cv_id, job_id, "# Version B")
    version_id_2 = svc._record_version(
        cv_id,
        job_id,
        score_before=75,
        score_after=80,
        path=svc.tailored_cv_path(cv_id, job_id),
        db_path=db_path,
    )
    assert version_id_2 is not None
    archive_b = svc.tailored_cv_version_path(cv_id, job_id, version_id_2)
    assert "Version B" in archive_b.read_text(encoding="utf-8")
    assert "Version A" in archive_a.read_text(encoding="utf-8")
    assert svc.load_tailored_cv_version(
        cv_id, job_id, version_id, db_path=db_path
    ).startswith("# Version A")


def test_tailor_cv_records_a_version_and_publishes_the_honest_score(
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
                "contact": {"name": "Name", "email": "name@example.com"},
                "raw_text": "Name — Technical Support. Python and SQL reporting.",
                "experience": {
                    "job_titles": ["Technical Support"],
                    "years_of_experience_estimate": 2,
                    "seniority_level": "junior",
                },
                "skills": {"programming_languages": ["Python", "SQL"]},
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
            "requirement_extraction": {
                "hard_requirements": [
                    {
                        "requirement": "Python",
                        "candidate_status": "MATCH",
                        "evidence_or_gap": "Python reporting scripts",
                    },
                    {
                        "requirement": "Production backend ownership",
                        "candidate_status": "PARTIAL",
                        "evidence_or_gap": "Support-side exposure only",
                    },
                ],
                "soft_requirements": [
                    {
                        "requirement": "SQL",
                        "candidate_status": "MATCH",
                        "evidence_or_gap": "Daily SQL queries",
                    }
                ],
            },
            "scoring": {
                "hard_score_pct": 70,
                "soft_score_pct": 100,
                "hard_cap_applied": False,
                "realistic_match_score": 95,
                "score_rationale": "Python overlap with partial backend depth.",
            },
            "key_matching_points": ["Python", "SQL"],
            "missing_critical_skills": ["Production backend ownership"],
            "transferable_skills_framing": [],
            "tailored_cv": {
                "summary": "Support engineer moving into backend work.",
                "skills": ["Python", "SQL"],
                "experience": [
                    {
                        "company": "Acme",
                        "title": "Technical Support",
                        "dates": "2023-2025",
                        "bullets": ["Wrote Python reporting jobs over SQL data."],
                    }
                ],
                "projects": [],
                "education": [],
            },
            "recommendation": "APPLY_WITH_HONEST_FRAMING",
        }

    monkeypatch.setattr(match_tailor_service, "call_openai_json", _fake_openai)
    monkeypatch.setattr(match_tailor_service, "is_ai_available", lambda: True)

    def _fake_pipeline(**kwargs):
        raw = _fake_openai("", "")
        from match_tailor_service import normalize_match_tailor_result
        from intelligent_tailor_fixtures import intelligent_report

        normalized = normalize_match_tailor_result(
            raw,
            job_title=str((kwargs.get("job") or {}).get("title") or "Backend Engineer"),
            source_resume_text=str(
                ((kwargs.get("cv_profile") or {}).get("raw_text") or "")
            ),
        )
        report = intelligent_report(
            score=int(normalized["scoring"]["realistic_match_score"]),
            summary=str(normalized["tailored_cv"].get("summary") or ""),
            skills=list(normalized["tailored_cv"].get("skills") or []),
            experience=list(normalized["tailored_cv"].get("experience") or []),
        )
        report["scoring"] = normalized["scoring"]
        report["score_validation"] = normalized["score_validation"]
        report["requirement_extraction"] = normalized["requirement_extraction"]
        report["key_matching_points"] = normalized["key_matching_points"]
        report["missing_critical_skills"] = normalized["missing_critical_skills"]
        report["transferable_skills_framing"] = normalized[
            "transferable_skills_framing"
        ]
        report["tailored_cv"] = normalized["tailored_cv"]
        report["tailored_resume"] = {
            "professional_title": normalized["tailored_cv"].get("professional_title")
            or "",
            "professional_summary": normalized["tailored_cv"].get("summary") or "",
            "summary": normalized["tailored_cv"].get("summary") or "",
            "skills": normalized["tailored_cv"].get("skills") or [],
            "experience": normalized["tailored_cv"].get("experience") or [],
            "projects": normalized["tailored_cv"].get("projects") or [],
            "education": normalized["tailored_cv"].get("education") or [],
            "certifications": [],
        }
        report["recommendation"] = normalized["recommendation"]
        report["realistic_match_score"] = normalized["scoring"]["realistic_match_score"]
        report["tailored_match_score"] = normalized["scoring"]["realistic_match_score"]
        report["original_match_score"] = normalized["scoring"]["realistic_match_score"]
        report["claim_validator_passed"] = True
        return report

    monkeypatch.setattr(svc, "run_intelligent_tailoring", _fake_pipeline)

    job = db.get_job_by_id(job_id, db_path=db_path)
    result = svc.tailor_cv_for_job(
        cv_id,
        job,
        force=True,
        use_cache=False,
        db_path=db_path,
    )

    # First honest evaluation: one score, no invented progression from the scan.
    assert result["score_after"] == 77
    assert result["score_before"] == 77
    assert result["initial_match_score"] == 77
    assert result["estimated_ats_score"] == result["score_after"]
    assert result["version_id"] is not None

    latest = db.get_latest_cv_tailor_version(cv_id, job_id, db_path=db_path)
    assert latest["score_before"] == 77
    assert latest["score_after"] == 77

    # The job card now shows the same number as the tailored CV view.
    match = db.get_cv_job_match(cv_id, job_id, db_path=db_path)
    assert match["match_score"] == 77
    assert match["match_method"] == "match_tailor"
    assert match["ats_score_label"]
    assert json.loads(match["missing_skills"]) == ["Production backend ownership"]
    # The frozen scan baseline is untouched.
    assert match["initial_score"] == 76
    assert db.get_match_baseline_score(cv_id, job_id, db_path=db_path) == 76


def test_persist_mvp_session_fields_and_restore(cvs_dir, db_path, monkeypatch):
    from conftest import register_test_user
    from cv_tailor.job_persist import restore_mvp_session_for_user

    db.init_db(db_path)
    monkeypatch.setattr("config.CVS_DIR", cvs_dir)
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr("config.cv_db_path", lambda _cv_id: db_path)
    monkeypatch.setattr("cv_tailor.job_persist.cv_db_path", lambda _cv_id: db_path)

    user = register_test_user(email="restore@example.com", db_path=db_path)
    cv_id = "cv_restore"
    db.create_cv(
        cv_id,
        user_id=user["id"],
        file_name="resume.pdf",
        display_name="Resume",
        stored_path=str(cvs_dir / "resume.pdf"),
        db_path=db_path,
    )
    job_id = db.insert_job(
        title="Engineer",
        job_url="https://example.com/job/restore",
        company="Acme",
        description="Need Python and FastAPI experience for backend services.",
        db_path=db_path,
    )
    db.upsert_cv_job_match(
        cv_id,
        job_id,
        {"match_score": 70, "match_reason": "test"},
        db_path=db_path,
    )

    tailored = {
        "name": "Jane Doe",
        "summary": "Backend engineer",
        "skills": ["Python"],
        "experience": [],
        "projects": [],
        "education": [],
        "certifications": [],
    }
    analysis = {
        "strong_matches": ["Python"],
        "gaps": [
            {
                "gap_id": "fastapi",
                "title": "FastAPI",
                "requirement": "FastAPI",
                "confirmation_text": "I have FastAPI experience",
                "status": "unsupported",
            }
        ],
    }
    saved = svc.persist_tailored_cv_markdown(
        cv_id,
        job_id,
        "# Jane Doe\n\nBackend engineer",
        db_path=db_path,
        pdf_bytes=b"%PDF-1.4 session",
        mvp_tailored_cv=tailored,
        mvp_job_analysis=analysis,
        mvp_user_confirmed_facts=[],
        mvp_cv_text="Original CV text with Python",
        mvp_model="test-model",
    )
    assert saved["version_id"] is not None

    restored = restore_mvp_session_for_user(
        cv_id=cv_id,
        job_id=job_id,
        user_id=user["id"],
        version_id=saved["version_id"],
    )
    assert restored["result_id"]
    assert restored["restored"] is True
    assert restored["job_analysis"]["gaps"][0]["gap_id"] == "fastapi"
    assert "Jane Doe" in restored["preview_text"]
    assert restored["model"] == "test-model"


def test_delete_tailored_cv_version_promotes_previous(cvs_dir, db_path, monkeypatch):
    from conftest import register_test_user
    from cv_tailor.job_persist import delete_tailored_cv_version_for_user

    db.init_db(db_path)
    monkeypatch.setattr("config.CVS_DIR", cvs_dir)
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr("config.cv_db_path", lambda _cv_id: db_path)
    monkeypatch.setattr("cv_tailor.job_persist.cv_db_path", lambda _cv_id: db_path)

    user = register_test_user(email="delete@example.com", db_path=db_path)
    cv_id = "cv_delete"
    db.create_cv(
        cv_id,
        user_id=user["id"],
        file_name="resume.pdf",
        display_name="Resume",
        stored_path=str(cvs_dir / "resume.pdf"),
        db_path=db_path,
    )
    job_id = db.insert_job(
        title="Engineer",
        job_url="https://example.com/job/delete",
        company="Acme",
        description="Python backend role with APIs and databases required here.",
        db_path=db_path,
    )
    db.upsert_cv_job_match(
        cv_id,
        job_id,
        {"match_score": 65, "match_reason": "test"},
        db_path=db_path,
    )

    first = svc.persist_tailored_cv_markdown(
        cv_id,
        job_id,
        "# Version One",
        db_path=db_path,
        pdf_bytes=b"%PDF-1.4 one",
        mvp_tailored_cv={"name": "One", "summary": "one"},
    )
    second = svc.persist_tailored_cv_markdown(
        cv_id,
        job_id,
        "# Version Two",
        db_path=db_path,
        pdf_bytes=b"%PDF-1.4 two",
        mvp_tailored_cv={"name": "Two", "summary": "two"},
    )

    deleted = delete_tailored_cv_version_for_user(
        cv_id=cv_id,
        job_id=job_id,
        version_id=second["version_id"],
        user_id=user["id"],
    )
    assert deleted["deleted"] is True
    assert deleted["remaining_count"] == 1
    assert deleted["has_tailored_cv"] is True
    assert deleted["latest_version_id"] == first["version_id"]
    assert "Version One" in svc.tailored_cv_path(cv_id, job_id).read_text(encoding="utf-8")
    assert svc.tailored_cv_pdf_path(cv_id, job_id).read_bytes() == b"%PDF-1.4 one"

    deleted_last = delete_tailored_cv_version_for_user(
        cv_id=cv_id,
        job_id=job_id,
        version_id=first["version_id"],
        user_id=user["id"],
    )
    assert deleted_last["remaining_count"] == 0
    assert deleted_last["has_tailored_cv"] is False
    match = db.get_cv_job_match(cv_id, job_id, db_path=db_path)
    assert not match.get("tailored_cv_path")
