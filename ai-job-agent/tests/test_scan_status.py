"""Regression tests for scan-status endpoint."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import api_server


def test_scan_status_does_not_deadlock_while_scan_running(monkeypatch):
    monkeypatch.setattr(api_server.db, "get_latest_scan", lambda *args, **kwargs: None)

    with api_server._scan_lock:
        api_server._scan_state.update(
            {
                "running": True,
                "cv_id": "test-cv",
                "scan_id": None,
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": None,
                "error": None,
                "log": [],
                "current_detail": "בודק…",
                "steps": [
                    {"key": "parse_cv", "name": "ניתוח", "status": "success"},
                    {"key": "collect", "name": "איסוף", "status": "running"},
                ],
            }
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(api_server.cv_scan_status, "test-cv").result(
                timeout=2
            )
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

    assert result["running"] is True
    assert result["current_step"] == "collect"
