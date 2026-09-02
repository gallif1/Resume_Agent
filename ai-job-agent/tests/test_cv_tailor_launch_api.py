"""API tests for CV Tailor launch integration (file + job context)."""

from __future__ import annotations

import api_server
import cv_service
import db
from conftest import auth_header_for, insert_job, register_test_user
from fastapi.testclient import TestClient


def _isolate(monkeypatch, db_path, cvs_dir):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)
    monkeypatch.setattr(cv_service.db, "REGISTRY_DB_PATH", db_path)
    import config as cfg

    monkeypatch.setattr(cfg, "CVS_DIR", cvs_dir)
    monkeypatch.setattr(db, "cv_db_path", lambda cv_id: cvs_dir / cv_id / "jobs.db")


def test_download_cv_file_and_job_context(db_path, cvs_dir, monkeypatch):
    _isolate(monkeypatch, db_path, cvs_dir)
    user = register_test_user(email="tailor-launch@example.com", db_path=db_path)
    client = TestClient(api_server.app)

    pdf_bytes = b"%PDF-1.4 tailor-launch-resume"
    cv = cv_service.upload_cv("MyCV.pdf", pdf_bytes, user_id=user["id"], db_path=db_path)
    cv_id = cv["id"]
    cv_db = cvs_dir / cv_id / "jobs.db"
    db.init_db(cv_db)
    job_id = insert_job(
        cv_db,
        title="Backend Engineer",
        url="https://example.com/jobs/1",
        company="Example Ltd",
    )
    with db.get_connection(cv_db) as conn:
        conn.execute(
            "UPDATE jobs SET full_description = ? WHERE id = ?",
            ("We need Python and FastAPI experience for this role.", job_id),
        )
        conn.commit()

    file_res = client.get(f"/cvs/{cv_id}/file", headers=auth_header_for(user))
    assert file_res.status_code == 200, file_res.text
    assert file_res.content == pdf_bytes
    assert "MyCV.pdf" in (file_res.headers.get("Content-Disposition") or "")

    ctx_res = client.get(
        f"/cvs/{cv_id}/jobs/{job_id}/context",
        headers=auth_header_for(user),
    )
    assert ctx_res.status_code == 200, ctx_res.text
    ctx = ctx_res.json()
    assert ctx["title"] == "Backend Engineer"
    assert ctx["company"] == "Example Ltd"
    assert "Python and FastAPI" in ctx["description"]
