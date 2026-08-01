"""Component-level caches for ResumeKnowledgeBase, JobProfile, CompanyProfile.

Avoid rebuilding deterministic knowledge / company analysis for every job, and
reuse JobProfile when the same JD is regenerated.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from config import AI_CACHE_DIR
from intelligent_tailoring.schemas import PIPELINE_VERSION

_LOCK = threading.Lock()
_MEMORY: dict[str, Any] = {}

PARSER_VERSION = "resume_parser_v1"
ONTOLOGY_VERSION = "ontology_v1"
COMPANY_PROMPT_VERSION = "company_intel_v1"
JOB_PROMPT_VERSION = "job_intel_v1"

CACHE_ROOT = AI_CACHE_DIR / "intelligent_tailor_components"


def _hash_payload(payload: str) -> str:
    return hashlib.sha256((payload or "").encode("utf-8")).hexdigest()


def _disk_path(namespace: str, key: str) -> Path:
    directory = CACHE_ROOT / namespace
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{key}.json"


def _memory_get(key: str) -> Any | None:
    with _LOCK:
        return _MEMORY.get(key)


def _memory_set(key: str, value: Any) -> None:
    with _LOCK:
        # Bound memory cache roughly
        if len(_MEMORY) > 256:
            _MEMORY.clear()
        _MEMORY[key] = value


def _read_disk(namespace: str, key: str) -> dict[str, Any] | None:
    path = _disk_path(namespace, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_disk(namespace: str, key: str, payload: dict[str, Any]) -> None:
    path = _disk_path(namespace, key)
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def knowledge_cache_key(*, resume_content_hash: str) -> str:
    return _hash_payload(
        f"{PIPELINE_VERSION}|{PARSER_VERSION}|{ONTOLOGY_VERSION}|{resume_content_hash}"
    )


def job_cache_key(*, company: str, title: str, jd_hash: str) -> str:
    return _hash_payload(
        f"{PIPELINE_VERSION}|{JOB_PROMPT_VERSION}|{company}|{title}|{jd_hash}"
    )


def company_cache_key(*, company_id: str, metadata_hash: str) -> str:
    return _hash_payload(
        f"{PIPELINE_VERSION}|{COMPANY_PROMPT_VERSION}|{company_id}|{metadata_hash}"
    )


def get_cached_knowledge(resume_content_hash: str) -> dict[str, Any] | None:
    key = knowledge_cache_key(resume_content_hash=resume_content_hash)
    mem = _memory_get(f"kb:{key}")
    if isinstance(mem, dict):
        return {**mem, "_cache_hit": True}
    disk = _read_disk("knowledge", key)
    if disk is not None:
        _memory_set(f"kb:{key}", disk)
        return {**disk, "_cache_hit": True}
    return None


def set_cached_knowledge(resume_content_hash: str, payload: dict[str, Any]) -> None:
    key = knowledge_cache_key(resume_content_hash=resume_content_hash)
    clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    _memory_set(f"kb:{key}", clean)
    _write_disk("knowledge", key, clean)


def get_cached_job_profile(company: str, title: str, jd_hash: str) -> dict[str, Any] | None:
    key = job_cache_key(company=company, title=title, jd_hash=jd_hash)
    mem = _memory_get(f"job:{key}")
    if isinstance(mem, dict):
        return {**mem, "_cache_hit": True}
    disk = _read_disk("job", key)
    if disk is not None:
        _memory_set(f"job:{key}", disk)
        return {**disk, "_cache_hit": True}
    return None


def set_cached_job_profile(
    company: str, title: str, jd_hash: str, payload: dict[str, Any]
) -> None:
    key = job_cache_key(company=company, title=title, jd_hash=jd_hash)
    clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    _memory_set(f"job:{key}", clean)
    _write_disk("job", key, clean)


def get_cached_company_profile(
    company_id: str, metadata_hash: str
) -> dict[str, Any] | None:
    key = company_cache_key(company_id=company_id, metadata_hash=metadata_hash)
    mem = _memory_get(f"co:{key}")
    if isinstance(mem, dict):
        return {**mem, "_cache_hit": True}
    disk = _read_disk("company", key)
    if disk is not None:
        _memory_set(f"co:{key}", disk)
        return {**disk, "_cache_hit": True}
    return None


def set_cached_company_profile(
    company_id: str, metadata_hash: str, payload: dict[str, Any]
) -> None:
    key = company_cache_key(company_id=company_id, metadata_hash=metadata_hash)
    clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    _memory_set(f"co:{key}", clean)
    _write_disk("company", key, clean)


def clear_component_caches() -> None:
    with _LOCK:
        _MEMORY.clear()
    if CACHE_ROOT.exists():
        for path in CACHE_ROOT.rglob("*.json"):
            try:
                path.unlink()
            except OSError:
                pass
