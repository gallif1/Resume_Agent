"""Regression: POST /api/job-apply/apply must not return HTTP 405.

Previously, when job-apply routes failed to register (or were missing), the SPA
catch-all ``@app.get("/{page_path:path}")`` matched the path and Starlette
answered POST with Method Not Allowed (405).
"""

from __future__ import annotations

from pathlib import Path

import api_server
from fastapi.testclient import TestClient


def test_job_apply_health_registered():
    client = TestClient(api_server.app)
    res = client.get("/api/job-apply/health")
    assert res.status_code == 200
    body = res.json()
    assert body.get("service") == "job-apply-automation"
    assert body.get("package_found") in {"true", "false"}


def test_job_apply_apply_not_405(tmp_path: Path):
    """POST must hit the apply handler (not SPA) — never bare 405."""
    client = TestClient(api_server.app)
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
            "headless": "true",
        },
        files={"cv": ("cv.pdf", cv.read_bytes(), "application/pdf")},
    )
    # 405 = SPA catch-all bug. Any other status means the POST route matched.
    assert res.status_code != 405, (
        f"POST /api/job-apply/apply returned 405 — SPA catch-all is intercepting. "
        f"body={res.text[:200]!r}"
    )


def test_no_get_catch_all_route_on_app():
    """Ensure we never reintroduce a GET /{path} catch-all that causes 405 on POST."""
    for route in api_server.app.routes:
        path = getattr(route, "path", "") or ""
        methods = getattr(route, "methods", None) or set()
        if path in {"/{page_path:path}", "/{full_path:path}", "/{path:path}"}:
            assert "GET" not in methods or path.startswith("/api"), (
                f"Dangerous SPA catch-all route still registered: {path} methods={methods}"
            )
