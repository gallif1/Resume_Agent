"""Headless fallback + JSON error wrapping for job-apply."""

from __future__ import annotations

from pathlib import Path

import api_server
from fastapi.testclient import TestClient


def test_resolve_headless_forces_when_no_display(monkeypatch):
    import job_apply.browser as browser

    monkeypatch.setenv("JOB_APPLY_FORCE_HEADLESS", "1")
    monkeypatch.delenv("JOB_APPLY_FORCE_HEADED", raising=False)
    headless, note = browser.resolve_headless(False)
    assert headless is True
    assert note


def test_apply_endpoint_returns_json_on_engine_crash(tmp_path: Path, monkeypatch):
    """Unhandled engine exceptions must become JSON 500, not HTML/empty bodies."""
    client = TestClient(api_server.app, raise_server_exceptions=False)

    def boom(_request):
        raise RuntimeError("simulated chromium crash")

    # Patch after path ensure so the handler's import sees our stub.
    import job_apply.engine as engine

    monkeypatch.setattr(engine, "apply_to_job", boom)

    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    res = client.post(
        "/api/job-apply/apply",
        data={
            "job_url": "https://example.com/job",
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "phone": "0500000000",
            "dry_run": "true",
            "headless": "false",
        },
        files={"cv": ("cv.pdf", cv.read_bytes(), "application/pdf")},
    )
    assert res.status_code == 500
    body = res.json()
    assert body.get("success") is False
    assert "chromium" in body.get("message", "").lower() or "הגשה" in body.get("message", "")
    assert body.get("failure_category") == "server_error"
