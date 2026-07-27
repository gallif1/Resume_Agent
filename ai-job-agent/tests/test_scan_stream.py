"""Tests for SSE scan stream helpers and JOB_FOUND parsing."""

from __future__ import annotations

import json
import queue

import collection_report
import scan_stream


def test_parse_job_found_line():
    payload = {"job_id": 7, "title": "Engineer", "match_score": 88}
    line = f"{collection_report.JOB_FOUND_PREFIX}{json.dumps(payload)}"
    parsed = collection_report.parse_agent_line(line)
    assert parsed is not None
    assert parsed["type"] == "job_found"
    assert parsed["job"]["job_id"] == 7
    assert parsed["job"]["match_score"] == 88


def test_parse_status_update_line():
    line = f"{collection_report.STATUS_UPDATE_PREFIX}סורק את דרושים…"
    parsed = collection_report.parse_agent_line(line)
    assert parsed is not None
    assert parsed["type"] == "status_update"
    assert "דרושים" in parsed["message"]


def test_scan_stream_publish_and_subscribe():
    user_id = "sse-test-user"
    q = scan_stream.subscribe(user_id)
    try:
        scan_stream.publish_status(user_id, "Scraping Drushim...")
        scan_stream.publish_job_found(user_id, {"job_id": 1, "title": "Dev"})
        scan_stream.publish_scan_complete(user_id, {})

        first = q.get(timeout=1)
        assert first["event"] == "status_update"
        assert "Drushim" in first["data"]

        second = q.get(timeout=1)
        assert second["event"] == "job_found"
        job = json.loads(second["data"])
        assert job["job_id"] == 1

        third = q.get(timeout=1)
        assert third["event"] == "scan_complete"

        sentinel = q.get(timeout=1)
        assert sentinel is scan_stream.SCAN_COMPLETE_SENTINEL
    finally:
        scan_stream.unsubscribe(user_id, q)


def test_scan_stream_unsubscribe_stops_delivery():
    user_id = "sse-unsub-user"
    q = scan_stream.subscribe(user_id)
    scan_stream.unsubscribe(user_id, q)
    scan_stream.publish_status(user_id, "should not arrive")
    try:
        q.get(timeout=0.05)
        raise AssertionError("expected empty queue after unsubscribe")
    except queue.Empty:
        pass
