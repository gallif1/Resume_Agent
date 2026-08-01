"""Progress emission helpers for the live four-agent generation UI."""

from __future__ import annotations

from typing import Any, Callable

from intelligent_tailoring.interview_philosophy import (
    STAGE_INDEX,
    STAGE_SUBSTEPS,
    TAILOR_STAGES,
    resolve_merged_stage,
)

ProgressCallback = Callable[[dict[str, Any]], None] | None


class ProgressReporter:
    """Thin wrapper that safely emits stage/decision events.

    Legacy specialist agent ids are remapped to the four merged UI stages so
    callers can keep emitting fine-grained internal substeps.
    """

    def __init__(self, callback: ProgressCallback = None) -> None:
        self.callback = callback
        self.total = len(TAILOR_STAGES)
        self._last_substep: dict[str, str] = {}

    def emit(
        self,
        stage: str,
        status: str,
        message: str,
        *,
        agent_id: str | None = None,
        decision: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        if not self.callback:
            return
        merged = resolve_merged_stage(stage)
        # Preserve original specialist id as substep metadata for the UI
        legacy_id = agent_id or stage
        substeps = list(STAGE_SUBSTEPS.get(merged) or [])
        payload: dict[str, Any] = {
            "event": "stage",
            "stage": merged,
            "agent_id": merged,
            "legacy_agent_id": legacy_id,
            "status": status,
            "message": message,
            "index": STAGE_INDEX.get(merged, 0),
            "total": self.total,
            "substeps": substeps,
            "stage_of": f"Stage {STAGE_INDEX.get(merged, 0) + 1} of {self.total}",
            **extra,
        }
        if status in ("started", "running", "completed"):
            self._last_substep[merged] = message
            payload["current_substep"] = message
        try:
            self.callback(payload)
        except Exception:
            pass
        if decision:
            self.decision(stage, decision)

    def started(self, stage: str, message: str, *, agent_id: str | None = None) -> None:
        self.emit(stage, "started", message, agent_id=agent_id)

    def completed(
        self,
        stage: str,
        message: str,
        *,
        agent_id: str | None = None,
        **extra: Any,
    ) -> None:
        self.emit(stage, "completed", message, agent_id=agent_id, **extra)

    def decision(self, stage: str, decision: dict[str, Any]) -> None:
        if not self.callback:
            return
        merged = resolve_merged_stage(stage)
        payload = {
            "event": "decision",
            "stage": merged,
            "agent_id": merged,
            "status": "info",
            "message": decision.get("text") or decision.get("reason") or "",
            "decision": decision,
            "index": STAGE_INDEX.get(merged, 0),
            "total": self.total,
            "stage_of": f"Stage {STAGE_INDEX.get(merged, 0) + 1} of {self.total}",
        }
        try:
            self.callback(payload)
        except Exception:
            pass

    def heartbeat(self, stage: str, message: str) -> None:
        self.emit(stage, "running", message)
