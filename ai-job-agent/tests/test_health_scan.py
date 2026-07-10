"""Health endpoint exposes active scan metadata for UI reconnect."""

from __future__ import annotations

import api_server


def test_health_includes_scan_cv_id_when_scan_running():
    with api_server._scan_lock:
        api_server._scan_state.update(
            {
                "running": True,
                "cv_id": "cv-123",
                "scan_id": None,
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": None,
                "error": None,
                "log": [],
                "current_detail": None,
                "steps": [],
            }
        )

    try:
        import asyncio

        result = asyncio.run(api_server.health())
    finally:
        with api_server._scan_lock:
            api_server._scan_state.update(
                {
                    "running": False,
                    "cv_id": None,
                    "scan_id": None,
                    "started_at": None,
                    "finished_at": None,
                    "error": None,
                    "log": [],
                    "current_detail": None,
                    "steps": [],
                }
            )

    assert result["ok"] is True
    assert result["scan_running"] is True
    assert result["scan_cv_id"] == "cv-123"


def test_health_omits_scan_cv_id_when_idle():
    import asyncio

    result = asyncio.run(api_server.health())

    assert result["ok"] is True
    assert result["scan_running"] is False
    assert result["scan_cv_id"] is None
