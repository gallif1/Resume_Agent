"""Tests for the standalone CV Tailor MVP module."""

from __future__ import annotations

import io
from unittest.mock import patch

import api_server
import auth
import db
from conftest import auth_header_for, register_test_user
from cv_tailor.models import ExperienceEntry, TailoredCvData
from cv_tailor.parser import CvParseError, parse_cv_bytes, sanitize_filename, validate_extension
from cv_tailor.renderer import render_tailored_cv_docx
from cv_tailor.service import CvTailorError, generate_tailored_cv, get_download_docx
from docx import Document
from fastapi.testclient import TestClient


def test_sanitize_filename_strips_unsafe_chars():
    assert sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert sanitize_filename("") == "cv"


def test_validate_extension_rejects_unknown():
    try:
        validate_extension("resume.txt")
        assert False, "expected CvParseError"
    except CvParseError as exc:
        assert "Unsupported" in str(exc)


def test_parse_cv_bytes_rejects_empty():
    try:
        parse_cv_bytes(b"", "cv.pdf")
        assert False, "expected CvParseError"
    except CvParseError as exc:
        assert "empty" in str(exc).lower()


def test_tailored_cv_preview_text():
    cv = TailoredCvData(
        name="Jane Doe",
        summary="Backend engineer with Python experience.",
        skills=["Python", "FastAPI"],
        experience=[
            ExperienceEntry(
                company="Acme",
                role="Engineer",
                dates="2020–2024",
                bullets=["Built APIs"],
            )
        ],
    )
    preview = cv.to_preview_text()
    assert "Jane Doe" in preview
    assert "Python" in preview
    assert "Acme" in preview


def test_render_tailored_cv_docx_produces_valid_docx():
    cv = TailoredCvData(
        name="Jane Doe",
        summary="Experienced developer.",
        skills=["Python"],
    )
    data = render_tailored_cv_docx(cv)
    assert data.startswith(b"PK")
    doc = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Jane Doe" in text
    assert "Experienced developer." in text


def test_api_cv_tailor_generate_and_download(db_path, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)

    user = register_test_user(email="tailor@example.com", db_path=db_path)
    client = TestClient(api_server.app)

    mock_llm = {
        "name": "Jane Doe",
        "contact": "jane@example.com",
        "summary": "Python backend engineer aligned with the role.",
        "skills": ["Python", "FastAPI"],
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Software Engineer",
                "dates": "2020–2024",
                "bullets": ["Built REST APIs with Python"],
            }
        ],
        "projects": [],
        "education": [],
        "certifications": [],
    }

    with patch("cv_tailor.service.call_openai_json", return_value=mock_llm):
        with patch(
            "cv_tailor.parser.extract_text_from_resume",
            return_value=("Jane Doe\nSoftware Engineer at Acme Corp\nPython, FastAPI", "docx"),
        ):
            res = client.post(
                "/api/cv-tailor/generate",
                headers=auth_header_for(user),
                files={"file": ("resume.docx", b"fake-docx-bytes", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                data={"job_description": "We need a Python backend engineer with FastAPI experience."},
            )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["result_id"]
    assert "Python" in body["preview_text"]
    assert body["tailored_cv"]["name"] == "Jane Doe"

    download = client.get(
        f"/api/cv-tailor/download/{body['result_id']}",
        headers=auth_header_for(user),
    )
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert download.content.startswith(b"PK")


def test_api_cv_tailor_requires_auth(db_path, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    client = TestClient(api_server.app)
    res = client.post(
        "/api/cv-tailor/generate",
        files={"file": ("resume.pdf", b"%PDF", "application/pdf")},
        data={"job_description": "Python engineer role with backend APIs."},
    )
    assert res.status_code == 401


def test_generate_tailored_cv_rejects_short_job_description():
    try:
        generate_tailored_cv(
            file_bytes=b"x",
            filename="cv.pdf",
            job_description="short",
            user_id="user-1",
        )
        assert False, "expected CvTailorError"
    except CvTailorError as exc:
        assert "short" in str(exc).lower()


def test_get_download_docx_wrong_user():
    mock_llm = {
        "summary": "Summary text for tailored CV.",
        "skills": ["Python"],
        "experience": [],
        "projects": [],
        "education": [],
        "certifications": [],
    }
    with patch("cv_tailor.service.call_openai_json", return_value=mock_llm):
        with patch(
            "cv_tailor.parser.extract_text_from_resume",
            return_value=("Long enough CV text " * 5, "pdf:test"),
        ):
            result = generate_tailored_cv(
                file_bytes=b"pdf-bytes",
                filename="cv.pdf",
                job_description="Looking for a Python engineer with API experience.",
                user_id="owner-user",
            )

    try:
        get_download_docx(result_id=result.result_id, user_id="other-user")
        assert False, "expected CvTailorError"
    except CvTailorError:
        pass
