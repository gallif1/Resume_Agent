"""Shared constants for the Live Job Scanner."""

from __future__ import annotations

PROVIDERS = (
    "lever",
    "greenhouse",
    "ashby",
    "workday",
    "comeet",
    "smartrecruiters",
    "workable",
    "teamtailor",
    "recruitee",
)

DEFAULT_INTERVALS_SECONDS: dict[str, int] = {
    "lever": 5 * 60,
    "greenhouse": 5 * 60,
    "comeet": 5 * 60,
    "ashby": 5 * 60,
    "workday": 10 * 60,
    "smartrecruiters": 5 * 60,
    "workable": 5 * 60,
    "teamtailor": 5 * 60,
    "recruitee": 5 * 60,
    "browser": 15 * 60,
}

MARKET = "Israel"

# Source lifecycle
SOURCE_NOT_INITIALIZED = "NOT_INITIALIZED"
SOURCE_BASELINE_SCANNING = "BASELINE_SCANNING"
SOURCE_LIVE = "LIVE"
SOURCE_ERROR = "ERROR"
SOURCE_DISABLED = "DISABLED"

# Scanner session status
STATUS_STOPPED = "STOPPED"
STATUS_RUNNING = "RUNNING"
STATUS_PAUSED = "PAUSED"
STATUS_BUILDING_BASELINE = "BUILDING_BASELINE"

# Job lifecycle (conservative)
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_POSSIBLY_REMOVED = "possibly_removed"
LIFECYCLE_CLOSED = "closed"

DISCOVERED_BY = "live_scanner"

# Missing from N consecutive successful source scans before possibly_removed
MISSING_SCANS_BEFORE_POSSIBLY_REMOVED = 2
MISSING_SCANS_BEFORE_CLOSED = 5

HTTP_TIMEOUT_SECONDS = 30
HTTP_RETRIES = 2

ACTIVITY_LIMIT_DEFAULT = 200
SESSION_JOBS_LIMIT_DEFAULT = 300
