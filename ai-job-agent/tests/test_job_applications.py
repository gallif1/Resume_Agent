"""Tests for automated job application feature."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

import api_server
import application_service
import db
from application_providers import select_provider
from application_providers.greenhouse_provider import GreenhouseProvider
from application_providers.provider_utils import detect_captcha, detect_login_required, detect_submission_success
from field_mapper import build_profile_values, match_field_key, normalize_label


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "html"


def _create_cv(registry_db: Path, cvs_dir: Path, cv_id: str | None = None) -> str:
    cv_id = cv_id or uuid.uuid4().hex
    cv_dir = cvs_dir / cv_id
    cv_dir.mkdir(parents=True, exist_ok=True)
    (cv_dir / "resume.pdf").write_bytes(b"%PDF-1.4 test")
    profile = {
        "contact": {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "0501234567",
            "location": "Tel Aviv",
            "linkedin": "https://linkedin.com/in/jane",
        },
        "experience": {"job_titles": ["Developer"]},
        "skills": {"prog": ["Python", "SQL"]},
    }
    (cv_dir / "cv_profile.json").write_text(
        json.dumps(profile, ensure_ascii=False), encoding="utf-8"
    )
    db.create_cv(
        cv_id,
        file_name="resume.pdf",
        stored_path=str(cv_dir / "resume.pdf"),
        display_name="Jane",
        file_ext=".pdf",
        parsed_profile=json.dumps(profile),
        db_path=registry_db,
    )
    return cv_id


def _link_match(registry_db: Path, cv_id: str, job_id: int) -> None:
    data_db = db.cv_db_path(cv_id)
    db.upsert_cv_job_match(cv_id, job_id, {"match_score": 80}, db_path=data_db)


# --- Field normalization -----------------------------------------------------

def test_normalize_label_strips_and_lowercases():
    assert normalize_label("  First Name  ") == "first name"
    assert normalize_label("דוא\"ל") == 'דוא"ל'


def test_match_field_key_english_and_hebrew():
    assert match_field_key("email address") == "email"
    assert match_field_key("שם פרטי") == "first_name"
    assert match_field_key("טלפון נייד") == "phone"
    assert match_field_key("random field") is None


def test_build_profile_values_splits_name_and_skills():
    profile = {
        "contact": {"name": "Jane Doe", "email": "j@x.com", "phone": "123"},
        "experience": {"job_titles": ["Engineer"]},
        "skills": {"prog": ["Python", "Go"]},
        "preferences": {"salary_expectations": "25000 ILS"},
    }
    values = build_profile_values(profile)
    assert values["first_name"] == "Jane"
    assert values["last_name"] == "Doe"
    assert values["email"] == "j@x.com"
    assert "Python" in values["skills"]
    assert values["salary"] == "25000 ILS"
    assert "work_authorization" not in values


# --- Provider selection ------------------------------------------------------

def test_provider_selection_greenhouse():
    provider = select_provider("https://boards.greenhouse.io/acme/jobs/123")
    assert provider.name == "greenhouse"


def test_provider_selection_drushim():
    provider = select_provider("https://www.drushim.co.il/job/123/")
    assert provider.name == "drushim"


def test_provider_selection_bullhorn():
    provider = select_provider("https://www.bullhorn.com/apply/job-123")
    assert provider.name == "bullhorn"


def test_bullhorn_form_fill(playwright_page, tmp_path):
    from application_providers.bullhorn_provider import BullhornProvider

    cv_file = tmp_path / "cv.pdf"
    cv_file.write_bytes(b"%PDF-1.4")

    html = (FIXTURES / "bullhorn_apply_form.html").read_text(encoding="utf-8")
    playwright_page.set_content(html)

    profile = {
        "contact": {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "0501234567",
            "portfolio": "https://jane.dev",
        }
    }
    provider = BullhornProvider()
    job = {"title": "Backend Developer", "company": "888", "job_url": "https://www.bullhorn.com/apply/x"}
    result = provider.fill_application(
        playwright_page, profile, str(cv_file), job
    )
    assert result.success is True
    assert "full_name" in result.filled_fields or "email" in result.filled_fields


def test_linkedin_external_apply_navigation(playwright_page, tmp_path):
    from application_providers.linkedin_provider import LinkedInProvider

    cv_file = tmp_path / "cv.pdf"
    cv_file.write_bytes(b"%PDF-1.4")
    bullhorn_html = (FIXTURES / "bullhorn_apply_form.html").read_text(encoding="utf-8")

    def handle_route(route):
        route.fulfill(body=bullhorn_html, content_type="text/html")

    playwright_page.route("**/apply.test.example/**", handle_route)

    linkedin_html = (FIXTURES / "linkedin_external_apply.html").read_text(encoding="utf-8")
    playwright_page.set_content(linkedin_html)

    profile = {
        "contact": {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "0501234567",
        }
    }
    provider = LinkedInProvider()
    job = {
        "title": "Backend Developer",
        "company": "888",
        "job_url": "https://www.linkedin.com/jobs/view/123",
    }
    result = provider.fill_application(
        playwright_page, profile, str(cv_file), job
    )
    assert result.success is True
    assert result.provider_name and result.provider_name.startswith("linkedin->")
    assert "email" in result.filled_fields or "full_name" in result.filled_fields


def test_greenhouse_can_handle():
    assert GreenhouseProvider().can_handle("https://boards.greenhouse.io/x")


# --- Database ----------------------------------------------------------------

def test_job_application_status_transitions(db_path, cvs_dir, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    cv_id = _create_cv(db_path, cvs_dir)
    job_id = db.insert_job("Dev", "https://example.com/job/1", db_path=db.cv_db_path(cv_id))
    app_id = uuid.uuid4().hex
    db.create_job_application(app_id, cv_id, job_id, db_path=db.cv_db_path(cv_id))

    db.update_job_application(
        app_id,
        {"status": db.JOB_APP_IN_PROGRESS, "started_at": "t1"},
        db_path=db.cv_db_path(cv_id),
    )
    db.add_job_application_step(
        app_id, "opening_job_page", db.STEP_SUCCESS, db_path=db.cv_db_path(cv_id)
    )
    db.update_job_application(
        app_id,
        {"status": db.JOB_APP_SUBMITTED, "submitted_at": "t2", "completed_at": "t2"},
        db_path=db.cv_db_path(cv_id),
    )

    app = db.get_job_application(app_id, db_path=db.cv_db_path(cv_id))
    assert app["status"] == db.JOB_APP_SUBMITTED
    steps = db.get_job_application_steps(app_id, db_path=db.cv_db_path(cv_id))
    assert len(steps) == 1


# --- Authorization & isolation -----------------------------------------------

def test_validate_cv_owns_job_rejects_foreign_job(db_path, cvs_dir, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    cv_a = _create_cv(db_path, cvs_dir)
    cv_b = _create_cv(db_path, cvs_dir)
    job_id = db.insert_job("Dev", "https://example.com/job/2", db_path=db.cv_db_path(cv_a))
    _link_match(db_path, cv_a, job_id)

    with pytest.raises(application_service.ApplicationError) as exc:
        application_service.validate_cv_owns_job(cv_b, job_id, db.cv_db_path(cv_b))
    assert exc.value.status_code == 403


def test_duplicate_application_prevention(db_path, cvs_dir, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    cv_id = _create_cv(db_path, cvs_dir)
    job_id = db.insert_job("Dev", "https://example.com/job/3", db_path=db.cv_db_path(cv_id))
    _link_match(db_path, cv_id, job_id)

    app_id = uuid.uuid4().hex
    db.create_job_application(
        app_id, cv_id, job_id, db_path=db.cv_db_path(cv_id)
    )
    db.update_job_application(
        app_id,
        {"status": db.JOB_APP_SUBMITTED},
        db_path=db.cv_db_path(cv_id),
    )

    with pytest.raises(application_service.ApplicationError) as exc:
        application_service.check_duplicate_application(
            cv_id, job_id, force=False, db_path=db.cv_db_path(cv_id)
        )
    assert exc.value.code == "duplicate_application"


def test_start_application_with_force_after_duplicate(db_path, cvs_dir, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    cv_id = _create_cv(db_path, cvs_dir)
    job_id = db.insert_job("Dev", "https://example.com/job/4", db_path=db.cv_db_path(cv_id))
    _link_match(db_path, cv_id, job_id)

    app_id = uuid.uuid4().hex
    db.create_job_application(app_id, cv_id, job_id, db_path=db.cv_db_path(cv_id))
    db.update_job_application(
        app_id, {"status": db.JOB_APP_SUBMITTED}, db_path=db.cv_db_path(cv_id)
    )

    result = application_service.start_application(
        cv_id, job_id, force=True, db_path=db.cv_db_path(cv_id)
    )
    assert result["application"]["attempt_number"] == 2


def test_cv_profile_isolation(db_path, cvs_dir, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    cv_a = _create_cv(db_path, cvs_dir)
    cv_b = _create_cv(db_path, cvs_dir)
    job_id = db.insert_job("Dev", "https://example.com/job/5", db_path=db.cv_db_path(cv_a))

    app_a = uuid.uuid4().hex
    db.create_job_application(app_a, cv_a, job_id, db_path=db.cv_db_path(cv_a))

    app = db.get_job_application(app_a, db_path=db.cv_db_path(cv_a))
    assert app is not None
    assert db.get_job_application(app_a, db_path=db.cv_db_path(cv_b)) is None


# --- API ---------------------------------------------------------------------

def test_api_apply_endpoint(db_path, cvs_dir, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    import config
    monkeypatch.setattr(config, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    monkeypatch.setattr(api_server, "enqueue_application", lambda *a, **k: True)
    monkeypatch.setattr(api_server, "_playwright_browser_ready", lambda: (True, None))

    cv_id = _create_cv(db_path, cvs_dir)
    job_id = db.insert_job("Dev", "https://example.com/job/6", db_path=db.cv_db_path(cv_id))
    _link_match(db_path, cv_id, job_id)

    from fastapi.testclient import TestClient

    client = TestClient(api_server.app)
    res = client.post(f"/cvs/{cv_id}/jobs/{job_id}/apply", json={"force": False})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == db.JOB_APP_PENDING
    assert "application_id" in body


def test_api_apply_rejects_unauthorized_job(db_path, cvs_dir, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    import config
    monkeypatch.setattr(config, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    monkeypatch.setattr(api_server, "_playwright_browser_ready", lambda: (True, None))
    cv_id = _create_cv(db_path, cvs_dir)
    job_id = db.insert_job("Dev", "https://example.com/job/7", db_path=db.cv_db_path(cv_id))

    from fastapi.testclient import TestClient

    client = TestClient(api_server.app)
    res = client.post(f"/cvs/{cv_id}/jobs/{job_id}/apply", json={"force": False})
    assert res.status_code == 403


def test_match_public_includes_job_application():
    match = {
        "match_id": 1,
        "job_id": 2,
        "job_application": {
            "application_id": "abc",
            "status": "submitted",
            "submitted_at": "t",
        },
    }
    out = api_server._match_public(match)
    assert out["job_application"]["status"] == "submitted"


# --- Playwright detection (mocked HTML) --------------------------------------

@pytest.fixture
def playwright_page():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def test_captcha_detection(playwright_page):
    html = (FIXTURES / "captcha_page.html").read_text(encoding="utf-8")
    playwright_page.set_content(html)
    assert detect_captcha(playwright_page) is True


def test_hidden_recaptcha_script_does_not_trigger_captcha(playwright_page):
    """Job pages often embed reCAPTCHA scripts without a visible challenge."""
    html = (FIXTURES / "job_page_with_hidden_recaptcha.html").read_text(encoding="utf-8")
    playwright_page.set_content(html)
    assert detect_captcha(playwright_page) is False


def test_login_required_detection(playwright_page):
    html = (FIXTURES / "login_page.html").read_text(encoding="utf-8")
    playwright_page.set_content(html)
    assert detect_login_required(playwright_page) is True


def test_submission_success_detection(playwright_page):
    html = (FIXTURES / "success_confirmation.html").read_text(encoding="utf-8")
    playwright_page.set_content(html)
    ok, snippet = detect_submission_success(playwright_page)
    assert ok is True
    assert "thank you" in snippet.lower() or "successfully" in snippet.lower()


def test_generic_form_fill_and_validate(playwright_page, tmp_path):
    from application_providers.generic_provider import GenericProvider

    cv_file = tmp_path / "cv.pdf"
    cv_file.write_bytes(b"%PDF-1.4")

    html = (FIXTURES / "generic_apply_form.html").read_text(encoding="utf-8")
    playwright_page.set_content(html)

    profile = {
        "contact": {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "0501234567",
        }
    }
    provider = GenericProvider()
    job = {"title": "Engineer", "company": "Acme", "job_url": "https://example.com"}
    result = provider.fill_application(
        playwright_page, profile, str(cv_file), job
    )
    assert result.success is True
    assert "first_name" in result.filled_fields or "email" in result.filled_fields

    validation = provider.validate_before_submit(playwright_page)
    assert validation.cv_attached is True


def test_missing_required_field_blocks_validation(playwright_page):
    from application_providers.generic_provider import GenericProvider

    html = (FIXTURES / "generic_apply_form.html").read_text(encoding="utf-8")
    playwright_page.set_content(html)
    provider = GenericProvider()
    validation = provider.validate_before_submit(playwright_page)
    assert validation.valid is False
    assert validation.missing_required


def test_rate_limiting():
    cv_id = "rate-test-cv"
    application_service._apply_rate_log.clear()
    for _ in range(application_service.RATE_LIMIT_MAX_REQUESTS):
        application_service.check_rate_limit(cv_id)
    with pytest.raises(application_service.ApplicationError) as exc:
        application_service.check_rate_limit(cv_id)
    assert exc.value.status_code == 429


def test_api_apply_rejects_when_playwright_unavailable(db_path, cvs_dir, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    import config
    monkeypatch.setattr(config, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    monkeypatch.setattr(
        api_server,
        "_playwright_browser_ready",
        lambda: (False, "chromium missing"),
    )

    cv_id = _create_cv(db_path, cvs_dir)
    job_id = db.insert_job("Dev", "https://example.com/job/8", db_path=db.cv_db_path(cv_id))
    _link_match(db_path, cv_id, job_id)

    from fastapi.testclient import TestClient

    client = TestClient(api_server.app)
    res = client.post(f"/cvs/{cv_id}/jobs/{job_id}/apply", json={"force": False})
    assert res.status_code == 503
