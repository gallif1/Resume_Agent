"""Browser integration tests against local HTML fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_apply.engine import apply_to_job
from job_apply.models import Applicant, ApplyRequest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CV_PDF = FIXTURES / "sample_cv.pdf"


@pytest.fixture(scope="session", autouse=True)
def _ensure_sample_cv() -> None:
    if not CV_PDF.exists():
        # Minimal valid-enough PDF bytes for file upload tests.
        CV_PDF.write_bytes(
            b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        )


def _file_url(name: str) -> str:
    return FIXTURES.joinpath(name).resolve().as_uri()


def test_apply_english_form_submits():
    result = apply_to_job(
        ApplyRequest(
            job_url=_file_url("apply_form.html"),
            cv_path=CV_PDF,
            applicant=Applicant(
                first_name="Jane",
                last_name="Doe",
                email="jane@example.com",
                phone="0501234567",
            ),
            dry_run=False,
            headless=True,
        )
    )
    assert result.success is True
    assert result.status == "submitted"
    assert "first_name" in result.filled_fields
    assert "last_name" in result.filled_fields
    assert "email" in result.filled_fields
    assert "cv_file" in result.filled_fields
    assert result.confirmation_text


def test_apply_dry_run_skips_submit():
    result = apply_to_job(
        ApplyRequest(
            job_url=_file_url("apply_form.html"),
            cv_path=CV_PDF,
            applicant=Applicant(
                first_name="Jane",
                last_name="Doe",
                email="jane@example.com",
                phone="0501234567",
            ),
            dry_run=True,
            headless=True,
        )
    )
    assert result.success is True
    assert result.status == "filled"
    assert "ניסיון" in result.message or "dry" in result.message.lower()


def test_comeet_style_iframe_form_submits():
    result = apply_to_job(
        ApplyRequest(
            job_url=_file_url("comeet_iframe_apply.html"),
            cv_path=CV_PDF,
            applicant=Applicant(
                first_name="Gal",
                last_name="Lifshitz",
                email="gal@example.com",
                phone="0523527293",
            ),
            dry_run=False,
            headless=True,
        )
    )
    assert result.success is True
    assert result.status == "submitted"
    assert "first_name" in result.filled_fields
    assert "email" in result.filled_fields
    assert "cv_file" in result.filled_fields


def test_hebrew_apply_entry_then_submit():
    result = apply_to_job(
        ApplyRequest(
            job_url=_file_url("hebrew_apply_entry.html"),
            cv_path=CV_PDF,
            applicant=Applicant(
                first_name="ישראל",
                last_name="ישראלי",
                email="israel@example.com",
                phone="0529998877",
            ),
            dry_run=False,
            headless=True,
        )
    )
    assert result.success is True
    assert result.status == "submitted"
    assert "first_name" in result.filled_fields
    assert "email" in result.filled_fields


def test_missing_cv_fails_fast():
    result = apply_to_job(
        ApplyRequest(
            job_url=_file_url("apply_form.html"),
            cv_path=Path("/tmp/does-not-exist-cv.pdf"),
            applicant=Applicant("A", "B", "a@b.com", "1"),
            headless=True,
        )
    )
    assert result.success is False
    assert result.failure_category == "cv_missing"
