"""Tests for automated site login helpers."""

from __future__ import annotations

import json

from site_auth import (
    cv_browser_profile_dir,
    import_linkedin_storage_state,
    linkedin_credentials_configured,
    linkedin_storage_state_path,
)


def test_cv_browser_profile_dir_creates_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("site_auth.cv_data_dir", lambda cv_id: tmp_path / cv_id)
    path = cv_browser_profile_dir("cv-test")
    assert path.is_dir()
    assert path.name == "browser_profile"


def test_import_and_apply_linkedin_storage_state(tmp_path, monkeypatch):
    monkeypatch.setattr("site_auth.cv_data_dir", lambda cv_id: tmp_path / cv_id)
    payload = {
        "cookies": [
            {
                "name": "li_at",
                "value": "test-token",
                "domain": ".linkedin.com",
                "path": "/",
            }
        ],
        "origins": [],
    }
    saved = import_linkedin_storage_state("cv-test", payload)
    assert saved == linkedin_storage_state_path("cv-test")
    assert json.loads(saved.read_text(encoding="utf-8"))["cookies"][0]["name"] == "li_at"


def test_linkedin_credentials_configured(monkeypatch):
    monkeypatch.setattr("site_auth.LINKEDIN_EMAIL", "")
    monkeypatch.setattr("site_auth.LINKEDIN_PASSWORD", "")
    assert linkedin_credentials_configured() is False
    monkeypatch.setattr("site_auth.LINKEDIN_EMAIL", "user@example.com")
    monkeypatch.setattr("site_auth.LINKEDIN_PASSWORD", "secret")
    assert linkedin_credentials_configured() is True
