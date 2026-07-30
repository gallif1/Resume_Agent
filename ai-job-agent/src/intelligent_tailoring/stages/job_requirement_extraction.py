"""Stage 2 — Job requirement extraction (LLM)."""

from __future__ import annotations

import json
from typing import Any

from ai_client import truncate_text
from config import OPENAI_JOB_MAX_CHARS
from intelligent_tailoring.llm_utils import call_stage_json
from intelligent_tailoring.prompts.stage_prompts import (
    JOB_REQUIREMENT_EXTRACTION_SYSTEM,
    build_job_requirement_user_prompt,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION, SchemaValidationError
from job_analyzer import parse_stored_job_profile
from match_tailor_service import build_job_payload

_REQUIRED_KEYS = (
    "required_skills",
    "preferred_skills",
    "responsibilities",
    "tools_technologies",
    "industry_terminology",
    "seniority_level",
    "soft_skills",
    "education_certifications",
    "ats_keywords",
)


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
    return items


def validate_requirements(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SchemaValidationError("job requirements must be an object")
    # Soft-fill missing keys rather than hard-failing on partial extractions
    # from smaller models — but require at least one list-like field.
    normalized = {key: _as_str_list(data.get(key)) for key in _REQUIRED_KEYS if key != "seniority_level"}
    normalized["seniority_level"] = str(data.get("seniority_level") or "").strip()
    normalized["hard_requirements"] = _as_str_list(
        data.get("hard_requirements")
    ) or list(normalized["required_skills"])
    normalized["soft_requirements"] = _as_str_list(
        data.get("soft_requirements")
    ) or list(normalized["preferred_skills"])
    normalized["language"] = str(data.get("language") or "en").strip() or "en"
    if not any(
        normalized[k]
        for k in (
            "required_skills",
            "preferred_skills",
            "responsibilities",
            "hard_requirements",
            "tools_technologies",
        )
    ):
        # Allow empty JD — return sparse structure; caller handles edge case.
        pass
    for key in _REQUIRED_KEYS:
        if key == "seniority_level":
            continue
        if key not in data and key not in normalized:
            raise SchemaValidationError(f"missing requirements key: {key}")
    return normalized


def extract_job_requirements(
    job: dict[str, Any],
    *,
    use_cache: bool = True,
    jd_snapshot: str | None = None,
) -> dict[str, Any]:
    job_profile = parse_stored_job_profile(job.get("job_profile"))
    jd_text = jd_snapshot or build_job_payload(job, job_profile)
    jd_text = truncate_text(jd_text, OPENAI_JOB_MAX_CHARS)
    title = str(job.get("title") or "")
    company = str(job.get("company") or "")

    if len(jd_text.strip()) < 40:
        return {
            "required_skills": [],
            "preferred_skills": [],
            "responsibilities": [],
            "tools_technologies": [],
            "industry_terminology": [],
            "seniority_level": "",
            "soft_skills": [],
            "education_certifications": [],
            "ats_keywords": [],
            "hard_requirements": [],
            "soft_requirements": [],
            "language": "en",
            "sparse": True,
            "jd_text": jd_text,
        }

    raw = call_stage_json(
        system_prompt=JOB_REQUIREMENT_EXTRACTION_SYSTEM,
        user_prompt=build_job_requirement_user_prompt(
            job_title=title, company=company, jd_text=jd_text
        ),
        validate=lambda d: validate_requirements(d),
        use_cache=use_cache,
        cache_namespace=f"{PIPELINE_VERSION}_job_req",
        cache_payload=f"{title}|{jd_text[:3000]}",
    )
    result = validate_requirements(raw)
    result["sparse"] = False
    result["jd_text"] = jd_text
    result["_from_cache"] = bool(raw.get("_from_cache"))
    return result


def requirements_json(requirements: dict[str, Any]) -> str:
    payload = {
        k: v
        for k, v in requirements.items()
        if not str(k).startswith("_") and k != "jd_text"
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
