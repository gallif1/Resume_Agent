"""Cooperative scan cancellation and persisted scan status."""

from __future__ import annotations

import json
import signal
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import user_data_dir

_lock = threading.Lock()
_cancel_event = threading.Event()
_active_procs: list[subprocess.Popen] = []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scan_state_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "scan_state.json"


def begin_scan() -> None:
    """Clear cancellation and active process list for a new scan."""
    with _lock:
        _cancel_event.clear()
        _active_procs.clear()


def request_cancel() -> bool:
    """Signal the running scan to stop and terminate active subprocesses."""
    with _lock:
        if not _cancel_event.is_set() and not _active_procs:
            # Still set the flag in case the scan is between steps.
            was_active = False
        else:
            was_active = True
        _cancel_event.set()
        procs = list(_active_procs)
    for proc in procs:
        _terminate_process(proc)
    return True


def is_cancelled() -> bool:
    return _cancel_event.is_set()


def register_process(proc: subprocess.Popen) -> None:
    with _lock:
        _active_procs.append(proc)
        if _cancel_event.is_set():
            _terminate_process(proc)


def unregister_process(proc: subprocess.Popen) -> None:
    with _lock:
        try:
            _active_procs.remove(proc)
        except ValueError:
            pass


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def save_scan_state(user_id: str, state: dict[str, Any]) -> None:
    path = scan_state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["user_id"] = user_id
    payload["updated_at"] = _utc_now()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_scan_state(user_id: str) -> dict[str, Any] | None:
    path = scan_state_path(user_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def mark_interrupted_if_stale(user_id: str) -> None:
    """On server startup, mark a previously-running scan as interrupted."""
    state = load_scan_state(user_id)
    if not state or not state.get("running"):
        return
    state["running"] = False
    state["finished_at"] = _utc_now()
    state["error"] = state.get("error") or "הסריקה הופסקה עקב הפעלה מחדש של השרת"
    save_scan_state(user_id, state)


class ScanCancelled(Exception):
    """Raised when a scan is stopped by the user."""
