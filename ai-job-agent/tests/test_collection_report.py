"""Tests for structured collection reporting."""

from __future__ import annotations

import json

from collection_report import (
    AGENT_WARNING_PREFIX,
    COLLECT_SUMMARY_PREFIX,
    CollectionOutcome,
    emit_agent_warning,
    emit_collect_summary,
    parse_agent_line,
)


def test_parse_agent_warning_line():
    parsed = parse_agent_line(f"{AGENT_WARNING_PREFIX} דרושים: לא נמצאו משרות")
    assert parsed == {
        "type": "warning",
        "message": "דרושים: לא נמצאו משרות",
    }


def test_parse_collect_summary_line():
    payload = {"drushim": {"raw": 0, "new": 0}}
    parsed = parse_agent_line(f"{COLLECT_SUMMARY_PREFIX}{json.dumps(payload)}")
    assert parsed == {"type": "summary", "summary": payload}


def test_emit_collect_summary_prints_prefix(capsys):
    emit_collect_summary({"warnings": ["test"]})
    output = capsys.readouterr().out.strip()
    assert output.startswith(COLLECT_SUMMARY_PREFIX)
    assert json.loads(output[len(COLLECT_SUMMARY_PREFIX) :])["warnings"] == ["test"]


def test_emit_agent_warning_prints_prefix(capsys):
    emit_agent_warning("הודעה")
    assert capsys.readouterr().out.strip() == f"{AGENT_WARNING_PREFIX} הודעה"


def test_collection_outcome_ok_flag():
    assert CollectionOutcome(jobs=[{"title": "Dev"}]).ok is True
    assert CollectionOutcome(status="blocked").ok is False
