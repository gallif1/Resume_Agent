"""Shared OpenAI client utilities: JSON calls, parsing, and file-based caching."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import (
    AI_CACHE_DIR,
    OPENAI_API_KEY,
    OPENAI_JOB_MAX_CHARS,
    OPENAI_MODEL,
)

VALID_DECISIONS = frozenset({"HIGH_MATCH", "MEDIUM_MATCH", "LOW_MATCH", "REJECT"})
VALID_ACTIONS = frozenset({"APPLY_NOW", "APPLY_IF_DESPERATE", "SKIP"})


class OpenAIAPIError(RuntimeError):
    """Raised when OpenAI is required but unavailable or failing."""


def is_ai_available() -> bool:
    """True when an OpenAI API key is configured."""
    return bool(OPENAI_API_KEY)


def require_openai_api() -> None:
    """Ensure an API key is configured. Raises OpenAIAPIError if not."""
    if not is_ai_available():
        raise OpenAIAPIError(
            "OPENAI_API_KEY is not set in .env. "
            "Add your API key or pass --fallback-only to skip AI."
        )


def verify_openai_api() -> None:
    """Verify the OpenAI API key works before starting AI operations."""
    require_openai_api()

    from openai import OpenAI

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        next(iter(client.models.list()), None)
    except StopIteration:
        pass
    except Exception as exc:
        raise OpenAIAPIError(
            f"OpenAI API is not available. Check your key, model, and network.\n"
            f"Details: {exc}"
        ) from exc


def truncate_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[... truncated ...]"


def parse_json_response(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError("Empty response from OpenAI")

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fenced:
        text = fenced.group(1).strip()

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("OpenAI response is not a JSON object")
    return data


def cache_key(namespace: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{namespace}_{digest}"


def read_cache(namespace: str, payload: str) -> dict[str, Any] | None:
    path = AI_CACHE_DIR / f"{cache_key(namespace, payload)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def write_cache(namespace: str, payload: str, result: dict[str, Any]) -> None:
    AI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = AI_CACHE_DIR / f"{cache_key(namespace, payload)}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def call_openai_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.2,
    use_cache: bool = True,
    cache_namespace: str = "openai",
    cache_payload: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    max_completion_tokens: int | None = None,
) -> dict[str, Any]:
    """Call OpenAI with JSON response format. Optional file cache on cache_payload."""
    require_openai_api()

    selected_model = (model or OPENAI_MODEL).strip() or OPENAI_MODEL
    payload = cache_payload or f"{selected_model}\n---\n{system_prompt}\n---\n{user_prompt}"
    if use_cache:
        cached = read_cache(cache_namespace, payload)
        if cached is not None:
            cached["_from_cache"] = True
            return cached

    from openai import OpenAI

    # GPT-5 family often rejects custom temperature; omit it for those models.
    model_l = selected_model.lower()
    supports_temperature = not (
        model_l.startswith("gpt-5")
        or model_l.startswith("o1")
        or model_l.startswith("o3")
        or model_l.startswith("o4")
    )
    create_kwargs: dict[str, Any] = {
        "model": selected_model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if supports_temperature:
        create_kwargs["temperature"] = temperature
    if reasoning_effort:
        create_kwargs["reasoning_effort"] = reasoning_effort
    if verbosity:
        create_kwargs["verbosity"] = verbosity
    if max_completion_tokens is not None and max_completion_tokens > 0:
        create_kwargs["max_completion_tokens"] = int(max_completion_tokens)

    def _create(kwargs: dict[str, Any]):
        client = OpenAI(api_key=OPENAI_API_KEY, timeout=120.0)
        return client.chat.completions.create(**kwargs)

    try:
        response = _create(create_kwargs)
    except Exception as exc:
        # Drop unsupported knobs once and retry (temperature / reasoning / verbosity).
        err = str(exc).lower()
        dropped = []
        for key in ("temperature", "reasoning_effort", "verbosity", "max_completion_tokens"):
            if key in create_kwargs and key in err:
                create_kwargs.pop(key, None)
                dropped.append(key)
        if not dropped and "temperature" in create_kwargs and (
            not supports_temperature or "temperature" in err
        ):
            create_kwargs.pop("temperature", None)
            dropped.append("temperature")
        if not dropped:
            raise OpenAIAPIError(f"OpenAI request failed: {exc}") from exc
        try:
            response = _create(create_kwargs)
        except Exception as retry_exc:
            raise OpenAIAPIError(f"OpenAI request failed: {retry_exc}") from retry_exc

    try:
        content = response.choices[0].message.content or ""
        result = parse_json_response(content)
    except Exception as exc:
        raise OpenAIAPIError(f"Invalid OpenAI response: {exc}") from exc
    result["_from_cache"] = False
    result["_cached_at"] = datetime.now(timezone.utc).isoformat()
    # Surface token timing hints for ops (not part of the tailored CV schema).
    try:
        usage = getattr(response, "usage", None)
        if usage is not None:
            result["_usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
            details = getattr(usage, "completion_tokens_details", None)
            if details is not None:
                result["_usage"]["reasoning_tokens"] = getattr(
                    details, "reasoning_tokens", None
                )
    except Exception:  # noqa: BLE001 — never fail parse because of usage metadata
        pass

    if use_cache:
        write_cache(cache_namespace, payload, result)

    return result


def clamp_score(value: Any, default: int = 0) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        score = default
    return max(0, min(100, score))


def normalize_string_list(value: Any, max_items: int | None = 15) -> list[str]:
    """Normalize a list of strings with optional de-dupe and length cap.

    When ``max_items`` is ``None``, all non-empty unique items are kept.
    """
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
        if max_items is not None and len(items) >= max_items:
            break
    return items


def summarize_job_text(title: str, company: str, location: str, description: str) -> str:
    """Compact job text for API calls."""
    parts = [
        f"Title: {title or 'N/A'}",
        f"Company: {company or 'N/A'}",
        f"Location: {location or 'N/A'}",
        "Description:",
        truncate_text(description, OPENAI_JOB_MAX_CHARS),
    ]
    return "\n".join(parts)
