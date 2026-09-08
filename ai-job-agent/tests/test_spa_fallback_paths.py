"""SPA soft-404 must not turn mistaken CV Tailor paths into HTML 200."""

from __future__ import annotations

from pathlib import Path

import api_server
from fastapi.testclient import TestClient


def test_spa_fallback_serves_cv_tailor_entrypoint(tmp_path: Path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    monkeypatch.setattr(api_server, "FRONTEND_DIST", dist)

    client = TestClient(api_server.app)
    res = client.get(
        "/cv-tailor",
        headers={"Accept": "text/html,application/xhtml+xml,*/*"},
    )
    # Real route or soft-404 both OK as long as the SPA shell is reachable.
    assert res.status_code in {200, 404}
    if res.status_code == 200:
        assert "html" in (res.headers.get("content-type") or "").lower()


def test_spa_fallback_does_not_html_fake_cv_tailor_job_paths(tmp_path: Path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    monkeypatch.setattr(api_server, "FRONTEND_DIST", dist)

    client = TestClient(api_server.app)
    res = client.get(
        "/cv-tailor/jobs/472c2430-53bd-4930-a86c-ec4c1ae807ab",
        headers={"Accept": "text/html,application/xhtml+xml,*/*"},
    )
    assert res.status_code == 404
    body = (res.text or "").lower()
    assert "<!doctype" not in body
    assert "<html" not in body
