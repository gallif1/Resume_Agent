"""Tests for per-CV site credentials."""

from __future__ import annotations

from site_credentials import (
    get_drushim_credentials,
    get_linkedin_credentials,
    linkedin_credentials_configured,
    load_site_credentials,
    public_site_credentials,
    update_site_credentials,
)


def test_update_and_load_site_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("site_credentials.cv_data_dir", lambda cv_id: tmp_path / cv_id)

    public = update_site_credentials(
        "cv-1",
        linkedin={"email": "user@example.com", "password": "secret123"},
        drushim={"email": "0501234567", "password": "drushim-pass"},
    )
    assert public["linkedin"]["configured"] is True
    assert public["linkedin"]["password_set"] is True
    assert public["linkedin"]["email"] == "user@example.com"
    assert "password" not in public["linkedin"]

    stored = load_site_credentials("cv-1")
    assert stored["linkedin"]["password"] == "secret123"

    email, password = get_linkedin_credentials("cv-1")
    assert email == "user@example.com"
    assert password == "secret123"


def test_password_preserved_when_empty_on_update(tmp_path, monkeypatch):
    monkeypatch.setattr("site_credentials.cv_data_dir", lambda cv_id: tmp_path / cv_id)
    update_site_credentials(
        "cv-1",
        linkedin={"email": "user@example.com", "password": "keep-me"},
    )

    update_site_credentials(
        "cv-1",
        linkedin={"email": "new@example.com", "password": ""},
    )

    stored = load_site_credentials("cv-1")
    assert stored["linkedin"]["email"] == "new@example.com"
    assert stored["linkedin"]["password"] == "keep-me"


def test_public_site_credentials_never_exposes_password(tmp_path, monkeypatch):
    monkeypatch.setattr("site_credentials.cv_data_dir", lambda cv_id: tmp_path / cv_id)
    update_site_credentials(
        "cv-1",
        linkedin={"email": "user@example.com", "password": "hidden"},
    )
    public = public_site_credentials("cv-1")
    assert public["linkedin"]["password_set"] is True
    assert "password" not in public["linkedin"]


def test_linkedin_credentials_configured_per_cv(tmp_path, monkeypatch):
    monkeypatch.setattr("site_credentials.cv_data_dir", lambda cv_id: tmp_path / cv_id)
    monkeypatch.setattr("site_credentials.LINKEDIN_EMAIL", "")
    monkeypatch.setattr("site_credentials.LINKEDIN_PASSWORD", "")
    assert linkedin_credentials_configured("cv-1") is False
    update_site_credentials(
        "cv-1",
        linkedin={"email": "user@example.com", "password": "secret"},
    )
    assert linkedin_credentials_configured("cv-1") is True


def test_env_fallback_for_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("site_credentials.cv_data_dir", lambda cv_id: tmp_path / cv_id)
    monkeypatch.setattr("site_credentials.LINKEDIN_EMAIL", "env@example.com")
    monkeypatch.setattr("site_credentials.LINKEDIN_PASSWORD", "env-pass")
    monkeypatch.setattr("site_credentials.DRUSHIM_EMAIL", "drushim@example.com")
    monkeypatch.setattr("site_credentials.DRUSHIM_PASSWORD", "d-pass")
    email, password = get_linkedin_credentials("cv-empty")
    assert email == "env@example.com"
    assert password == "env-pass"
    email, password = get_drushim_credentials("cv-empty")
    assert email == "drushim@example.com"
    assert password == "d-pass"
