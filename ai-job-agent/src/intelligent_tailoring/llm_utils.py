"""Shared LLM call helper with one schema-retry for pipeline stages."""

from __future__ import annotations

import logging
from typing import Any, Callable

from ai_client import OpenAIAPIError, call_openai_json
from config import OPENAI_TAILOR_MODEL
from intelligent_tailoring.schemas import JSON_RETRY_NOTE, SchemaValidationError

logger = logging.getLogger("intelligent_tailoring")

DEFAULT_TEMPERATURE = 0.2


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

    try:
        return _invoke(system_prompt, allow_cache=True, payload=cache_payload)
    except (OpenAIAPIError, SchemaValidationError, ValueError, TypeError) as first_error:
        logger.warning(
            "intelligent_tailoring stage %s failed (%s) — retrying once",
            cache_namespace,
            first_error,
        )
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
