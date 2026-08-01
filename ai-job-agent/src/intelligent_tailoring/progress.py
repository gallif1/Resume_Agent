"""Progress emission helpers for the live multi-agent generation UI."""

from __future__ import annotations

from typing import Any, Callable

from intelligent_tailoring.interview_philosophy import STAGE_INDEX, TAILOR_STAGES

ProgressCallback = Callable[[dict[str, Any]], None] | None


class ProgressReporter:
    """Thin wrapper that safely emits stage/decision events."""

    def __init__(self, callback: ProgressCallback = None) -> None:
        self.callback = callback
        self.total = len(TAILOR_STAGES)

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
        payload: dict[str, Any] = {
            "event": "stage",
            "stage": stage,
            "agent_id": agent_id or stage,
            "status": status,
            "message": message,
            "index": STAGE_INDEX.get(stage, 0),
            "total": self.total,
            **extra,
        }
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
        payload = {
            "event": "decision",
            "stage": stage,
            "status": "info",
            "message": decision.get("text") or decision.get("reason") or "",
            "decision": decision,
            "index": STAGE_INDEX.get(stage, 0),
            "total": self.total,
        }
        try:
            self.callback(payload)
        except Exception:
            pass

    def heartbeat(self, stage: str, message: str) -> None:
        self.emit(stage, "running", message)
