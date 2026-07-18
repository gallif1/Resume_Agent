"""Auth, match sorting, and job description payload tests."""

from __future__ import annotations

import api_server
import auth
import db
from conftest import auth_header_for, authed_client, insert_job, register_test_user
from fastapi.testclient import TestClient


def _match_fields(score: int) -> dict:
    return {
        "match_score": score,
        "match_reason": "reason",
        "match_method": "test",
        "ai_explanation": "AI reason",
    }


def test_register_login_and_me(db_path, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)

    client = TestClient(api_server.app)
    reg = client.post(
        "/api/auth/register",
        json={"email": "Gal@Example.com", "password": "secret12"},
    )
    assert reg.status_code == 200, reg.text
    body = reg.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "gal@example.com"
    token = body["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "gal@example.com"

    login = client.post(
        "/api/auth/login",
        json={"email": "gal@example.com", "password": "secret12"},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]

    bad = client.post(
        "/api/auth/login",
        json={"email": "gal@example.com", "password": "wrong-password"},
    )
    assert bad.status_code == 401


def test_cvs_require_auth(db_path, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)
    client = TestClient(api_server.app)
    assert client.get("/cvs").status_code == 401


def test_users_cannot_see_each_others_cvs(db_path, monkeypatch, cvs_dir):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)
    monkeypatch.setattr(api_server, "_workspace_match_count", lambda *a, **k: 0)

    alice = register_test_user(email="alice@example.com", db_path=db_path)
    bob = register_test_user(email="bob@example.com", db_path=db_path)
    cv = db.create_cv(
        "cv-alice",
        file_name="a.pdf",
        stored_path="a",
        user_id=alice["id"],
        db_path=db_path,
    )

    client = TestClient(api_server.app)
    alice_list = client.get("/cvs", headers=auth_header_for(alice))
    assert alice_list.status_code == 200
    assert [c["id"] for c in alice_list.json()["cvs"]] == [cv["id"]]

    bob_list = client.get("/cvs", headers=auth_header_for(bob))
    assert bob_list.status_code == 200
    assert bob_list.json()["cvs"] == []

    bob_get = client.get(f"/cvs/{cv['id']}", headers=auth_header_for(bob))
    assert bob_get.status_code == 404


def test_match_sorting_by_site_and_score(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    j_drushim = insert_job(db_path, title="D", url="https://x/d")
    j_linkedin = insert_job(db_path, title="L", url="https://x/l")
    j_got = insert_job(db_path, title="G", url="https://x/g")

    with db.get_connection(db_path) as conn:
        conn.execute("UPDATE jobs SET source = ? WHERE id = ?", ("drushim", j_drushim))
        conn.execute("UPDATE jobs SET source = ? WHERE id = ?", ("linkedin", j_linkedin))
        conn.execute("UPDATE jobs SET source = ? WHERE id = ?", ("gotfriends", j_got))
        conn.commit()

    scan = db.create_scan("cv-a", db_path=db_path)
    db.upsert_cv_job_match("cv-a", j_drushim, _match_fields(50), scan_id=scan, db_path=db_path)
    db.upsert_cv_job_match("cv-a", j_linkedin, _match_fields(90), scan_id=scan, db_path=db_path)
    db.upsert_cv_job_match("cv-a", j_got, _match_fields(70), scan_id=scan, db_path=db_path)

    by_score = db.get_cv_matches(
        "cv-a", latest_only=True, sort_by="score", order="desc", db_path=db_path
    )
    assert [m["job_id"] for m in by_score] == [j_linkedin, j_got, j_drushim]

    by_site = db.get_cv_matches(
        "cv-a", latest_only=True, sort_by="site", order="asc", db_path=db_path
    )
    assert [m["source"] for m in by_site] == ["drushim", "gotfriends", "linkedin"]


def test_match_payload_includes_full_description(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    job_id = insert_job(db_path, title="Dev", url="https://x/desc")
    with db.get_connection(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET description = ?, full_description = ? WHERE id = ?",
            ("short", "תיאור מלא של המשרה\nשורה שנייה", job_id),
        )
        conn.commit()
    scan = db.create_scan("cv-a", db_path=db_path)
    db.upsert_cv_job_match("cv-a", job_id, _match_fields(80), scan_id=scan, db_path=db_path)

    rows = db.get_cv_matches("cv-a", latest_only=True, db_path=db_path)
    public = api_server._match_public(rows[0])
    assert "תיאור מלא" in public["description"]
    assert "שורה שנייה" in public["description"]


def test_password_hash_roundtrip():
    hashed = auth.hash_password("secret12")
    assert hashed != "secret12"
    assert auth.verify_password("secret12", hashed)
    assert not auth.verify_password("nope", hashed)


def test_authed_client_lists_empty(db_path, monkeypatch):
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)
    monkeypatch.setattr(api_server, "_workspace_match_count", lambda *a, **k: 0)
    with authed_client() as client:
        res = client.get("/cvs")
    assert res.status_code == 200
    assert res.json()["cvs"] == []
