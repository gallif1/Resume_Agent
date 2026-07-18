"""Tests for job board selection helpers."""

from __future__ import annotations

import pytest

import job_boards


def test_list_job_boards_includes_all_known_sites(monkeypatch):
    monkeypatch.setattr(job_boards, "LINKEDIN_ENABLED", True)
    monkeypatch.setattr(job_boards, "GOTFRIENDS_ENABLED", True)
    monkeypatch.setattr(job_boards, "ALLJOBS_ENABLED", True)
    monkeypatch.setattr(job_boards, "INDEED_ENABLED", True)
    monkeypatch.setattr(job_boards, "SECRET_TEL_AVIV_ENABLED", True)
    monkeypatch.setattr(job_boards, "GEEKTIME_ENABLED", True)
    sites = job_boards.list_job_boards()
    assert [site["id"] for site in sites] == [
        "drushim",
        "linkedin",
        "gotfriends",
        "alljobs",
        "indeed",
        "secret_tel_aviv",
        "geektime",
    ]
    assert all(site["enabled"] for site in sites)


def test_normalize_job_board_ids_defaults_to_enabled(monkeypatch):
    monkeypatch.setattr(job_boards, "LINKEDIN_ENABLED", False)
    monkeypatch.setattr(job_boards, "GOTFRIENDS_ENABLED", True)
    monkeypatch.setattr(job_boards, "ALLJOBS_ENABLED", False)
    monkeypatch.setattr(job_boards, "INDEED_ENABLED", False)
    monkeypatch.setattr(job_boards, "SECRET_TEL_AVIV_ENABLED", False)
    monkeypatch.setattr(job_boards, "GEEKTIME_ENABLED", False)
    assert job_boards.normalize_job_board_ids(None) == ["drushim", "gotfriends"]


def test_normalize_job_board_ids_requires_at_least_one():
    with pytest.raises(ValueError, match="לפחות אתר אחד"):
        job_boards.normalize_job_board_ids([])


def test_normalize_job_board_ids_rejects_unknown_site():
    with pytest.raises(ValueError, match="אתר לא נתמך"):
        job_boards.normalize_job_board_ids(["not-a-real-board"])


def test_normalize_job_board_ids_accepts_aliases(monkeypatch):
    monkeypatch.setattr(job_boards, "SECRET_TEL_AVIV_ENABLED", True)
    monkeypatch.setattr(job_boards, "INDEED_ENABLED", True)
    assert job_boards.normalize_job_board_ids(["sta", "indeed-israel"]) == [
        "secret_tel_aviv",
        "indeed",
    ]


def test_normalize_job_board_ids_rejects_disabled_site(monkeypatch):
    monkeypatch.setattr(job_boards, "LINKEDIN_ENABLED", False)
    with pytest.raises(ValueError, match="לינקדאין"):
        job_boards.normalize_job_board_ids(["linkedin"])
