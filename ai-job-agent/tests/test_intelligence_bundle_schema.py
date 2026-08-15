"""Tests for Agent 1 intelligence-bundle schema normalization + fallback."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from intelligent_tailoring.schemas import SchemaValidationError
from intelligent_tailoring.stages.intelligence_bundle import (
    _validate_bundle,
    normalize_intelligence_raw,
    run_intelligence_bundle_llm,
)


def test_normalize_accepts_flat_requirement_payload():
    raw = {
        "required_skills": ["React", "TypeScript"],
        "responsibilities": ["Build UI components"],
        "tools_technologies": ["React"],
        "hard_requirements": ["React"],
        "language": "en",
    }
    out = normalize_intelligence_raw(raw)
    assert isinstance(out["job_requirements"], dict)
    assert out["job_requirements"]["required_skills"] == ["React", "TypeScript"]
    assert out["inferred_competencies"] == []


def test_normalize_accepts_wrapped_and_aliased_payload():
    raw = {
        "result": {
            "requirements": {
                "required_skills": ["Python"],
                "hard_requirements": ["Python"],
                "responsibilities": ["Ship APIs"],
            },
            "competencies": [],
        }
    }
    out = normalize_intelligence_raw(raw)
    assert out["job_requirements"]["required_skills"] == ["Python"]
    assert out["inferred_competencies"] == []


def test_validate_bundle_mutates_flat_payload_in_place():
    data = {
        "required_skills": ["CSS"],
        "tools_technologies": ["CSS"],
        "responsibilities": ["Style components"],
    }
    _validate_bundle(data)
    assert "job_requirements" in data
    assert data["inferred_competencies"] == []


def test_validate_bundle_still_rejects_empty_noise():
    with pytest.raises(SchemaValidationError):
        _validate_bundle({"notes": "hello", "score": 1})


def test_run_intelligence_bundle_accepts_flat_llm_response():
    flat = {
        "required_skills": ["React", "Redux"],
        "preferred_skills": ["TypeScript"],
        "responsibilities": ["Implement frontend features for enterprise apps"],
        "tools_technologies": ["React", "Redux"],
        "industry_terminology": [],
        "seniority_level": "mid",
        "soft_skills": [],
        "education_certifications": [],
        "ats_keywords": ["React"],
        "hard_requirements": ["React"],
        "soft_requirements": ["TypeScript"],
        "language": "en",
    }
    with patch(
        "intelligent_tailoring.stages.intelligence_bundle.call_stage_json",
        return_value=dict(flat),
    ):
        out = run_intelligence_bundle_llm(
            job={
                "title": "Frontend Developer",
                "company": "Bylith",
                "description": (
                    "Looking for a Frontend Developer with React and TypeScript "
                    "experience to build polished UI. " * 4
                ),
            },
            resume_facts={
                "raw_text": "Frontend developer React Redux TypeScript " * 20,
                "skills": ["React", "Redux"],
            },
            use_cache=False,
        )
    assert out["job_requirements"]["required_skills"]
    assert "React" in out["job_requirements"]["required_skills"]
    assert out.get("_deterministic_fallback") is not True


def test_run_intelligence_bundle_falls_back_when_schema_fails():
    with patch(
        "intelligent_tailoring.stages.intelligence_bundle.call_stage_json",
        side_effect=SchemaValidationError(
            "Stage merged_intel_v1_intel_bundle failed after retry: "
            "intelligence bundle missing job_requirements and inferred_competencies"
        ),
    ):
        out = run_intelligence_bundle_llm(
            job={
                "title": "Frontend Developer",
                "company": "Bylith",
                "description": (
                    "We need a Frontend Developer skilled in React, TypeScript, "
                    "and CSS to deliver modern web interfaces. Collaborate with "
                    "design and backend teams. " * 3
                ),
            },
            resume_facts={
                "raw_text": (
                    "Built React and TypeScript interfaces. Used CSS and Redux "
                    "on production apps. " * 10
                ),
                "skills": ["React", "TypeScript", "CSS"],
            },
            use_cache=False,
        )
    assert out.get("_deterministic_fallback") is True
    assert isinstance(out.get("job_requirements"), dict)
    assert isinstance(out.get("inferred_competencies"), list)
    # Should still surface React from the JD via ontology/text cues when possible
    skills = (
        out["job_requirements"].get("required_skills")
        or out["job_requirements"].get("hard_requirements")
        or []
    )
    blob = " ".join(skills).lower()
    assert "react" in blob or out["job_requirements"].get("responsibilities")
