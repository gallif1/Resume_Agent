"""In-process SSE event bus for live resume-generation progress.

Mirrors ``scan_stream``: tailor worker publishes; ``GET /api/tailor/stream``
subscribers consume Server-Sent Events keyed by user_id (+ optional cv/job filter).
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any, Callable

TAILOR_COMPLETE_SENTINEL = object()

_lock = threading.Lock()
# user_id -> list of subscriber queues
_subscribers: dict[str, list[queue.Queue]] = {}
# run_id -> latest snapshot for late joiners / polling
_runs: dict[str, dict[str, Any]] = {}


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def subscribe(user_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=2000)
    with _lock:
        _subscribers.setdefault(user_id, []).append(q)
    return q


def unsubscribe(user_id: str, q: queue.Queue) -> None:
    with _lock:
        subs = _subscribers.get(user_id)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            pass
        if not subs:
            _subscribers.pop(user_id, None)


def publish(user_id: str, event: str, data: Any) -> None:
    if not user_id:
        return
    if isinstance(data, str):
        payload_data = data
    else:
        payload_data = json.dumps(data, ensure_ascii=False, default=str)
    item = {"event": event, "data": payload_data}
    with _lock:
        subs = list(_subscribers.get(user_id, []))
    for q in subs:
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(item)
            except queue.Full:
                pass


def publish_stage(user_id: str, payload: dict[str, Any]) -> None:
    publish(user_id, "tailor_stage", payload)


def publish_decision(user_id: str, payload: dict[str, Any]) -> None:
    publish(user_id, "tailor_decision", payload)


def publish_complete(user_id: str, payload: dict[str, Any] | None = None) -> None:
    publish(user_id, "tailor_complete", payload if payload is not None else {})
    with _lock:
        subs = list(_subscribers.get(user_id, []))
    for q in subs:
        try:
            q.put_nowait(TAILOR_COMPLETE_SENTINEL)
        except queue.Full:
            pass


def begin_run(
    *,
    user_id: str,
    cv_id: str,
    job_id: int,
    run_id: str | None = None,
) -> str:
    rid = run_id or new_run_id()
    snapshot = {
        "run_id": rid,
        "user_id": user_id,
        "cv_id": cv_id,
        "job_id": int(job_id),
        "status": "running",
        "started_at": time.time(),
        "stages": [],
        "decisions": [],
        "current_stage": None,
        "message": "Starting resume generation…",
    }
    with _lock:
        _runs[rid] = snapshot
    publish_stage(
        user_id,
        {
            "run_id": rid,
            "cv_id": cv_id,
            "job_id": int(job_id),
            "stage": "start",
            "status": "started",
            "message": "AI team starting work on your resume…",
            "index": 0,
            "total": 11,
        },
    )
    return rid


def get_run(run_id: str) -> dict[str, Any] | None:
    with _lock:
        snap = _runs.get(run_id)
        return dict(snap) if snap else None


def _update_run(run_id: str, mutator: Callable[[dict[str, Any]], None]) -> None:
    with _lock:
        snap = _runs.get(run_id)
        if not snap:
            return
        mutator(snap)


def make_progress_callback(
    *,
    user_id: str,
    cv_id: str,
    job_id: int,
    run_id: str,
) -> Callable[[dict[str, Any]], None]:
    """Return a callback suitable for ``run_intelligent_tailoring``."""

    def _callback(event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        payload = {
            "run_id": run_id,
            "cv_id": cv_id,
            "job_id": int(job_id),
            **event,
        }
        kind = str(event.get("event") or "stage")

        def mutate(snap: dict[str, Any]) -> None:
            if kind == "decision":
                snap.setdefault("decisions", []).append(payload)
                # Keep last 40 decisions
                snap["decisions"] = snap["decisions"][-40:]
            else:
                stage = str(event.get("stage") or "")
                status = str(event.get("status") or "")
                snap["current_stage"] = stage
                snap["message"] = event.get("message") or snap.get("message")
                stages = snap.setdefault("stages", [])
                # Upsert stage entry
                found = False
                for item in stages:
                    if item.get("stage") == stage:
                        item.update(payload)
                        found = True
                        break
                if not found:
                    stages.append(payload)
                if status == "failed":
                    snap["status"] = "failed"

        _update_run(run_id, mutate)

        if kind == "decision":
            publish_decision(user_id, payload)
        else:
            publish_stage(user_id, payload)

    return _callback


def finish_run(
    *,
    user_id: str,
    run_id: str,
    report: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    def mutate(snap: dict[str, Any]) -> None:
        snap["status"] = "failed" if error else "completed"
        snap["finished_at"] = time.time()
        started = float(snap.get("started_at") or snap["finished_at"])
        snap["elapsed_seconds"] = round(snap["finished_at"] - started, 1)
        if report is not None:
            snap["generation_report"] = report
        if error:
            snap["error"] = error
            snap["message"] = error
        else:
            snap["message"] = "Resume successfully generated."
            snap["current_stage"] = "complete"

    _update_run(run_id, mutate)
    snap = get_run(run_id) or {}
    publish_complete(
        user_id,
        {
            "run_id": run_id,
            "cv_id": snap.get("cv_id"),
            "job_id": snap.get("job_id"),
            "status": snap.get("status"),
            "generation_report": snap.get("generation_report") or report or {},
            "elapsed_seconds": snap.get("elapsed_seconds"),
            "error": error,
        },
    )
