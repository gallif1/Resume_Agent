"""Public service façade for Live Job Scanner controls and queries."""

from __future__ import annotations

import logging
from typing import Any

from live_scanner.collectors import list_providers, validate_source_config
from live_scanner.constants import MARKET, STATUS_STOPPED
from live_scanner.defaults import default_interval_for
from live_scanner.scheduler import get_registry
from live_scanner import store

logger = logging.getLogger("live_scanner.service")


class LiveScannerService:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    def _db(self):
        return store.workspace_db(self.user_id)

    def ensure_ready(self) -> None:
        db_path = self._db()
        store.seed_default_sources(self.user_id, db_path=db_path)
        # If process restarted while status was RUNNING, mark stopped until PLAY
        state = store.get_state(self.user_id, db_path=db_path)
        worker = get_registry().get(self.user_id)
        if state.get("status") in {"RUNNING", "BUILDING_BASELINE", "PAUSED"} and not worker.is_alive:
            # Persist baseline; only reset runtime status
            if state.get("status") != "PAUSED":
                store.save_state(
                    self.user_id, {"status": STATUS_STOPPED}, db_path=db_path
                )
                logger.info(
                    "live_scanner recovered stopped status after restart user=%s",
                    self.user_id,
                )

    def snapshot(self) -> dict[str, Any]:
        self.ensure_ready()
        db_path = self._db()
        state = store.get_state(self.user_id, db_path=db_path)
        stats = store.source_stats(self.user_id, db_path=db_path)
        sources = store.list_sources(self.user_id, db_path=db_path)
        activity = store.list_activity(self.user_id, db_path=db_path)
        discovered_jobs = store.list_session_jobs(
            self.user_id, new_only=False, limit=300, db_path=db_path
        )
        if not discovered_jobs:
            # After CLEAR / page reload, still show indexed baseline jobs.
            discovered_jobs = store.list_known_jobs_for_display(
                self.user_id, limit=300, db_path=db_path
            )
        new_jobs = [j for j in discovered_jobs if not j.get("is_baseline")]
        worker = get_registry().get(self.user_id)
        return {
            "state": {**state, **stats, "market": MARKET},
            "market": MARKET,
            "worker_alive": worker.is_alive,
            "sources": sources,
            "activity": activity,
            "discovered_jobs": discovered_jobs,
            "new_jobs": new_jobs,
            "providers": list_providers(),
        }

    def play(self) -> dict[str, Any]:
        self.ensure_ready()
        worker = get_registry().get(self.user_id)
        state = worker.play()
        return {"ok": True, "state": state, "worker_alive": worker.is_alive}

    def pause(self) -> dict[str, Any]:
        self.ensure_ready()
        worker = get_registry().get(self.user_id)
        state = worker.pause()
        return {"ok": True, "state": state, "worker_alive": worker.is_alive}

    def stop(self) -> dict[str, Any]:
        self.ensure_ready()
        worker = get_registry().get(self.user_id)
        state = worker.stop()
        return {"ok": True, "state": state, "worker_alive": worker.is_alive}

    def clear(self) -> dict[str, Any]:
        """Clear session display only — never baseline / known IDs / jobs."""
        self.ensure_ready()
        db_path = self._db()
        state = store.clear_session_display(self.user_id, db_path=db_path)
        store.add_activity(
            self.user_id,
            "Session display cleared (baseline & known jobs retained)",
            level="info",
            db_path=db_path,
        )
        logger.info("live_scanner session cleared user=%s", self.user_id)
        return {"ok": True, "state": state}

    def add_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        provider = str(payload.get("provider") or "").strip().lower()
        board = str(payload.get("board_identifier") or "").strip()
        careers_url = str(payload.get("careers_url") or "").strip() or None
        company = str(payload.get("company_name") or "").strip()
        if not company:
            raise ValueError("company_name is required")
        if not provider:
            raise ValueError("provider is required")
        err = validate_source_config(
            provider, board_identifier=board, careers_url=careers_url
        )
        if err:
            raise ValueError(err)

        # Soft reachability check — never crash; surface parse/HTTP issues early.
        from live_scanner.collectors import get_collector

        collector = get_collector(provider)
        if collector is not None and (board or careers_url):
            try:
                probe = collector.collect(
                    board_identifier=board, careers_url=careers_url
                )
                if probe.status in {"error", "unsupported"}:
                    raise ValueError(
                        f"Source validation failed: {probe.error or probe.status}"
                    )
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Source validation failed: {exc}") from exc

        interval = payload.get("scan_interval_seconds")
        if interval is None:
            interval = default_interval_for(provider)
        source = store.add_source(
            self.user_id,
            company_name=company,
            provider=provider,
            board_identifier=board,
            careers_url=careers_url,
            enabled=bool(payload.get("enabled", True)),
            scan_interval_seconds=int(interval),
            notes=str(payload.get("notes") or "") or None,
            is_demo=bool(payload.get("is_demo", False)),
            db_path=self._db(),
        )
        return source

    def update_source(self, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        current = store.get_source(self.user_id, source_id, db_path=self._db())
        if not current:
            raise KeyError("Source not found")
        provider = str(payload.get("provider") or current["provider"])
        board = payload.get("board_identifier")
        careers = payload.get("careers_url")
        board_val = current["board_identifier"] if board is None else str(board)
        careers_val = current.get("careers_url") if careers is None else (str(careers) or None)
        err = validate_source_config(
            provider, board_identifier=board_val, careers_url=careers_val
        )
        if err and payload.get("enabled", current.get("enabled")):
            raise ValueError(err)
        updated = store.update_source(
            self.user_id,
            source_id,
            company_name=payload.get("company_name"),
            board_identifier=None if board is None else str(board),
            careers_url=None if careers is None else (str(careers) or None),
            enabled=payload.get("enabled"),
            scan_interval_seconds=payload.get("scan_interval_seconds"),
            notes=payload.get("notes"),
            db_path=self._db(),
        )
        if not updated:
            raise KeyError("Source not found")
        return updated

    def delete_source(self, source_id: str) -> None:
        self.ensure_ready()
        ok = store.delete_source(self.user_id, source_id, db_path=self._db())
        if not ok:
            raise KeyError("Source not found")


def get_scanner_service(user_id: str) -> LiveScannerService:
    return LiveScannerService(user_id)
