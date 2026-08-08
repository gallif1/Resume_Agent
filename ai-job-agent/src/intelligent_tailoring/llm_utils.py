"""Shared LLM call helper with one schema-retry for pipeline stages."""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar
from typing import Any, Callable

from ai_client import OpenAIAPIError, call_openai_json
from config import OPENAI_TAILOR_MODEL
from intelligent_tailoring.schemas import JSON_RETRY_NOTE, SchemaValidationError

logger = logging.getLogger("intelligent_tailoring")

DEFAULT_TEMPERATURE = 0.2

CONTENT_VALIDATION_RETRY_NOTE = (
    "Your previous structured resume failed DETERMINISTIC validation. "
    "Fix ONLY the listed issues. Return STRICT JSON matching the required "
    "schema. Do not drop base experience/project ids. Do not emit raw "
    "dicts/lists inside string fields."
)

# Per-generation primary LLM call accounting (four-agent pipeline).
_primary_calls: ContextVar[list[str] | None] = ContextVar(
    "intelligent_tailoring_primary_llm_calls", default=None
)
_token_stats: ContextVar[dict[str, int] | None] = ContextVar(
    "intelligent_tailoring_token_stats", default=None
)
_lock = threading.Lock()


def begin_llm_metrics() -> None:
    """Reset primary-call counters for a new generation run."""
    _primary_calls.set([])
    _token_stats.set(
        {
            "primary_llm_calls": 0,
            "stage_json_invocations": 0,
            "stage_json_retries": 0,
            "cache_hits": 0,
        }
    )


def record_primary_llm_call(agent_id: str) -> None:
    """Record a primary (merged-agent) LLM call."""
    calls = _primary_calls.get()
    if calls is None:
        calls = []
        _primary_calls.set(calls)
    calls.append(agent_id)
    stats = _token_stats.get()
    if stats is not None:
        stats["primary_llm_calls"] = len(calls)


def get_llm_metrics() -> dict[str, Any]:
    calls = list(_primary_calls.get() or [])
    stats = dict(_token_stats.get() or {})
    return {
        "primary_llm_calls": len(calls),
        "primary_llm_call_agents": calls,
        **stats,
    }


def call_stage_json(
    *,
    system_prompt: str,
    user_prompt: str,
    validate: Callable[[dict[str, Any]], Any] | None = None,
    use_cache: bool = True,
    cache_namespace: str,
    cache_payload: str,
    temperature: float = DEFAULT_TEMPERATURE,
    model: str | None = None,
    count_as_primary: str | None = None,
) -> dict[str, Any]:
    """Call OpenAI JSON once; on schema/JSON failure retry once with correction note."""

    def _invoke(system: str, *, allow_cache: bool, payload: str) -> dict[str, Any]:
        raw = call_openai_json(
            system,
            user_prompt,
            temperature=temperature,
            model=model or OPENAI_TAILOR_MODEL,
            use_cache=allow_cache and use_cache,
            cache_namespace=cache_namespace,
            cache_payload=payload,
        )
        if not isinstance(raw, dict):
            raise SchemaValidationError("LLM response is not a JSON object")
        if validate is not None:
            validate(raw)
        return raw

    stats = _token_stats.get()
    if stats is not None:
        stats["stage_json_invocations"] = int(stats.get("stage_json_invocations") or 0) + 1

    if count_as_primary:
        record_primary_llm_call(count_as_primary)

    try:
        result = _invoke(system_prompt, allow_cache=True, payload=cache_payload)
        if stats is not None and result.get("_from_cache"):
            stats["cache_hits"] = int(stats.get("cache_hits") or 0) + 1
        return result
    except (OpenAIAPIError, SchemaValidationError, ValueError, TypeError) as first_error:
        logger.warning(
            "intelligent_tailoring stage %s failed (%s) — retrying once",
            cache_namespace,
            first_error,
        )
        if stats is not None:
            stats["stage_json_retries"] = int(stats.get("stage_json_retries") or 0) + 1
        try:
            return _invoke(
                f"{system_prompt}\n\n{JSON_RETRY_NOTE}",
                allow_cache=False,
                payload=f"retry|{cache_payload}",
            )
        except (OpenAIAPIError, SchemaValidationError, ValueError, TypeError) as retry_error:
            raise SchemaValidationError(
                f"Stage {cache_namespace} failed after retry: {retry_error}"
            ) from retry_error


def call_stage_json_with_content_validation(
    *,
    system_prompt: str,
    user_prompt: str,
    validate: Callable[[dict[str, Any]], Any] | None = None,
    content_validate: Callable[[dict[str, Any]], Any],
    use_cache: bool = True,
    cache_namespace: str,
    cache_payload: str,
    temperature: float = DEFAULT_TEMPERATURE,
    model: str | None = None,
    count_as_primary: str | None = None,
    max_content_retries: int = 1,
) -> dict[str, Any]:
    """Call JSON stage, then run deterministic content validation with feedback regen.

    ``content_validate`` must return an object with ``.passed`` and
    ``.feedback_for_agent()`` (see ``structured_validation.ValidationReport``).
    On failure, re-invokes the LLM once with the specific validation errors.
    """
    raw = call_stage_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        validate=validate,
        use_cache=use_cache,
        cache_namespace=cache_namespace,
        cache_payload=cache_payload,
        temperature=temperature,
        model=model,
        count_as_primary=count_as_primary,
    )
    report = content_validate(raw)
    if getattr(report, "passed", False):
        raw["_content_validation"] = (
            report.to_dict() if hasattr(report, "to_dict") else {"passed": True}
        )
        return raw

    stats = _token_stats.get()
    attempts = max(0, int(max_content_retries))
    last_report = report
    for attempt in range(1, attempts + 1):
        feedback = ""
        if hasattr(last_report, "feedback_for_agent"):
            feedback = str(last_report.feedback_for_agent() or "")
        if not feedback:
            feedback = "Content validation failed. Return corrected structured JSON."
        logger.warning(
            "intelligent_tailoring stage %s content validation failed "
            "(attempt %d/%d): %s",
            cache_namespace,
            attempt,
            attempts,
            getattr(last_report, "error_codes", lambda: [])(),
        )
        if stats is not None:
            stats["stage_json_retries"] = int(stats.get("stage_json_retries") or 0) + 1
            stats["content_validation_retries"] = (
                int(stats.get("content_validation_retries") or 0) + 1
            )
        try:
            raw = call_stage_json(
                system_prompt=(
                    f"{system_prompt}\n\n{CONTENT_VALIDATION_RETRY_NOTE}\n\n"
                    f"{feedback}"
                ),
                user_prompt=(
                    f"{user_prompt}\n\n=== VALIDATION FEEDBACK (fix these) ===\n"
                    f"{feedback}\n"
                ),
                validate=validate,
                use_cache=False,
                cache_namespace=f"{cache_namespace}_content_retry",
                cache_payload=f"content_retry{attempt}|{cache_payload}",
                temperature=min(0.35, temperature + 0.05),
                model=model,
                count_as_primary=None,
            )
        except SchemaValidationError as exc:
            logger.warning(
                "content validation regen schema-failed for %s: %s",
                cache_namespace,
                exc,
            )
            break
        last_report = content_validate(raw)
        if getattr(last_report, "passed", False):
            raw["_content_validation"] = (
                last_report.to_dict()
                if hasattr(last_report, "to_dict")
                else {"passed": True}
            )
            raw["_content_validation_repaired"] = True
            return raw

    raw["_content_validation"] = (
        last_report.to_dict()
        if hasattr(last_report, "to_dict")
        else {"passed": False}
    )
    raw["_content_validation_failed"] = True
    return raw
