"""Tests for user-level multi-CV database helpers."""

from __future__ import annotations

import db
import cv_service


def test_create_cv_assigns_default_user(db_path):
    cv = db.create_cv(
        "cv-1",
        file_name="a.pdf",
        stored_path="data/cvs/cv-1/resume.pdf",
        db_path=db_path,
    )
    assert cv["user_id"] == db.DEFAULT_USER_ID
    assert cv["is_active"] == 1


def test_list_active_cvs_for_user(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    db.create_cv("cv-b", file_name="b.pdf", stored_path="b", db_path=db_path)
    db.update_cv("cv-b", {"is_active": 0}, db_path=db_path)

    active = db.list_active_cvs_for_user(db_path=db_path)
    assert len(active) == 1
    assert active[0]["id"] == "cv-a"


def test_upload_multiple_cvs_same_user(db_path, cvs_dir):
    cv_a = cv_service.upload_cv("alice.pdf", b"alice-resume", db_path=db_path)
    cv_b = cv_service.upload_cv("bob.pdf", b"bob-resume", db_path=db_path)
    active = db.list_active_cvs_for_user(db_path=db_path)
    assert len(active) == 2
    assert {cv["id"] for cv in active} == {cv_a["id"], cv_b["id"]}
