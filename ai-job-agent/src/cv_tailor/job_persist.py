"""Bridge CV Tailor MVP output into per-job tailored-CV history."""

from __future__ import annotations

import logging
from typing import Any

from tailor_cv_service import TailorCvError, persist_mvp_tailored_cv_for_user

logger = logging.getLogger("cv_tailor.job_persist")


def maybe_persist_tailored_cv_to_job(
    *,
    cv_id: str | None,
    job_id: int | None,
    preview_text: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Save MVP output to job history when cv/job ids are provided."""
    if not cv_id or job_id is None:
        return None
    markdown = (preview_text or "").strip()
    if not markdown:
        return None
    try:
        return persist_mvp_tailored_cv_for_user(
            cv_id,
            int(job_id),
            markdown,
            user_id=user_id,
        )
    except TailorCvError as exc:
        logger.warning(
            "Failed to persist CV tailor output for cv=%s job=%s: %s",
            cv_id,
            job_id,
            exc.message,
        )
        return None
    except Exception:
        logger.exception(
            "Unexpected failure persisting CV tailor output for cv=%s job=%s",
            cv_id,
            job_id,
        )
        return None
