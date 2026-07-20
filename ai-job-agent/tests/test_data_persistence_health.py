"""Tests for deploy persistence health signals."""

from __future__ import annotations

import os

import pytest

import api_server


def test_data_dir_looks_persistent_respects_env_override(monkeypatch):
    monkeypatch.setenv("DATA_PERSISTENT", "true")
    assert api_server._data_dir_looks_persistent() is True

    monkeypatch.setenv("DATA_PERSISTENT", "false")
    assert api_server._data_dir_looks_persistent() is False


def test_health_includes_persistence_fields(monkeypatch):
    monkeypatch.setenv("DATA_PERSISTENT", "true")
    monkeypatch.setattr(api_server, "_playwright_browser_ready", lambda: (True, None))

    payload = pytest.importorskip("asyncio").run(api_server.health())

    assert payload["ok"] is True
    assert payload["data_persistent"] is True
    assert "data_dir" in payload
    assert "registry_db_exists" in payload
