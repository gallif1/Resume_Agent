"""Regression: Agent 2 must not hard-fail when the model omits tailored_resume."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_2_SYSTEM,
    MERGED_AGENT_2_PROMPT_VERSION,
    merged_prompt_contains_legacy_rules,
)
from intelligent_tailoring.schemas import SchemaValidationError
from intelligent_tailoring.services.resume_rewriter import rewrite_resume_with_strategy


def test_agent2_prompt_forbids_triage_only_payload():
    assert "tailored_resume" in AGENT_2_SYSTEM
    assert "Do NOT return a triage" in AGENT_2_SYSTEM or "do not emit triage" in AGENT_2_SYSTEM.lower()
    # Competing triage JSON schema must not remain as an allowed final output
    assert '"triage":' not in AGENT_2_SYSTEM
    assert MERGED_AGENT_2_PROMPT_VERSION.startswith("merged_strategy_v2")
    assert merged_prompt_contains_legacy_rules()["content_triage"]
    assert merged_prompt_contains_legacy_rules()["deep_tailor"]


def test_rewrite_falls_back_when_composed_returns_triage_only():
    calls: list[str] = []

    def fake_call_stage_json(**kwargs):
        calls.append(kwargs.get("cache_namespace") or "")
        system = kwargs.get("system_prompt") or ""
        validate = kwargs.get("validate")
        if "merged_strategy" in (kwargs.get("cache_namespace") or ""):
            # Simulate the production failure: triage-shaped response
            payload = {"triage": [], "section_order": ["skills"]}
            if validate:
                with pytest.raises(SchemaValidationError):
                    validate(payload)
                raise SchemaValidationError("missing tailored_resume")
            raise SchemaValidationError("missing tailored_resume")
        # Deep-tailor fallback succeeds
        payload = {
            "tailored_resume": {
                "professional_title": "Engineer",
                "professional_summary": "Engineer with evidenced Python experience.",
                "skills": ["Languages: Python"],
                "experience": [],
                "projects": [],
                "education": [],
                "certifications": [],
            },
            "change_log": [],
            "matched_requirements": ["Python"],
            "missing_requirements": [],
            "removed_or_deprioritized_content": [],
            "ats_keywords_added": [],
        }
        if validate:
            validate(payload)
        return payload

    with patch(
        "intelligent_tailoring.services.resume_rewriter.call_stage_json",
        side_effect=fake_call_stage_json,
    ):
        result = rewrite_resume_with_strategy(
            resume_facts={"raw_text": "Python developer", "skills": ["Python"]},
            rebuilt_resume={
                "professional_title": "Engineer",
                "professional_summary": "Engineer with evidenced Python experience.",
                "skills": ["Languages: Python"],
                "experience": [],
                "projects": [],
                "education": [],
                "certifications": [],
            },
            strategy={"job_family": "software"},
            scores={},
            ranked_requirements=[],
            inferred=[],
            evidence_map=[],
            triage={"triage": []},
            use_cache=False,
        )

    assert "tailored_resume" in result
    assert result["tailored_resume"]["professional_summary"]
    assert any("merged_strategy" in c for c in calls)
    assert any("fallback" in c for c in calls)


def test_rewrite_uses_rebuilt_when_all_llm_schemas_fail():
    def always_fail(**kwargs):
        raise SchemaValidationError("missing tailored_resume")

    with patch(
        "intelligent_tailoring.services.resume_rewriter.call_stage_json",
        side_effect=always_fail,
    ):
        result = rewrite_resume_with_strategy(
            resume_facts={
                "raw_text": "Python developer",
                "skills": ["Python"],
                "experience_roles": [
                    {
                        "company": "Acme",
                        "title": "Dev",
                        "dates": "2023-2024",
                        "bullets": ["Built APIs"],
                    }
                ],
                "projects": [],
                "education": [],
            },
            rebuilt_resume={
                "professional_title": "Developer",
                "professional_summary": "Developer with API experience.",
                "skills": ["Languages: Python"],
                "experience": [
                    {
                        "company": "Acme",
                        "title": "Dev",
                        "dates": "2023-2024",
                        "bullets": ["Built APIs"],
                    }
                ],
                "projects": [],
                "education": [],
                "certifications": [],
            },
            strategy={"job_family": "software", "genuine_gaps": []},
            scores={},
            ranked_requirements=[],
            inferred=[],
            evidence_map=[],
            triage={},
            use_cache=False,
        )

    assert result.get("_fallback") == "rebuilt_resume"
    assert result["tailored_resume"]["skills"]
