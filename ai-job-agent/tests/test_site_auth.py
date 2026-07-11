"""Tests for automated site login helpers."""

from __future__ import annotations

from site_auth import cv_browser_profile_dir
from site_credentials import linkedin_credentials_configured


def test_cv_browser_profile_dir_creates_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("site_auth.cv_data_dir", lambda cv_id: tmp_path / cv_id)
    path = cv_browser_profile_dir("cv-test")
    assert path.is_dir()
    assert path.name == "browser_profile"


def test_linkedin_credentials_configured(monkeypatch):
    monkeypatch.setattr("site_credentials.LINKEDIN_EMAIL", "")
    monkeypatch.setattr("site_credentials.LINKEDIN_PASSWORD", "")
    assert linkedin_credentials_configured() is False
    monkeypatch.setattr("site_credentials.LINKEDIN_EMAIL", "user@example.com")
    monkeypatch.setattr("site_credentials.LINKEDIN_PASSWORD", "secret")
    assert linkedin_credentials_configured() is True
