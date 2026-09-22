"""Backend scheduler loop for continuous live scanning.

One worker thread per user. Prevents overlapping scans for the same source
and duplicate PLAY workers.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from live_scanner.collectors import get_collector
from live_scanner.constants import (
    SOURCE_BASELINE_SCANNING,
    SOURCE_ERROR,
    SOURCE_LIVE,
    SOURCE_NOT_INITIALIZED,
    STATUS_BUILDING_BASELINE,
    STATUS_PAUSED,
    STATUS_RUNNING,
    STATUS_STOPPED,
)
from live_scanner import store
from live_scanner.israel_filter import filter_israel_jobs
from live_scanner.pipeline import persist_collected_job

logger = logging.getLogger("live_scanner.scheduler")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat()


class UserScannerWorker:
    """Single-user continuous scanner."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._source_locks: dict[str, threading.Lock] = {}
        self._wake = threading.Event()

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def play(self) -> dict[str, Any]:
        with self._lock:
            db_path = store.workspace_db(self.user_id)
            store.seed_default_sources(self.user_id, db_path=db_path)
            state = store.get_state(self.user_id, db_path=db_path)

            if self.is_alive:
                if self._pause.is_set():
                    self._pause.clear()
                    self._wake.set()
                    stats = store.source_stats(self.user_id, db_path=db_path)
                    status = (
                        STATUS_BUILDING_BASELINE
                        if stats["sources_initialized"] < stats["sources_monitored"]
                        else STATUS_RUNNING
                    )
                    store.save_state(
                        self.user_id, {"status": status, **stats}, db_path=db_path
                    )
                    store.add_activity(
                        self.user_id, "Scanner resumed", level="info", db_path=db_path
                    )
                    logger.info("live_scanner resumed user=%s", self.user_id)
                    return store.get_state(self.user_id, db_path=db_path)
                # Already running — no duplicate worker
                return store.get_state(self.user_id, db_path=db_path)

            self._stop.clear()
            self._pause.clear()
            session_id = uuid.uuid4().hex
            stats = store.source_stats(self.user_id, db_path=db_path)
            status = (
                STATUS_BUILDING_BASELINE
                if stats["sources_initialized"] < stats["sources_monitored"]
                else STATUS_RUNNING
            )
            store.save_state(
                self.user_id,
                {
                    "status": status,
                    "session_id": session_id,
                    "session_started_at": _iso(),
                    "jobs_checked": 0,
                    "new_jobs": 0,
                    "duplicates_skipped": 0,
                    "relevant_jobs": 0,
                    "jobs_fetched": 0,
                    "foreign_filtered": 0,
                    "israel_jobs": 0,
                    **stats,
                },
                db_path=db_path,
            )
            store.add_activity(
                self.user_id,
                "Scanner started",
                level="info",
                db_path=db_path,
            )
            logger.info("live_scanner started user=%s session=%s", self.user_id, session_id)

            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"live-scanner-{self.user_id[:8]}",
                daemon=True,
            )
            self._thread.start()
            return store.get_state(self.user_id, db_path=db_path)

    def pause(self) -> dict[str, Any]:
        db_path = store.workspace_db(self.user_id)
        self._pause.set()
        store.save_state(self.user_id, {"status": STATUS_PAUSED}, db_path=db_path)
        store.add_activity(self.user_id, "Scanner paused", level="info", db_path=db_path)
        logger.info("live_scanner paused user=%s", self.user_id)
        return store.get_state(self.user_id, db_path=db_path)

    def stop(self) -> dict[str, Any]:
        db_path = store.workspace_db(self.user_id)
        self._stop.set()
        self._pause.clear()
        self._wake.set()
        store.save_state(self.user_id, {"status": STATUS_STOPPED, "next_scan_at": None}, db_path=db_path)
        store.add_activity(self.user_id, "Scanner stopped", level="info", db_path=db_path)
        logger.info("live_scanner stopped user=%s", self.user_id)
        return store.get_state(self.user_id, db_path=db_path)

    def _source_lock(self, source_id: str) -> threading.Lock:
        with self._lock:
            if source_id not in self._source_locks:
                self._source_locks[source_id] = threading.Lock()
            return self._source_locks[source_id]

    def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                if self._pause.is_set():
                    self._wake.wait(timeout=1.0)
                    self._wake.clear()
                    continue

                db_path = store.workspace_db(self.user_id)
                sources = [
                    s
                    for s in store.list_sources(self.user_id, db_path=db_path)
                    if s.get("enabled")
                ]
                stats = store.source_stats(self.user_id, db_path=db_path)
                building = any(
                    not s.get("baseline_created_at")
                    for s in sources
                )
                status = STATUS_BUILDING_BASELINE if building else STATUS_RUNNING

                due = [s for s in sources if self._is_due(s)]
                if not due:
                    next_at = self._compute_next_scan(sources)
                    store.save_state(
                        self.user_id,
                        {
                            "status": status,
                            "next_scan_at": next_at,
                            **stats,
                        },
                        db_path=db_path,
                    )
                    self._wake.wait(timeout=2.0)
                    self._wake.clear()
                    continue

                store.save_state(
                    self.user_id,
                    {"status": status, **stats},
                    db_path=db_path,
                )

                for source in due:
                    if self._stop.is_set() or self._pause.is_set():
                        break
                    self._scan_source(source)

                stats = store.source_stats(self.user_id, db_path=db_path)
                building = any(
                    not s.get("baseline_created_at")
                    for s in store.list_sources(self.user_id, db_path=db_path)
                    if s.get("enabled")
                )
                next_at = self._compute_next_scan(
                    store.list_sources(self.user_id, db_path=db_path)
                )
                store.save_state(
                    self.user_id,
                    {
                        "status": STATUS_BUILDING_BASELINE if building else STATUS_RUNNING,
                        "last_scan_at": _iso(),
                        "next_scan_at": next_at,
                        **stats,
                    },
                    db_path=db_path,
                )
                # Brief yield between cycles
                self._wake.wait(timeout=1.0)
                self._wake.clear()
        except Exception:
            logger.exception("live_scanner worker crashed user=%s", self.user_id)
            try:
                db_path = store.workspace_db(self.user_id)
                store.save_state(
                    self.user_id, {"status": STATUS_STOPPED}, db_path=db_path
                )
                store.add_activity(
                    self.user_id,
                    "Scanner worker crashed — stopped",
                    level="error",
                    db_path=db_path,
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            logger.info("live_scanner worker exit user=%s", self.user_id)

    def _is_due(self, source: dict[str, Any]) -> bool:
        # Always due until baseline exists
        if not source.get("baseline_created_at"):
            return True
        last = source.get("last_scan_at")
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        except ValueError:
            return True
        interval = int(source.get("scan_interval_seconds") or 300)
        return _utc_now() >= last_dt + timedelta(seconds=interval)

    def _compute_next_scan(self, sources: list[dict[str, Any]]) -> str | None:
        candidates: list[datetime] = []
        now = _utc_now()
        for s in sources:
            if not s.get("enabled"):
                continue
            if not s.get("baseline_created_at"):
                return _iso(now)
            last = s.get("last_scan_at")
            interval = int(s.get("scan_interval_seconds") or 300)
            if not last:
                candidates.append(now)
                continue
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            except ValueError:
                candidates.append(now)
                continue
            candidates.append(last_dt + timedelta(seconds=interval))
        if not candidates:
            return None
        return _iso(min(candidates))

    def _scan_source(self, source: dict[str, Any]) -> None:
        source_id = str(source["id"])
        lock = self._source_lock(source_id)
        if not lock.acquire(blocking=False):
            return
        try:
            self._scan_source_locked(source)
        finally:
            lock.release()

    def _scan_source_locked(self, source: dict[str, Any]) -> None:
        user_id = self.user_id
        db_path = store.workspace_db(user_id)
        source_id = str(source["id"])
        provider = str(source.get("provider") or "")
        company = str(source.get("company_name") or "")
        is_baseline_pass = not bool(source.get("baseline_created_at"))

        if is_baseline_pass:
            store.update_source_scan_result(
                user_id,
                source_id,
                status=SOURCE_BASELINE_SCANNING,
                db_path=db_path,
            )
            store.add_activity(
                user_id,
                f"{company} / {provider.title()} → Building baseline",
                level="baseline",
                source_id=source_id,
                db_path=db_path,
            )
            logger.info(
                "baseline scan started user=%s source=%s provider=%s",
                user_id,
                source_id,
                provider,
            )
        else:
            store.add_activity(
                user_id,
                f"Scanning {company} [{provider}]",
                level="info",
                source_id=source_id,
                db_path=db_path,
            )
            logger.info(
                "scan started user=%s source=%s provider=%s",
                user_id,
                source_id,
                provider,
            )

        collector = get_collector(provider)
        if collector is None:
            store.update_source_scan_result(
                user_id,
                source_id,
                status=SOURCE_ERROR,
                last_error=f"Unknown provider: {provider}",
                db_path=db_path,
            )
            store.add_activity(
                user_id,
                f"{company} [{provider}] ERROR: Unknown provider",
                level="error",
                source_id=source_id,
                db_path=db_path,
            )
            self._bump_failed()
            return

        try:
            result = collector.collect(
                board_identifier=str(source.get("board_identifier") or ""),
                careers_url=source.get("careers_url"),
            )
        except Exception as exc:  # noqa: BLE001
            result = type("R", (), {"status": "error", "error": str(exc), "jobs": []})()

        if result.status in {"error", "unsupported"}:
            err = result.error or result.status
            # Keep LIVE if baseline already exists — one failure must not wipe init state.
            err_status = (
                SOURCE_LIVE if source.get("baseline_created_at") else SOURCE_ERROR
            )
            store.update_source_scan_result(
                user_id,
                source_id,
                status=err_status,
                last_error=err,
                success=False,
                db_path=db_path,
            )
            store.add_activity(
                user_id,
                f"{company} [{provider}] ERROR: {err} — Retry scheduled",
                level="error",
                source_id=source_id,
                db_path=db_path,
            )
            logger.warning(
                "source failure user=%s source=%s error=%s", user_id, source_id, err
            )
            self._bump_failed()
            return

        jobs_global = list(result.jobs or [])
        jobs, fetched_count, foreign_filtered = filter_israel_jobs(jobs_global)
        known = store.get_known_external_ids(user_id, source_id, db_path=db_path)

        checked = len(jobs)
        new_count = 0
        dup_count = 0
        relevant_count = 0
        inserted_baseline = 0
        present_ids: set[str] = set()

        baseline_ts = _iso() if is_baseline_pass else source.get("baseline_created_at")

        store.add_activity(
            user_id,
            (
                f"{company} [{provider}] → {fetched_count} jobs returned → "
                f"{checked} Israel → {foreign_filtered} foreign filtered"
            ),
            level="info",
            source_id=source_id,
            db_path=db_path,
        )

        for job in jobs:
            present_ids.add(job.external_job_id)
            if job.external_job_id in known:
                dup_count += 1
                # Refresh last_seen for known
                store.upsert_known_job(
                    user_id,
                    source_id,
                    external_job_id=job.external_job_id,
                    canonical_url=job.job_url,
                    job_id=None,
                    is_baseline=is_baseline_pass,
                    db_path=db_path,
                )
                continue

            # Unknown external id
            if is_baseline_pass:
                outcome = persist_collected_job(
                    user_id,
                    source,
                    job,
                    is_baseline=True,
                    baseline_created_at=baseline_ts,
                    db_path=db_path,
                    # Cheap match only for potentially relevant baseline titles later;
                    # skip expensive analysis by default for full baseline dumps.
                    run_match=False,
                )
                if outcome.get("action") == "inserted":
                    inserted_baseline += 1
                elif outcome.get("action") == "duplicate":
                    dup_count += 1
                known.add(job.external_job_id)
            else:
                outcome = persist_collected_job(
                    user_id,
                    source,
                    job,
                    is_baseline=False,
                    baseline_created_at=None,
                    db_path=db_path,
                    run_match=True,
                )
                known.add(job.external_job_id)
                if outcome.get("action") == "inserted":
                    new_count += 1
                    if outcome.get("relevant"):
                        relevant_count += 1
                    loc_bit = f" — {job.location}" if (job.location or "").strip() else ""
                    store.add_activity(
                        user_id,
                        f"NEW: {job.title}{loc_bit}",
                        level="new_job",
                        source_id=source_id,
                        job_id=outcome.get("job_id"),
                        payload={
                            "title": job.title,
                            "company": job.company,
                            "location": job.location,
                            "url": job.job_url,
                        },
                        db_path=db_path,
                    )
                    logger.info(
                        "new job detected user=%s source=%s title=%s",
                        user_id,
                        source_id,
                        job.title,
                    )
                elif outcome.get("action") == "duplicate":
                    dup_count += 1
                    logger.info(
                        "duplicate skipped user=%s source=%s ext=%s",
                        user_id,
                        source_id,
                        job.external_job_id,
                    )
                else:
                    store.add_activity(
                        user_id,
                        f"Job processing failure: {outcome.get('error')}",
                        level="error",
                        source_id=source_id,
                        db_path=db_path,
                    )

        # Only mark missing when the fetch succeeded with a real listing response
        if result.status in {"ok", "empty"}:
            store.mark_missing_known_jobs(
                user_id, source_id, present_ids, db_path=db_path
            )

        if is_baseline_pass:
            store.update_source_scan_result(
                user_id,
                source_id,
                status=SOURCE_LIVE,
                success=True,
                last_error=None,
                baseline_created_at=str(baseline_ts),
                baseline_job_count=checked,
                db_path=db_path,
            )
            store.add_activity(
                user_id,
                (
                    f"{company} / {provider.title()} → {fetched_count} global → "
                    f"{checked} Israel jobs → Baseline complete "
                    f"({inserted_baseline} added; {foreign_filtered} foreign filtered)"
                ),
                level="baseline",
                source_id=source_id,
                db_path=db_path,
            )
            logger.info(
                "baseline scan completed user=%s source=%s jobs=%s inserted=%s",
                user_id,
                source_id,
                checked,
                inserted_baseline,
            )
            # Session counters: baseline does NOT increment new_jobs
            self._bump_counters(
                jobs_checked=checked,
                duplicates_skipped=dup_count,
                baseline_delta=inserted_baseline,
                jobs_fetched=fetched_count,
                foreign_filtered=foreign_filtered,
                israel_jobs=checked,
            )
        else:
            store.update_source_scan_result(
                user_id,
                source_id,
                status=SOURCE_LIVE,
                success=True,
                last_error=None,
                db_path=db_path,
            )
            if new_count:
                store.add_activity(
                    user_id,
                    (
                        f"{company} [{provider}] → {fetched_count} fetched, "
                        f"{checked} Israel, {dup_count} known, {new_count} NEW Israeli"
                    ),
                    level="info",
                    source_id=source_id,
                    db_path=db_path,
                )
            else:
                store.add_activity(
                    user_id,
                    (
                        f"{company} [{provider}] → {fetched_count} fetched, "
                        f"{checked} Israel, no new Israeli jobs"
                    ),
                    level="info",
                    source_id=source_id,
                    db_path=db_path,
                )
            logger.info(
                "scan completed user=%s source=%s fetched=%s israel=%s new=%s dups=%s",
                user_id,
                source_id,
                fetched_count,
                checked,
                new_count,
                dup_count,
            )
            self._bump_counters(
                jobs_checked=checked,
                new_jobs=new_count,
                duplicates_skipped=dup_count,
                relevant_jobs=relevant_count,
                jobs_fetched=fetched_count,
                foreign_filtered=foreign_filtered,
                israel_jobs=checked,
            )

    def _bump_counters(
        self,
        *,
        jobs_checked: int = 0,
        new_jobs: int = 0,
        duplicates_skipped: int = 0,
        relevant_jobs: int = 0,
        baseline_delta: int = 0,
        jobs_fetched: int = 0,
        foreign_filtered: int = 0,
        israel_jobs: int = 0,
    ) -> None:
        db_path = store.workspace_db(self.user_id)
        state = store.get_state(self.user_id, db_path=db_path)
        stats = store.source_stats(self.user_id, db_path=db_path)
        store.save_state(
            self.user_id,
            {
                "jobs_checked": int(state.get("jobs_checked") or 0) + jobs_checked,
                "new_jobs": int(state.get("new_jobs") or 0) + new_jobs,
                "duplicates_skipped": int(state.get("duplicates_skipped") or 0)
                + duplicates_skipped,
                "relevant_jobs": int(state.get("relevant_jobs") or 0) + relevant_jobs,
                "jobs_fetched": int(state.get("jobs_fetched") or 0) + jobs_fetched,
                "foreign_filtered": int(state.get("foreign_filtered") or 0)
                + foreign_filtered,
                "israel_jobs": int(state.get("israel_jobs") or 0) + israel_jobs,
                "baseline_jobs": stats["baseline_jobs"],
                **{
                    k: stats[k]
                    for k in ("sources_monitored", "sources_initialized", "sources_failed")
                },
            },
            db_path=db_path,
        )

    def _bump_failed(self) -> None:
        db_path = store.workspace_db(self.user_id)
        stats = store.source_stats(self.user_id, db_path=db_path)
        store.save_state(self.user_id, stats, db_path=db_path)


class ScannerRegistry:
    """Process-wide registry ensuring one worker per user."""

    def __init__(self) -> None:
        self._workers: dict[str, UserScannerWorker] = {}
        self._lock = threading.Lock()

    def get(self, user_id: str) -> UserScannerWorker:
        with self._lock:
            worker = self._workers.get(user_id)
            if worker is None:
                worker = UserScannerWorker(user_id)
                self._workers[user_id] = worker
            return worker


_REGISTRY = ScannerRegistry()


def get_registry() -> ScannerRegistry:
    return _REGISTRY
