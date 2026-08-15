"""Regression: smart agent must not hard-fail when the model omits tailored_resume."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_2_SYSTEM,
    MERGED_AGENT_2_PROMPT_VERSION,
    SMART_AGENT_PROMPT_VERSION,
    SMART_AGENT_SYSTEM,
    merged_prompt_contains_legacy_rules,
)
from intelligent_tailoring.schemas import SchemaValidationError
from intelligent_tailoring.services.resume_rewriter import rewrite_resume_with_strategy
from intelligent_tailoring.structured_validation import ValidationReport


def test_smart_agent_prompt_forbids_triage_only_payload():
    assert "tailored_resume" in SMART_AGENT_SYSTEM
    assert "Do NOT return a triage" in SMART_AGENT_SYSTEM or "do not emit triage" in SMART_AGENT_SYSTEM.lower()
    assert '"triage":' not in SMART_AGENT_SYSTEM
    assert SMART_AGENT_PROMPT_VERSION.startswith("smart_resume")
    assert merged_prompt_contains_legacy_rules()["content_triage"]
    assert merged_prompt_contains_legacy_rules()["deep_tailor"]
    assert merged_prompt_contains_legacy_rules()["smart_agent_present"]
    # Legacy Agent 2 prompt still available for fallback
    assert "100% CONTENT RULE" in AGENT_2_SYSTEM
    assert MERGED_AGENT_2_PROMPT_VERSION.startswith("merged_strategy_v")
    assert "WEAK-MATCH FULLNESS" in AGENT_2_SYSTEM


def test_rewrite_falls_back_when_composed_returns_triage_only():
    calls: list[str] = []

    def fake_call_with_validation(**kwargs):
        calls.append(kwargs.get("cache_namespace") or "")
        validate = kwargs.get("validate")
        content_validate = kwargs.get("content_validate")
        ns = kwargs.get("cache_namespace") or ""
        if "smart_resume" in ns or "merged_strategy" in ns:
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
                "contact": {},
            },
            "change_log": [],
            "matched_requirements": ["Python"],
            "missing_requirements": [],
            "removed_or_deprioritized_content": [],
            "ats_keywords_added": [],
        }
        if validate:
            validate(payload)
        if content_validate:
            report = content_validate(payload)
            if not getattr(report, "passed", True):
                payload["_content_validation"] = (
                    report.to_dict() if hasattr(report, "to_dict") else {"passed": False}
                )
                payload["_content_validation_failed"] = True
            else:
                payload["_content_validation"] = {"passed": True}
        return payload

    with patch(
        "intelligent_tailoring.services.resume_rewriter.call_stage_json_with_content_validation",
        side_effect=fake_call_with_validation,
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
    assert any("smart_resume" in c for c in calls)
    assert any("fallback" in c or "deep_rewrite" in c for c in calls)


def test_rewrite_uses_rebuilt_when_all_llm_schemas_fail():
    def always_fail(**kwargs):
        raise SchemaValidationError("missing tailored_resume")

    with patch(
        "intelligent_tailoring.services.resume_rewriter.call_stage_json_with_content_validation",
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
    # Stable ids stamped even on deterministic fallback
    assert result["tailored_resume"]["experience"][0].get("id") or result[
        "tailored_resume"
    ]["experience"][0].get("source_entry_id")


def test_content_validation_feedback_triggers_regen():
    """Deterministic validation failure must re-invoke with specific feedback."""
    from intelligent_tailoring.llm_utils import call_stage_json_with_content_validation

    invocations: list[str] = []

    def fake_openai_stage(**kwargs):
        system = kwargs.get("system_prompt") or ""
        invocations.append(system)
        payload = {
            "tailored_resume": {
                "professional_title": "Engineer",
                "professional_summary": "Engineer with Python experience building APIs.",
                "skills": ["Languages: Python"],
                "experience": [],
                "projects": [],
                "education": [],
                "certifications": [],
            }
        }
        validate = kwargs.get("validate")
        if validate:
            validate(payload)
        return payload

    reports = [
        ValidationReport(passed=False, issues=[], structured={}),
        ValidationReport(passed=True, issues=[], structured={}),
    ]
    # First report needs a real issue so feedback is non-empty
    from intelligent_tailoring.structured_validation import ValidationIssue

    reports[0] = ValidationReport(
        passed=False,
        issues=[
            ValidationIssue(
                code="missing_experience_id",
                message="Base experience id 'role_0' is missing.",
                path="experience.id=role_0",
            )
        ],
        structured={},
    )

    idx = {"n": 0}

    def content_validate(_payload):
        report = reports[min(idx["n"], len(reports) - 1)]
        idx["n"] += 1
        return report

    with patch(
        "intelligent_tailoring.llm_utils.call_stage_json",
        side_effect=fake_openai_stage,
    ):
        result = call_stage_json_with_content_validation(
            system_prompt="SYSTEM",
            user_prompt="USER",
            validate=None,
            content_validate=content_validate,
            use_cache=False,
            cache_namespace="test_ns",
            cache_payload="x",
            max_content_retries=1,
        )

    assert result.get("_content_validation_repaired") is True
    assert idx["n"] >= 2
    assert any("missing_experience_id" in s for s in invocations[1:])
