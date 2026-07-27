"""Structured collection status lines for the web UI and scan summary."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

AGENT_WARNING_PREFIX = "AGENT_WARNING:"
COLLECT_SUMMARY_PREFIX = "COLLECT_SUMMARY:"
MATCH_SUMMARY_PREFIX = "MATCH_SUMMARY:"
JOB_FOUND_PREFIX = "JOB_FOUND:"
STATUS_UPDATE_PREFIX = "STATUS_UPDATE:"


@dataclass
class CollectionOutcome:
    """Result of one job-board search for a single query."""

    jobs: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    reason: str | None = None
    reason_he: str | None = None
    http_status: int | None = None
    debug_artifact: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.jobs)


def emit_agent_warning(message: str) -> None:
    """Print a user-visible warning consumed by the API scan log."""
    text = message.strip()
    if text:
        print(f"{AGENT_WARNING_PREFIX} {text}")


def emit_collect_summary(summary: dict[str, Any]) -> None:
    """Print machine-readable collection summary for scan persistence."""
    print(f"{COLLECT_SUMMARY_PREFIX}{json.dumps(summary, ensure_ascii=False)}")


def emit_match_summary(summary: dict[str, Any]) -> None:
    """Print machine-readable matching summary for scan persistence."""
    print(f"{MATCH_SUMMARY_PREFIX}{json.dumps(summary, ensure_ascii=False)}")


def emit_job_found(job: dict[str, Any]) -> None:
    """Print one scored job for the API SSE stream (collect→enrich→match done)."""
    print(f"{JOB_FOUND_PREFIX}{json.dumps(job, ensure_ascii=False, default=str)}")


def emit_status_update(message: str) -> None:
    """Print a user-facing progress line for the API SSE stream."""
    text = message.strip()
    if text:
        print(f"{STATUS_UPDATE_PREFIX}{text}")


def parse_agent_line(line: str) -> dict[str, Any] | None:
    """Parse AGENT_* / COLLECT_* / MATCH_* / JOB_FOUND / STATUS lines from stdout."""
    stripped = line.strip()
    if stripped.startswith(AGENT_WARNING_PREFIX):
        return {
            "type": "warning",
            "message": stripped[len(AGENT_WARNING_PREFIX) :].strip(),
        }
    if stripped.startswith(COLLECT_SUMMARY_PREFIX):
        payload = stripped[len(COLLECT_SUMMARY_PREFIX) :].strip()
        try:
            return {"type": "summary", "summary": json.loads(payload)}
        except json.JSONDecodeError:
            return None
    if stripped.startswith(MATCH_SUMMARY_PREFIX):
        payload = stripped[len(MATCH_SUMMARY_PREFIX) :].strip()
        try:
            return {"type": "match_summary", "summary": json.loads(payload)}
        except json.JSONDecodeError:
            return None
    if stripped.startswith(JOB_FOUND_PREFIX):
        payload = stripped[len(JOB_FOUND_PREFIX) :].strip()
        try:
            return {"type": "job_found", "job": json.loads(payload)}
        except json.JSONDecodeError:
            return None
    if stripped.startswith(STATUS_UPDATE_PREFIX):
        return {
            "type": "status_update",
            "message": stripped[len(STATUS_UPDATE_PREFIX) :].strip(),
        }
    return None


def outcome_to_dict(outcome: CollectionOutcome) -> dict[str, Any]:
    data = asdict(outcome)
    data.pop("jobs", None)
    data["job_count"] = len(outcome.jobs)
    return data
