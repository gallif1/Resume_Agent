"""In-process SSE event bus for live scan job streaming.

Scan worker threads publish events; ``GET /api/scan/stream`` subscribers
consume them as Server-Sent Events.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

# Sentinel placed on subscriber queues when a scan finishes (or is torn down).
SCAN_COMPLETE_SENTINEL = object()

_lock = threading.Lock()
# user_id -> list of subscriber queues
_subscribers: dict[str, list[queue.Queue]] = {}


def subscribe(user_id: str) -> queue.Queue:
    """Register a new SSE consumer for ``user_id``. Returns a Queue of event dicts."""
    q: queue.Queue = queue.Queue(maxsize=1000)
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
    """Push one SSE event to every subscriber of ``user_id``.

    ``data`` may be a string (already JSON) or any JSON-serializable object.
    """
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
            # Drop oldest to make room — prefer freshest job_found events.
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(item)
            except queue.Full:
                pass


def publish_status(user_id: str, message: str) -> None:
    text = (message or "").strip()
    if text:
        publish(user_id, "status_update", text)


def publish_job_found(user_id: str, job_dict: dict[str, Any]) -> None:
    publish(user_id, "job_found", job_dict)


def publish_scan_complete(user_id: str, payload: dict[str, Any] | None = None) -> None:
    """Notify listeners the scan finished, then wake blocked getters."""
    publish(user_id, "scan_complete", payload if payload is not None else {})
    with _lock:
        subs = list(_subscribers.get(user_id, []))
    for q in subs:
        try:
            q.put_nowait(SCAN_COMPLETE_SENTINEL)
        except queue.Full:
            pass


def subscriber_count(user_id: str) -> int:
    with _lock:
        return len(_subscribers.get(user_id, []))
