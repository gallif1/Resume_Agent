"""Shared pytest fixtures for the AI Job Agent test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import auth  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A fresh, isolated SQLite database with registry + jobs schema."""
    path = tmp_path / "test.db"
    db.init_registry_db(path)
    db.init_db(path)
    return path


@pytest.fixture
def cvs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect per-CV file storage to a temp directory."""
    directory = tmp_path / "cvs"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "CVS_DIR", directory)
    return directory


def insert_job(db_path: Path, *, title: str, url: str, company: str = "Acme") -> int:
    """Insert a global job and return its id."""
    job_id = db.insert_job(
        title=title,
        job_url=url,
        company=company,
        location="Tel Aviv",
        source="linkedin",
        description="desc",
        db_path=db_path,
    )
    assert job_id is not None
    return job_id


def register_test_user(
    *,
    email: str = "tester@example.com",
    password: str = "secret12",
    db_path: Path | None = None,
) -> dict:
    """Create an auth user in the (optionally overridden) registry DB."""
    path = db_path or db.REGISTRY_DB_PATH
    existing = db.get_user_by_email(email, db_path=path)
    if existing is not None:
        return existing
    return auth.register_user(email, password, db_path=path)


def auth_header_for(user: dict) -> dict[str, str]:
    token = auth.create_access_token(user["id"], user.get("email") or "")
    return {"Authorization": f"Bearer {token}"}


def override_current_user(app, user: dict):
    """Force FastAPI's get_current_user dependency to return ``user``."""

    async def _fake_user():
        return user

    app.dependency_overrides[auth.get_current_user] = _fake_user
    return app


class authed_client:
    """Context manager: TestClient with get_current_user overridden."""

    def __init__(self, user: dict | None = None):
        import api_server

        self.app = api_server.app
        self.user = user or {
            "id": db.DEFAULT_USER_ID,
            "email": "default@local",
            "display_name": "Default User",
        }
        self.client: TestClient | None = None

    def __enter__(self):
        from fastapi.testclient import TestClient

        override_current_user(self.app, self.user)
        self.client = TestClient(self.app)
        return self.client

    def __exit__(self, exc_type, exc, tb):
        self.app.dependency_overrides.pop(auth.get_current_user, None)
        return False


@pytest.fixture
def default_auth_user(db_path: Path) -> dict:
    """Ensure the legacy default user exists for ownership checks."""
    db.init_registry_db(db_path)
    user = db.get_user_by_id(db.DEFAULT_USER_ID, db_path=db_path)
    assert user is not None
    return {
        "id": db.DEFAULT_USER_ID,
        "email": user.get("email") or "default@local",
        "display_name": user.get("display_name") or "Default User",
        "hashed_password": user.get("hashed_password"),
        "created_at": user.get("created_at"),
    }
