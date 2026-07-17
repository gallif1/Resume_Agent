"""Tests for scan cancellation helpers."""

from __future__ import annotations

import scan_control


def test_begin_and_cancel_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_control, "user_data_dir", lambda _uid: tmp_path)
    scan_control.begin_scan()
    assert not scan_control.is_cancelled()
    scan_control.request_cancel()
    assert scan_control.is_cancelled()


def test_persist_and_load_scan_state(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_control, "user_data_dir", lambda _uid: tmp_path)
    scan_control.save_scan_state(
        "default",
        {
            "running": True,
            "detail": "בודק",
            "steps": [{"key": "parse_cvs", "name": "parse", "status": "running"}],
            "log": [">> start"],
        },
    )
    loaded = scan_control.load_scan_state("default")
    assert loaded is not None
    assert loaded["running"] is True
    assert loaded["detail"] == "בודק"


def test_mark_interrupted_if_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_control, "user_data_dir", lambda _uid: tmp_path)
    scan_control.save_scan_state("default", {"running": True, "error": None})
    scan_control.mark_interrupted_if_stale("default")
    loaded = scan_control.load_scan_state("default")
    assert loaded is not None
    assert loaded["running"] is False
    assert "הפעלה מחדש" in (loaded.get("error") or "")
