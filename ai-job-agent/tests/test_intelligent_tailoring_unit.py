"""Unit tests for Intelligent Resume Tailoring core modules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intelligent_tailoring.cache import (
    invalidate_tailoring_cache,
    read_tailoring_cache,
    write_tailoring_cache,
)
from intelligent_tailoring.claim_validator import (
    statement_supported_by_evidence,
    validate_claims,
)
from intelligent_tailoring.experience_math import (
    claim_years_supported,
    estimate_years_from_text,
    extract_years_claims,
    parse_date_range,
    years_between,
    years_from_experience_entries,
)
from intelligent_tailoring.ontology import (
    clear_ontology_cache,
    dedupe_skills,
    get_ontology,
    load_ontology,
)
from intelligent_tailoring.schemas import (
    SchemaValidationError,
    TailoredResume,
    validate_tailoring_result,
)
from intelligent_tailoring.stages.evidence_mapping import build_evidence_map
from intelligent_tailoring.stages.requirement_ranking import rank_requirements
from intelligent_tailoring.pipeline import apply_change_decisions, detect_language


# --------------------------------------------------------------------------- #
# Ontology
# --------------------------------------------------------------------------- #


def test_ontology_maps_java_to_oop():
    ontology = get_ontology()
    hits = ontology.infer_from_resume_text(
        "Developed services in Java and C# for three years."
    )
    targets = {h.relation.target for h in hits}
    assert "object-oriented programming" in targets


def test_ontology_maps_non_software_skills():
    ontology = get_ontology()
    hits = ontology.infer_from_resume_text(
        "Prepared Excel reports weekly and handled customer complaints at the front desk."
    )
    targets = {h.relation.target for h in hits}
    assert "data analysis and reporting" in targets
    assert "conflict resolution" in targets


def test_ontology_loads_from_custom_file(tmp_path: Path):
    clear_ontology_cache()
    path = tmp_path / "ont.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "relationships": [
                    {
                        "id": "custom",
                        "source": ["WidgetPro"],
                        "target": "widget mastery",
                        "relation": "tool_to_competency",
                        "confidence": 0.95,
                        "hedged_statement": {"en": "Experience with widget tooling"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ontology = load_ontology(path)
    hits = ontology.infer_from_resume_text("Used WidgetPro daily to process orders.")
    assert len(hits) == 1
    assert hits[0].relation.id == "custom"
    clear_ontology_cache()


def test_dedupe_skills_collapses_case_and_near_duplicates():
    assert dedupe_skills(["Python", "python", "Java", "Python "]) == ["Python", "Java"]


# --------------------------------------------------------------------------- #
# Experience math
# --------------------------------------------------------------------------- #


def test_estimate_years_from_text_deterministic():
    years = estimate_years_from_text("Jan 2020 – Dec 2022 Backend Engineer at Acme")
    assert years is not None
    assert 2.5 <= years <= 3.5


def test_parse_date_range_and_years_between():
    start, end = parse_date_range("March 2019 - Present")
    assert start is not None and end is not None
    span = years_between(start, end)
    assert span is not None and span >= 5


def test_years_from_experience_entries_merges_overlap():
    years = years_from_experience_entries(
        [
            {"dates": "2020-2022"},
            {"dates": "2021-2023"},
        ]
    )
    assert years is not None
    assert 2.5 <= years <= 3.5


def test_claim_years_supported_rejects_inflation():
    assert claim_years_supported(2, resume_years=2.0) is True
    assert claim_years_supported(10, resume_years=2.0) is False
    assert claim_years_supported(3, resume_years=None) is False


def test_extract_years_claims():
    assert 5.0 in extract_years_claims("Over 5 years of experience in sales")


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #


def test_schema_validation_requires_top_level_keys():
    with pytest.raises(SchemaValidationError):
        validate_tailoring_result({"tailored_resume": {}})


def test_schema_rejects_strongly_inferred_without_evidence():
    payload = {
        "tailored_resume": {
            "professional_summary": "x",
            "skills": [],
            "experience": [],
            "projects": [],
            "education": [],
            "certifications": [],
        },
        "matched_requirements": [],
        "missing_requirements": [],
        "inferred_competencies": [
            {
                "statement": "Expert in Kubernetes",
                "supporting_evidence": "",
                "reasoning": "",
                "confidence_score": 0.9,
                "related_requirement": "K8s",
                "inference_category": "Strongly Inferred",
            }
        ],
        "removed_or_deprioritized_content": [],
        "ats_keywords_added": [],
        "change_log": [],
        "validation_warnings": [],
        "original_match_score": 10,
        "tailored_match_score": 10,
    }
    with pytest.raises(SchemaValidationError):
        validate_tailoring_result(payload)


def test_schema_accepts_valid_result():
    payload = {
        "tailored_resume": {
            "professional_summary": "Engineer with Python experience",
            "skills": ["Python"],
            "experience": [],
            "projects": [],
            "education": [],
            "certifications": [],
        },
        "matched_requirements": ["Python"],
        "missing_requirements": [],
        "inferred_competencies": [
            {
                "statement": "Experience with scripting and process automation",
                "supporting_evidence": "Wrote Python scripts",
                "reasoning": "Ontology python-scripting",
                "confidence_score": 0.88,
                "related_requirement": "automation",
                "inference_category": "Strongly Inferred",
            }
        ],
        "removed_or_deprioritized_content": [],
        "ats_keywords_added": ["Python"],
        "change_log": [
            {
                "original_text": "Wrote scripts",
                "new_text": "Wrote Python automation scripts",
                "reason": "JD terminology",
                "supporting_evidence": "Wrote scripts in Python",
                "related_job_requirement": "Python",
                "inference_category": "Explicit",
                "confidence_score": 1.0,
            }
        ],
        "validation_warnings": [],
        "original_match_score": 40,
        "tailored_match_score": 55,
    }
    result = validate_tailoring_result(payload)
    assert result.tailored_match_score == 55
    assert result.inferred_competencies[0].inference_category == "Strongly Inferred"


# --------------------------------------------------------------------------- #
# Claim validator
# --------------------------------------------------------------------------- #


def test_claim_validator_rejects_unsupported_skill_and_bullet():
    resume = TailoredResume(
        professional_summary="Engineer with Java experience at Acme",
        skills=["Java", "SalesforceApexNeverSeen"],
        experience=[
            {
                "company": "Acme",
                "title": "Engineer",
                "dates": "2020-2022",
                "bullets": [
                    "Built REST APIs in Java",
                    "Led a 200-person Salesforce transformation generating $10M",
                ],
            }
        ],
    )
    result = validate_claims(
        original_resume_text=(
            "Acme Engineer 2020-2022. Built REST APIs in Java. Used PostgreSQL."
        ),
        tailored_resume=resume,
    )
    assert "SalesforceApexNeverSeen" in result.rejected_statements
    assert any("Salesforce" in s or "200-person" in s for s in result.rejected_statements)
    assert "Java" in result.cleaned_resume.skills
    assert result.cleaned_resume.skills.count("SalesforceApexNeverSeen") == 0


def test_claim_validator_drops_weakly_inferred_change_log_entries():
    result = validate_claims(
        original_resume_text="Handled invoices and scheduling at RetailCo.",
        tailored_resume=TailoredResume(
            professional_summary="Administrative coordinator",
            skills=["scheduling"],
            experience=[],
        ),
        change_log=[
            {
                "original_text": "",
                "new_text": "Expert enterprise SAP architect",
                "reason": "guess",
                "supporting_evidence": "",
                "related_job_requirement": "SAP",
                "inference_category": "Weakly Inferred",
                "confidence_score": 0.3,
            }
        ],
    )
    assert result.change_log[0].accepted is False
    assert any(w.inference_category == "Weakly Inferred" for w in result.warnings)


def test_statement_supported_by_strongly_inferred():
    from intelligent_tailoring.schemas import InferredCompetency

    ok, reason = statement_supported_by_evidence(
        "Experience applying object-oriented programming principles",
        source_text="Built services in Java.",
        strongly_inferred=[
            InferredCompetency(
                statement="Experience applying object-oriented programming principles",
                supporting_evidence="Built services in Java.",
                reasoning="java-oop",
                confidence_score=0.92,
                related_requirement="OOP",
                ontology_rule_id="java-oop",
            )
        ],
    )
    assert ok is True
    assert "strongly_inferred" in reason


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #


def test_tailoring_cache_hit_and_invalidation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import intelligent_tailoring.cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path / "cache")
    resume = "Resume text v1"
    jd = "JD text v1"
    write_tailoring_cache(
        {"tailored_match_score": 70, "pipeline_version": "intelligent_tailor_v1"},
        resume_text=resume,
        jd_text=jd,
        language="en",
    )
    hit = read_tailoring_cache(resume_text=resume, jd_text=jd, language="en")
    assert hit is not None
    assert hit["from_cache"] is True
    assert hit["tailored_match_score"] == 70

    # Different JD → miss
    assert read_tailoring_cache(resume_text=resume, jd_text="JD text v2", language="en") is None

    invalidate_tailoring_cache(resume_text=resume, jd_text=jd, language="en")
    assert read_tailoring_cache(resume_text=resume, jd_text=jd, language="en") is None


# --------------------------------------------------------------------------- #
# Ranking / evidence / decisions / language
# --------------------------------------------------------------------------- #


def test_rank_requirements_puts_unmet_hard_first():
    requirements = {
        "hard_requirements": ["Python", "Salesforce"],
        "soft_requirements": ["Nice smile"],
    }
    evidence = [
        {
            "requirement": "Python",
            "importance": "hard",
            "candidate_status": "MATCH",
            "inference_category": "Explicit",
        },
        {
            "requirement": "Salesforce",
            "importance": "hard",
            "candidate_status": "MISSING",
            "inference_category": "Unsupported",
        },
        {
            "requirement": "Nice smile",
            "importance": "soft",
            "candidate_status": "MISSING",
            "inference_category": "Unsupported",
        },
    ]
    ranked = rank_requirements(requirements, evidence)
    assert ranked[0]["requirement"] == "Salesforce"


def test_build_evidence_map_classifies_explicit_and_missing():
    ontology = get_ontology()
    evidence = build_evidence_map(
        resume_facts={
            "raw_text": "Built REST APIs in Java. PostgreSQL.",
            "skills": ["Java", "PostgreSQL"],
        },
        requirements={
            "hard_requirements": ["Java", "Salesforce Apex"],
            "soft_requirements": [],
        },
        inferred=[],
        ontology=ontology,
    )
    by_req = {e["requirement"]: e for e in evidence}
    assert by_req["Java"]["inference_category"] == "Explicit"
    assert by_req["Salesforce Apex"]["candidate_status"] == "MISSING"


def test_apply_change_decisions_restores_original_on_reject():
    result = {
        "change_log": [
            {
                "original_text": "Helped customers",
                "new_text": "Delivered white-glove customer success",
                "reason": "JD wording",
                "supporting_evidence": "Helped customers",
                "related_job_requirement": "customer service",
                "inference_category": "Explicit",
                "confidence_score": 1.0,
            }
        ],
        "tailored_resume": {
            "professional_summary": "Coordinator",
            "skills": [],
            "experience": [
                {
                    "company": "Shop",
                    "title": "Clerk",
                    "dates": "2022",
                    "bullets": ["Delivered white-glove customer success"],
                }
            ],
            "projects": [],
            "education": [],
            "certifications": [],
        },
    }
    updated = apply_change_decisions(result, [{"index": 0, "accepted": False}])
    assert updated["change_log"][0]["accepted"] is False
    assert updated["tailored_resume"]["experience"][0]["bullets"] == ["Helped customers"]


def test_detect_language_hebrew_vs_english():
    he = detect_language("שלום אני מחפש משרה בתמיכה טכנית עם ניסיון במכירות ושירות לקוחות")
    en = detect_language("Looking for a technical support role with customer service experience")
    assert he == "he"
    assert en == "en"
