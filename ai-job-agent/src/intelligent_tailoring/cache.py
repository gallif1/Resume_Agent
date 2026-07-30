"""Cache tailored results keyed on (resume version hash, JD snapshot hash)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from config import AI_CACHE_DIR
from intelligent_tailoring.schemas import PIPELINE_VERSION

CACHE_DIR = AI_CACHE_DIR / "intelligent_tailor"
CACHE_NAMESPACE = f"intelligent_tailor_{PIPELINE_VERSION}"


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def cache_key(
    *,
    resume_hash: str,
    jd_hash: str,
    language: str = "en",
    pipeline_version: str = PIPELINE_VERSION,
) -> str:
    payload = f"{pipeline_version}|{language}|{resume_hash}|{jd_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def read_tailoring_cache(
    *,
    resume_text: str,
    jd_text: str,
    language: str = "en",
) -> dict[str, Any] | None:
    key = cache_key(
        resume_hash=content_hash(resume_text),
        jd_hash=content_hash(jd_text),
        language=language,
    )
    path = cache_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    data["_from_cache"] = True
    data["from_cache"] = True
    return data


def write_tailoring_cache(
    result: dict[str, Any],
    *,
    resume_text: str,
    jd_text: str,
    language: str = "en",
) -> str:
    key = cache_key(
        resume_hash=content_hash(resume_text),
        jd_hash=content_hash(jd_text),
        language=language,
    )
    path = cache_path(key)
    payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
    payload["from_cache"] = False
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return key


def invalidate_tailoring_cache(
    *,
    resume_text: str | None = None,
    jd_text: str | None = None,
    language: str = "en",
) -> None:
    """Drop a specific cache entry, or the whole intelligent-tailor cache dir."""
    if resume_text is None or jd_text is None:
        if CACHE_DIR.exists():
            for path in CACHE_DIR.glob("*.json"):
                try:
                    path.unlink()
                except OSError:
                    pass
        return
    key = cache_key(
        resume_hash=content_hash(resume_text),
        jd_hash=content_hash(jd_text),
        language=language,
    )
    path = cache_path(key)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
