"""Job board definitions and site-selection helpers."""

from __future__ import annotations

from typing import Any, Callable

from config import GOTFRIENDS_ENABLED, LINKEDIN_ENABLED

JOB_BOARD_ORDER = ("drushim", "linkedin", "gotfriends")

JOB_BOARD_META: dict[str, dict[str, str]] = {
    "drushim": {
        "label": "Drushim",
        "label_he": "דרושים",
        "description_he": "drushim.co.il",
    },
    "linkedin": {
        "label": "LinkedIn",
        "label_he": "לינקדאין",
        "description_he": "משרות ציבוריות בישראל",
    },
    "gotfriends": {
        "label": "GotFriends",
        "label_he": "גוטפרנדס",
        "description_he": "gotfriends.co.il",
    },
}


def is_board_enabled(board_id: str) -> bool:
    if board_id == "drushim":
        return True
    if board_id == "linkedin":
        return LINKEDIN_ENABLED
    if board_id == "gotfriends":
        return GOTFRIENDS_ENABLED
    return False


def list_job_boards() -> list[dict[str, Any]]:
    """Return all known boards with server-side availability."""
    boards: list[dict[str, Any]] = []
    for board_id in JOB_BOARD_ORDER:
        meta = JOB_BOARD_META[board_id]
        boards.append(
            {
                "id": board_id,
                "label": meta["label"],
                "label_he": meta["label_he"],
                "description_he": meta["description_he"],
                "enabled": is_board_enabled(board_id),
            }
        )
    return boards


def default_job_board_ids() -> list[str]:
    return [board["id"] for board in list_job_boards() if board["enabled"]]


def normalize_job_board_ids(site_ids: list[str] | None) -> list[str]:
    """Validate and normalize a user-selected list of job boards."""
    if site_ids is None:
        return default_job_board_ids()

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in site_ids:
        board_id = (raw or "").strip().lower()
        if not board_id or board_id in seen:
            continue
        if board_id not in JOB_BOARD_META:
            raise ValueError(f"אתר לא נתמך: {board_id}")
        if not is_board_enabled(board_id):
            label = JOB_BOARD_META[board_id]["label_he"]
            raise ValueError(f"האתר '{label}' אינו זמין בשרת")
        normalized.append(board_id)
        seen.add(board_id)

    if not normalized:
        raise ValueError("יש לבחור לפחות אתר אחד לחיפוש משרות")
    return normalized


def job_boards_label(site_ids: list[str]) -> str:
    return " + ".join(site_ids)


def collection_searches(
    site_ids: list[str] | None,
    collectors: dict[str, Callable[..., Any]],
) -> list[tuple[str, Callable[..., Any]]]:
    selected = normalize_job_board_ids(site_ids)
    return [(board_id, collectors[board_id]) for board_id in selected]
