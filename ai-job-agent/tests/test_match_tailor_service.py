"""Tests for the three-phase match + tailor engine.

The four calibration cases at the bottom of this file are the deployment sanity
checks for the prompt: they lock in the Hard Cap Rule, its domain-agnostic
behaviour, and the fact that a soft-requirement gap alone stays a high score.
"""

from __future__ import annotations

from typing import Any

import pytest

import match_tailor_service as svc


def _requirement(text: str, status: str, evidence: str = "") -> dict[str, str]:
    return {
        "requirement": text,
        "candidate_status": status,
        "evidence_or_gap": evidence or f"({status.lower()})",
    }


def _model_payload(
    *,
    hard: list[dict[str, str]],
    soft: list[dict[str, str]],
    realistic_match_score: int,
    hard_cap_applied: bool = False,
    recommendation: str = "APPLY_WITH_HONEST_FRAMING",
    missing_critical_skills: list[str] | None = None,
    skills: list[str] | None = None,
) -> dict[str, Any]:
    """A well-formed model response, so tests only vary what they are asserting."""
    return {
        "requirement_extraction": {
            "hard_requirements": hard,
            "soft_requirements": soft,
        },
        "scoring": {
            "hard_score_pct": 0,
            "soft_score_pct": 0,
            "hard_cap_applied": hard_cap_applied,
            "realistic_match_score": realistic_match_score,
            "score_rationale": "Model rationale.",
        },
        "key_matching_points": ["Python backend work"],
        "missing_critical_skills": missing_critical_skills or [],
        "transferable_skills_framing": [
            {
                "gap": "Salesforce Apex",
                "how_to_honestly_frame_existing_experience": (
                    "Built internal automation tools with Python and AWS Lambda, "
                    "directly applicable to custom business-logic integrations."
                ),
            }
        ],
        "tailored_cv": {
            "summary": "Backend engineer with Python and AWS automation experience.",
            "skills": skills if skills is not None else ["Python", "AWS", "FastAPI"],
            "experience": [
                {
                    "company": "Acme",
                    "title": "Backend Engineer",
                    "dates": "2022-2025",
                    "bullets": ["Built Python services on AWS Lambda."],
                }
            ],
            "projects": [],
            "education": [],
        },
        "recommendation": recommendation,
    }


SOURCE_RESUME = (
    "Backend engineer. Python, FastAPI, React, AWS Lambda, PostgreSQL, Docker, "
    "OpenAI API integrations."
)


# --------------------------------------------------------------------------- #
# Unit behaviour
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MATCH", "MATCH"),
        ("match", "MATCH"),
        ("PARTIAL / TRANSFERABLE", "PARTIAL"),
        ("Transferable", "PARTIAL"),
        ("MISSING", "MISSING"),
        ("no match", "MISSING"),
        ("", "MISSING"),
        (None, "MISSING"),
        ("something weird", "MISSING"),
    ],
)
def test_normalize_status(raw: Any, expected: str):
    assert svc.normalize_status(raw) == expected


def test_core_title_tokens_keeps_subject_matter_only():
    assert svc.core_title_tokens("Senior Salesforce Developer (Apex, LWC)") == [
        "salesforce",
        "apex",
        "lwc",
    ]
    assert svc.core_title_tokens("Marketing Manager") == ["marketing"]
    assert svc.core_title_tokens("Software Engineer") == ["software"]


def test_compute_rubric_scores_follows_the_documented_formula():
    hard = [
        _requirement("Apex", "MATCH"),
        _requirement("LWC", "PARTIAL"),
        _requirement("Integration patterns", "MISSING"),
    ]
    soft = [_requirement("Salesforce certification", "MISSING")]

    rubric = svc.compute_rubric_scores(hard, soft)

    # HARD_SCORE = (1.0 + 0.4 + 0.0) / 3 = 0.4667 ; SOFT_SCORE = 0.0
    assert rubric["hard_score_pct"] == 47
    assert rubric["soft_score_pct"] == 0
    # composite = 0.4667 * 0.75 + 0.0 * 0.25 = 0.35
    assert rubric["composite_score"] == 35


def test_compute_rubric_scores_treats_absent_soft_bucket_as_full_credit():
    hard = [_requirement("Apex", "MATCH"), _requirement("LWC", "MATCH")]
    assert svc.compute_rubric_scores(hard, [])["composite_score"] == 100


def test_align_recommendation_only_downgrades():
    assert svc.align_recommendation("STRONG_APPLY", 90) == "STRONG_APPLY"
    assert svc.align_recommendation("STRONG_APPLY", 60) == "APPLY_WITH_HONEST_FRAMING"
    assert svc.align_recommendation("STRONG_APPLY", 42) == "STRETCH_APPLY_LOW_ODDS"
    assert svc.align_recommendation("STRONG_APPLY", 25) == "DO_NOT_RECOMMEND"
    assert svc.align_recommendation("DO_NOT_RECOMMEND", 95) == "DO_NOT_RECOMMEND"
    assert svc.align_recommendation("nonsense", 80) == "STRONG_APPLY"


def test_validate_schema_keys_rejects_incomplete_payloads():
    with pytest.raises(svc.MatchTailorSchemaError):
        svc.validate_schema_keys({"scoring": {}})
    with pytest.raises(svc.MatchTailorSchemaError):
        svc.validate_schema_keys("not a dict")

    payload = _model_payload(hard=[], soft=[], realistic_match_score=50)
    svc.validate_schema_keys(payload)


def test_normalize_returns_every_schema_field():
    result = svc.normalize_match_tailor_result(
        _model_payload(hard=[], soft=[], realistic_match_score=0),
        job_title="Backend Engineer",
        source_resume_text=SOURCE_RESUME,
    )

    for key in (
        "requirement_extraction",
        "scoring",
        "key_matching_points",
        "missing_critical_skills",
        "transferable_skills_framing",
        "tailored_cv",
        "recommendation",
    ):
        assert key in result
    for key in ("summary", "skills", "experience", "projects", "education"):
        assert key in result["tailored_cv"]
    assert result["tailored_cv"]["education"] == []
    assert result["recommendation"] in svc.VALID_RECOMMENDATIONS


def test_unsupported_skills_are_dropped_from_the_tailored_cv():
    payload = _model_payload(
        hard=[_requirement("Python", "MATCH")],
        soft=[],
        realistic_match_score=90,
        skills=["Python", "FastAPI", "Salesforce Apex"],
    )

    result = svc.normalize_match_tailor_result(
        payload, job_title="Backend Engineer", source_resume_text=SOURCE_RESUME
    )

    assert "Salesforce Apex" not in result["tailored_cv"]["skills"]
    assert "Python" in result["tailored_cv"]["skills"]
    assert result["score_validation"]["dropped_unsupported_skills"] == ["Salesforce Apex"]


def test_find_unsupported_skills_reports_atoms_inside_category_rows():
    unsupported = svc.find_unsupported_skills(
        ["Languages: Python, Apex", "Cloud: AWS"], SOURCE_RESUME
    )
    assert unsupported == ["Apex"]


def test_categorized_skill_row_survives_when_partly_supported():
    payload = _model_payload(
        hard=[_requirement("Python", "MATCH")],
        soft=[],
        realistic_match_score=90,
        skills=["Languages: Python, Apex"],
    )
    result = svc.normalize_match_tailor_result(
        payload, job_title="Backend Engineer", source_resume_text=SOURCE_RESUME
    )
    assert result["tailored_cv"]["skills"] == ["Languages: Python, Apex"]


@pytest.mark.parametrize(
    "skill, source",
    [
        ("PostgreSQL", "Stored data in Postgres for two years"),
        ("Postgres", "Modelled schemas in PostgreSQL"),
        ("CI/CD", "Set up CICD in GitHub Actions"),
        ("Node.js", "Wrote a NodeJS worker"),
        ("Stakeholder communication", "Communicated with stakeholders weekly"),
        ("REST API design", "Designed REST APIs for internal clients"),
        ("Kubernetes", "Deployed to kubernetes clusters"),
    ],
)
def test_reworded_skills_count_as_supported(skill: str, source: str):
    """Normalized matching, so a real skill is not stripped over its spelling."""
    assert svc.skill_supported_by_source(skill, source) is True


@pytest.mark.parametrize(
    "skill",
    ["Salesforce Apex", "Terraform", "Kubernetes Operators", "SAP HANA"],
)
def test_absent_skills_are_still_unsupported(skill: str):
    assert svc.skill_supported_by_source(skill, SOURCE_RESUME) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["- Python", "* FastAPI", "• AWS"], ["Python", "FastAPI", "AWS"]),
        (
            [
                {"category": "Databases", "skills": ["PostgreSQL", "Redis"]},
                {"category": "Languages", "skills": ["Python"]},
            ],
            ["Databases: PostgreSQL, Redis", "Languages: Python"],
        ),
        ([["Python", "SQL"], ["AWS"]], ["Python, SQL", "AWS"]),
        ("Python, FastAPI", ["Python, FastAPI"]),
    ],
)
def test_grouped_or_bulleted_skill_shapes_become_clean_rows(
    raw: object, expected: list[str]
):
    """Schema drift must not put bullets or Python syntax on the resume.

    The model is asked for flat strings, but it also returns bulleted strings and
    ``{"category": ..., "skills": [...]}`` objects. Those are flattened into the
    "Category: a, b" rows the resume renderer understands.
    """
    normalized, _dropped = svc._normalize_tailored_cv(
        {"skills": raw}, source_text=SOURCE_RESUME
    )
    assert normalized["skills"] == expected


def test_prompt_requires_complete_sections():
    from match_tailor_prompt import MATCH_TAILOR_SYSTEM_PROMPT

    prompt = MATCH_TAILOR_SYSTEM_PROMPT
    assert "COMPLETENESS REQUIREMENT" in prompt
    assert "never contain empty or near-empty sections" in prompt
    assert "appear only in Experience/Projects bullets" in prompt
    assert "do not omit real skills" in prompt
    assert "Worked on backend systems." in prompt
    assert "do not fabricate content to fill it" in prompt
    # The completeness block belongs to Phase 3, after the scoring phase.
    assert prompt.index("COMPLETENESS REQUIREMENT") > prompt.index(
        "PHASE 3 — TAILORED RESUME GENERATION"
    )


def test_unmet_core_requirements_ignores_covered_tokens():
    hard = [
        _requirement("Salesforce Apex development", "PARTIAL"),
        _requirement("Python scripting", "MATCH"),
    ]
    assert svc.unmet_core_requirements("Salesforce Developer (Apex)", hard) == []


# --------------------------------------------------------------------------- #
# OpenAI orchestration
# --------------------------------------------------------------------------- #


def test_evaluate_requires_an_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc, "is_ai_available", lambda: False)
    with pytest.raises(svc.MatchTailorError) as excinfo:
        svc.evaluate_candidate_for_job(cv_profile={}, job={"id": 1, "title": "X"})
    assert excinfo.value.status_code == 503


def test_evaluate_retries_once_on_schema_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)
    calls: list[str] = []
    good = _model_payload(
        hard=[_requirement("Python", "MATCH")],
        soft=[],
        realistic_match_score=95,
        recommendation="STRONG_APPLY",
    )

    def fake_call(system_prompt: str, user_prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(system_prompt)
        if len(calls) == 1:
            return {"scoring": {}}
        return dict(good)

    monkeypatch.setattr(svc, "call_openai_json", fake_call)

    result = svc.evaluate_candidate_for_job(
        cv_profile={"raw_text": SOURCE_RESUME},
        job={"id": 7, "title": "Backend Engineer", "description": "Python, AWS"},
    )

    assert len(calls) == 2
    assert "not valid JSON matching the schema" in calls[1]
    assert result["scoring"]["realistic_match_score"] == 100


def test_evaluate_raises_when_the_retry_also_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)
    calls: list[str] = []

    def fake_call(system_prompt: str, user_prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(system_prompt)
        return {"nope": True}

    monkeypatch.setattr(svc, "call_openai_json", fake_call)

    with pytest.raises(svc.MatchTailorError) as excinfo:
        svc.evaluate_candidate_for_job(
            cv_profile={"raw_text": SOURCE_RESUME},
            job={"id": 7, "title": "Backend Engineer"},
        )

    assert len(calls) == 2
    assert excinfo.value.status_code == 502


def test_evaluate_passes_title_and_jd_into_the_user_prompt(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)
    seen: dict[str, Any] = {}

    def fake_call(system_prompt: str, user_prompt: str, **kwargs: Any) -> dict[str, Any]:
        seen["user_prompt"] = user_prompt
        seen["temperature"] = kwargs.get("temperature")
        seen["model"] = kwargs.get("model")
        return _model_payload(
            hard=[_requirement("Python", "MATCH")], soft=[], realistic_match_score=90
        )

    monkeypatch.setattr(svc, "call_openai_json", fake_call)

    svc.evaluate_candidate_for_job(
        cv_profile={"raw_text": SOURCE_RESUME},
        job={
            "id": 3,
            "title": "Salesforce Developer",
            "company": "Dot Compliance",
            "full_description": "Apex, LWC, Python scripting on AWS.",
        },
    )

    prompt = seen["user_prompt"]
    assert "Title: Salesforce Developer" in prompt
    assert "Company: Dot Compliance" in prompt
    assert "Apex, LWC" in prompt
    assert SOURCE_RESUME in prompt
    assert 0.2 <= seen["temperature"] <= 0.3
    assert seen["model"]


# --------------------------------------------------------------------------- #
# Calibration cases (deployment sanity checks)
# --------------------------------------------------------------------------- #


def test_case_1_zero_salesforce_experience_cannot_score_83():
    """The reported failure mode: 83% against a Salesforce-required role."""
    payload = _model_payload(
        hard=[
            _requirement("Salesforce Apex development", "MISSING", "No Apex on resume"),
            _requirement("Salesforce LWC development", "MISSING", "No LWC on resume"),
            _requirement("Python scripting", "MATCH", "FastAPI services in Python"),
            _requirement("AWS automation", "MATCH", "AWS Lambda automation"),
        ],
        soft=[
            _requirement("CI/CD pipelines", "MATCH", "GitHub Actions"),
            _requirement("Regulated life-sciences background", "MISSING"),
        ],
        realistic_match_score=83,
        hard_cap_applied=False,
        recommendation="STRONG_APPLY",
    )

    result = svc.normalize_match_tailor_result(
        payload,
        job_title="Salesforce Developer (Apex, LWC)",
        source_resume_text=SOURCE_RESUME,
    )
    scoring = result["scoring"]

    assert 30 <= scoring["realistic_match_score"] <= 50
    assert scoring["hard_cap_applied"] is True
    assert result["score_validation"]["model_reported_score"] == 83
    assert result["score_validation"]["score_overridden"] is True
    named = " ".join(result["missing_critical_skills"]).lower()
    assert "salesforce" in named and "apex" in named
    assert result["recommendation"] == "STRETCH_APPLY_LOW_ODDS"
    assert "Hard Cap Rule applied" in scoring["score_rationale"]


def test_case_2_direct_match_scores_high_without_a_cap():
    payload = _model_payload(
        hard=[
            _requirement("5+ years Salesforce Apex development", "MATCH"),
            _requirement("Salesforce LWC development", "MATCH"),
            _requirement("Integration patterns (REST/SOAP)", "PARTIAL"),
        ],
        soft=[_requirement("Salesforce Platform Developer I certification", "MATCH")],
        realistic_match_score=85,
        recommendation="STRONG_APPLY",
    )

    result = svc.normalize_match_tailor_result(
        payload, job_title="Salesforce Developer", source_resume_text=""
    )

    assert result["scoring"]["realistic_match_score"] >= 80
    assert result["scoring"]["hard_cap_applied"] is False
    assert result["recommendation"] == "STRONG_APPLY"


def test_case_3_non_technical_gap_caps_identically():
    """Domain-agnostic: a paid-social gap behaves like the Salesforce gap."""
    payload = _model_payload(
        hard=[
            _requirement(
                "3+ years managing paid social ad spend", "MISSING", "Email only"
            ),
            _requirement("Campaign reporting and analytics", "MATCH"),
            _requirement("Content strategy ownership", "MATCH"),
        ],
        soft=[_requirement("HubSpot experience", "MATCH")],
        realistic_match_score=78,
        hard_cap_applied=True,
        recommendation="STRONG_APPLY",
    )

    result = svc.normalize_match_tailor_result(
        payload, job_title="Marketing Manager", source_resume_text=""
    )

    assert result["scoring"]["hard_cap_applied"] is True
    assert result["scoring"]["realistic_match_score"] <= 55
    assert result["recommendation"] != "STRONG_APPLY"


def test_case_4_soft_requirement_gap_keeps_a_high_score():
    payload = _model_payload(
        hard=[
            _requirement("5+ years Salesforce Apex development", "MATCH"),
            _requirement("Salesforce LWC development", "MATCH"),
            _requirement("Integration patterns (REST/SOAP)", "MATCH"),
        ],
        soft=[
            _requirement("Salesforce Platform Developer II certification", "MISSING"),
            _requirement("Agile delivery", "MATCH"),
        ],
        realistic_match_score=88,
        recommendation="STRONG_APPLY",
    )

    result = svc.normalize_match_tailor_result(
        payload, job_title="Salesforce Developer", source_resume_text=""
    )

    assert 75 <= result["scoring"]["realistic_match_score"] <= 90
    assert result["scoring"]["hard_cap_applied"] is False
    assert result["scoring"]["hard_score_pct"] == 100
    assert result["scoring"]["soft_score_pct"] == 50
