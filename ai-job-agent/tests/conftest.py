"""Shared pytest fixtures for the AI Job Agent test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

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
