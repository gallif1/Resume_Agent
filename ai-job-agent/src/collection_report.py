"""Structured collection status lines for the web UI and scan summary."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

AGENT_WARNING_PREFIX = "AGENT_WARNING:"
COLLECT_SUMMARY_PREFIX = "COLLECT_SUMMARY:"
JOB_SCORED_PREFIX = "JOB_SCORED:"


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


def emit_job_scored(job_id: int) -> None:
    """Print a marker consumed by the API's SSE job-stream bridge.

    ``match_jobs.py`` runs as a separate subprocess, so this stdout line is
    how the API process (tailing that subprocess's output) learns a job was
    just scored and saved, in time to push a ``job_found`` SSE event.
    """
    print(f"{JOB_SCORED_PREFIX}{job_id}")


def parse_agent_line(line: str) -> dict[str, Any] | None:
    """Parse AGENT_* / COLLECT_* / JOB_SCORED lines from subprocess stdout."""
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
    if stripped.startswith(JOB_SCORED_PREFIX):
        payload = stripped[len(JOB_SCORED_PREFIX) :].strip()
        try:
            return {"type": "job_scored", "job_id": int(payload)}
        except ValueError:
            return None
    return None


def outcome_to_dict(outcome: CollectionOutcome) -> dict[str, Any]:
    data = asdict(outcome)
    data.pop("jobs", None)
    data["job_count"] = len(outcome.jobs)
    return data
