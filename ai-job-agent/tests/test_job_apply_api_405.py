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


def test_job_apply_package_found_in_repo_layout():
    """In the monorepo checkout, sibling job-apply-automation must resolve."""
    pkg = api_server._ensure_job_apply_on_path()
    assert pkg is not None
    assert (pkg / "job_apply").is_dir()
    client = TestClient(api_server.app)
    body = client.get("/api/job-apply/health").json()
    assert body["package_found"] == "true"
    assert body.get("package_path")


def test_job_apply_finds_vendored_src_layout(tmp_path: Path, monkeypatch):
    """EC2 --no-build inject vendors job_apply under ai-job-agent/src/job_apply."""
    vendored = tmp_path / "src" / "job_apply"
    vendored.mkdir(parents=True)
    (vendored / "__init__.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(api_server, "PROJECT_ROOT", tmp_path)
    # Hide the real monorepo sibling + docker paths for this unit test.
    real_is_dir = Path.is_dir

    def fake_is_dir(self: Path) -> bool:  # noqa: ANN001
        text = str(self)
        if "job-apply-automation" in text or text.startswith("/app/"):
            return False
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    # Drop any previously imported job_apply so import-fallback does not cheat.
    sys_modules = __import__("sys").modules
    sys_modules.pop("job_apply", None)
    for key in list(sys_modules):
        if key.startswith("job_apply."):
            sys_modules.pop(key, None)

    pkg = api_server._ensure_job_apply_on_path()
    assert pkg == tmp_path / "src"
    assert (pkg / "job_apply").is_dir()


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
