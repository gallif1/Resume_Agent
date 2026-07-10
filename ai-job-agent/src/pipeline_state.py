"""Persist pipeline-wide state such as candidate strategy version."""

from __future__ import annotations

import json
from typing import Any

from config import PIPELINE_STATE_PATH


def load_pipeline_state() -> dict[str, Any]:
    if not PIPELINE_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(PIPELINE_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_pipeline_state(updates: dict[str, Any]) -> None:
    state = load_pipeline_state()
    state.update(updates)
    PIPELINE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PIPELINE_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
