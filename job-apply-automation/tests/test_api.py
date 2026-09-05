"""API smoke tests (no browser for health; apply uses fixture)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from job_apply.api import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CV_PDF = FIXTURES / "sample_cv.pdf"


def test_health():
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_apply_endpoint_dry_run(tmp_path: Path):
    if not CV_PDF.exists():
        CV_PDF.write_bytes(b"%PDF-1.4\n%%EOF\n")

    client = TestClient(app)
    with CV_PDF.open("rb") as fh:
        res = client.post(
            "/apply",
            data={
                "job_url": FIXTURES.joinpath("apply_form.html").resolve().as_uri(),
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
                "phone": "0501234567",
                "dry_run": "true",
                "headless": "true",
            },
            files={"cv": ("cv.pdf", fh, "application/pdf")},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["status"] == "filled"
