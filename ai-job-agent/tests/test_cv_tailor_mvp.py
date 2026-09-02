"""Tests for the standalone CV Tailor MVP module."""

from __future__ import annotations

from unittest.mock import patch

import api_server
import db
from conftest import auth_header_for, register_test_user
from cv_tailor.models import ExperienceEntry, SkillGroup, TailoredCvData
from cv_tailor.parser import CvParseError, parse_cv_bytes, sanitize_filename, validate_extension
from cv_tailor.renderer import pdf_filename_for_cv, render_tailored_cv_pdf, structured_cv_to_html
from cv_tailor.service import CvTailorError, generate_tailored_cv, get_download_pdf
from fastapi.testclient import TestClient


def test_sanitize_filename_strips_unsafe_chars():
    assert sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert sanitize_filename("") == "cv"


def test_validate_extension_rejects_unknown():
    try:
        validate_extension("resume.txt")
        assert False, "expected CvParseError"
    except CvParseError as exc:
        assert "לא נתמך" in str(exc)


def test_parse_cv_bytes_rejects_empty():
    try:
        parse_cv_bytes(b"", "cv.pdf")
        assert False, "expected CvParseError"
    except CvParseError as exc:
        assert "ריק" in str(exc)


def test_tailored_cv_preview_text():
    cv = TailoredCvData(
        name="Jane Doe",
        professional_title="Backend Software Developer",
        summary="Backend engineer with Python experience.",
        skill_groups=[SkillGroup(category="Backend", skills=["Python", "FastAPI"])],
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
    assert "Backend Software Developer" in preview
    assert "Python" in preview
    assert "Acme" in preview


def test_structured_cv_to_html_includes_professional_layout():
    cv = TailoredCvData(
        name="Jane Doe",
        professional_title="Backend Software Developer",
        contact="Israel | jane@example.com",
        summary="Experienced developer.",
        skill_groups=[SkillGroup(category="Backend", skills=["Python"])],
    )
    html_doc = structured_cv_to_html(cv)
    assert "Jane Doe" in html_doc
    assert "Backend Software Developer" in html_doc
    assert "Technical Skills" in html_doc
    assert "2e4a7d" in html_doc


def test_render_tailored_cv_pdf_produces_valid_pdf():
    cv = TailoredCvData(
        name="Jane Doe",
        professional_title="Backend Software Developer",
        summary="Experienced developer.",
        skills=["Python"],
    )
    with patch("playwright.sync_api.sync_playwright") as mock_pw:
        mock_browser = mock_pw.return_value.__enter__.return_value.chromium.launch.return_value
        mock_page = mock_browser.new_page.return_value
        mock_page.pdf.return_value = b"%PDF-1.4 fake pdf content"
        data = render_tailored_cv_pdf(cv)
    assert data.startswith(b"%PDF")
    assert pdf_filename_for_cv(cv) == "Jane_Doe_CV_Tailored.pdf"


def test_api_cv_tailor_generate_and_download(db_path, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)

    user = register_test_user(email="tailor@example.com", db_path=db_path)
    client = TestClient(api_server.app)

    mock_llm = {
        "tailored_cv": {
            "name": "Jane Doe",
            "contact": "jane@example.com",
            "professional_title": "Backend Software Developer",
            "summary": "Python backend engineer aligned with the role.",
            "skill_groups": [{"category": "Backend", "skills": ["Python", "FastAPI"]}],
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
        },
        "job_analysis": {"strong_matches": ["Python"], "gaps": []},
    }

    fake_pdf = b"%PDF-1.4 tailored"

    with patch("cv_tailor.service.call_openai_json", return_value=mock_llm):
        with patch(
            "cv_tailor.parser.extract_text_from_resume",
            return_value=("Jane Doe\nSoftware Engineer at Acme Corp\nPython, FastAPI", "docx"),
        ):
            with patch("cv_tailor.service.render_tailored_cv_pdf", return_value=fake_pdf):
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
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content.startswith(b"%PDF")


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


def test_get_download_pdf_wrong_user():
    mock_llm = {
        "tailored_cv": {
            "summary": "Summary text for tailored CV.",
            "skills": ["Python"],
            "experience": [],
            "projects": [],
            "education": [],
            "certifications": [],
        },
        "job_analysis": {"strong_matches": [], "gaps": []},
    }
    with patch("cv_tailor.service.call_openai_json", return_value=mock_llm):
        with patch(
            "cv_tailor.parser.extract_text_from_resume",
            return_value=("Long enough CV text " * 5, "pdf:test"),
        ):
            with patch("cv_tailor.service.render_tailored_cv_pdf", return_value=b"%PDF-1.4 ok"):
                result = generate_tailored_cv(
                    file_bytes=b"pdf-bytes",
                    filename="cv.pdf",
                    job_description="Looking for a Python engineer with API experience.",
                    user_id="owner-user",
                )

    try:
        get_download_pdf(result_id=result.result_id, user_id="other-user")
        assert False, "expected CvTailorError"
    except CvTailorError:
        pass
