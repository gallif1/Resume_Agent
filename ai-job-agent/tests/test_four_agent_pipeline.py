"""Regression + performance tests for the four-agent resume pipeline."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from intelligent_tailoring.gate_severity import (
    classify_quality_gates,
    humanize_gate_failure,
    should_block_download,
)
from intelligent_tailoring.normalization import (
    canonical_skill,
    display_skill,
    project_names_match,
    resolve_original_project,
    stable_source_entry_id,
)
from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_MERGE_MAP,
    merged_prompt_contains_legacy_rules,
)
from intelligent_tailoring.quality_gates import evaluate_quality_gates
from intelligent_tailoring.knowledge_base import build_knowledge_base
from intelligent_tailoring.component_cache import (
    clear_component_caches,
    get_cached_knowledge,
    set_cached_knowledge,
)
from intelligent_tailoring.interview_philosophy import (
    TAILOR_STAGES,
    resolve_merged_stage,
)
from tailor_cv_service import assert_safe_to_export, prepare_for_preview


RESTAURANT_SOURCE = {
    "contact": {"name": "Gal Lifshitz", "email": "gal@example.com"},
    "raw_text": (
        "Projects: Restaurant App: React Native mobile UI with FastAPI backend, "
        "SQLite and Firebase."
    ),
    "skills": {"databases": ["SQLite", "Firebase"]},
    "projects": [
        {
            "name": "Capstone Project",
            "description": "Backend",
            "technologies": ["FastAPI", "PostgreSQL"],
            "bullets": ["Built FastAPI APIs with PostgreSQL"],
        },
        {
            "name": "Restaurant App",
            "description": "Ordering application",
            "technologies": ["React Native", "FastAPI", "SQLite", "Firebase"],
            "bullets": [
                "Built React Native mobile UI",
                "Created FastAPI backend with SQLite and Firebase",
            ],
        },
    ],
}


def _facts_from_profile(profile: dict[str, Any]) -> list[dict[str, Any]]:
    kb = build_knowledge_base(profile, None)
    return [f.to_dict() for f in kb.facts]


class TestCanonicalNormalization:
    def test_skill_aliases_and_case(self):
        assert canonical_skill("Firebase") == "firebase"
        assert canonical_skill("firebase") == "firebase"
        assert canonical_skill("Node.js") == "nodejs"
        assert display_skill("firebase") == "Firebase"

    def test_stable_source_entry_ids(self):
        assert (
            stable_source_entry_id(
                section="projects", name="Restaurant Menu Ordering App"
            )
            == "restaurant_menu_ordering_app"
        )
        assert (
            stable_source_entry_id(
                section="projects",
                name="Restaurant App",
                existing_id="project_1",
            )
            == "project_1"
        )

    def test_project_name_soft_match(self):
        assert project_names_match("Restaurant App", "Restaurant Menu Ordering App")
        assert not project_names_match("Restaurant App", "Capstone Project")


class TestFirebaseFalsePositive:
    def test_firebase_accepted_for_renamed_restaurant_project(self):
        facts = _facts_from_profile(RESTAURANT_SOURCE)
        # Find restaurant project index in source
        restaurant_idx = next(
            i
            for i, p in enumerate(RESTAURANT_SOURCE["projects"])
            if "Restaurant" in p["name"]
        )
        tailored = {
            "professional_summary": "Builder of mobile ordering apps.",
            "skills": ["Databases: SQLite, Firebase"],
            "experience": [],
            "projects": [
                {
                    "name": "Restaurant Menu Ordering App",
                    "description": "Ordering application",
                    "bullets": [
                        "Built React Native UI with Firebase and SQLite",
                    ],
                    # Intentionally omit source_entry_id and reorder above Capstone
                },
                {
                    "name": "Capstone Project",
                    "bullets": ["Built FastAPI APIs with PostgreSQL"],
                },
            ],
        }
        gates = evaluate_quality_gates(
            tailored_resume=tailored,
            original_resume_text=RESTAURANT_SOURCE["raw_text"],
            facts=facts,
            original_projects=RESTAURANT_SOURCE["projects"],
            require_summary=True,
        )
        cross = [
            f for f in gates["failures"] if str(f).startswith("cross_entry_tech:")
        ]
        assert not any("firebase" in f.lower() for f in cross), cross
        # Resolver should map renamed title to original restaurant entry
        idx, orig = resolve_original_project(
            tailored["projects"][0], RESTAURANT_SOURCE["projects"]
        )
        assert idx == restaurant_idx
        assert "Restaurant" in orig.get("name", "")

    def test_technology_cannot_move_across_projects(self):
        facts = _facts_from_profile(RESTAURANT_SOURCE)
        tailored = {
            "professional_summary": "Backend engineer.",
            "skills": ["Backend: FastAPI"],
            "experience": [],
            "projects": [
                {
                    "name": "Capstone Project",
                    "bullets": [
                        # Firebase belongs to Restaurant App, not Capstone
                        "Integrated Firebase into Capstone backend",
                    ],
                }
            ],
        }
        gates = evaluate_quality_gates(
            tailored_resume=tailored,
            original_resume_text=RESTAURANT_SOURCE["raw_text"],
            facts=facts,
            original_projects=RESTAURANT_SOURCE["projects"],
            require_summary=True,
        )
        assert any(
            "cross_entry_tech" in f and "firebase" in f.lower()
            for f in gates["failures"]
        )


class TestGateSeverityAndPreview:
    def test_humanize_cross_entry_message(self):
        msg = humanize_gate_failure(
            "cross_entry_tech:Restaurant Menu Ordering App:firebase"
        )
        assert "Firebase" in msg
        assert "Restaurant Menu Ordering App" in msg
        assert "before downloading" in msg.lower() or "Review" in msg

    def test_preview_allowed_when_warning_or_critical(self):
        gates = classify_quality_gates(
            {
                "passed": False,
                "failures": ["writing_quality:ats:missing_keywords"],
                "warnings": [],
            }
        )
        assert gates["preview_allowed"] is True
        assert gates["download_blocked"] is False

        critical = classify_quality_gates(
            {
                "passed": False,
                "failures": ["cross_entry_tech:X:firebase"],
                "warnings": [],
            }
        )
        assert critical["preview_allowed"] is True
        assert critical["download_blocked"] is True
        assert critical["review_mode"] is True

    def test_preview_does_not_invoke_export(self):
        report = {
            "quality_gates": {
                "passed": False,
                "failures": ["cross_entry_tech:App:firebase"],
            },
            "claim_validator_passed": True,
        }
        preview = prepare_for_preview(report)
        assert preview["preview_allowed"] is True
        assert preview["download_blocked"] is True
        with pytest.raises(Exception):
            assert_safe_to_export(report)


class TestFourAgentArchitecture:
    def test_four_ui_stages(self):
        assert len(TAILOR_STAGES) == 4
        assert TAILOR_STAGES[0]["id"] == "candidate_opportunity_intelligence"
        assert resolve_merged_stage("resume_knowledge") == (
            "candidate_opportunity_intelligence"
        )
        assert resolve_merged_stage("final_polish") == "final_hiring_ats_page"

    def test_merge_map_covers_legacy_agents(self):
        merged = set()
        for members in AGENT_MERGE_MAP.values():
            merged.update(members)
        for required in (
            "resume_knowledge",
            "job_intelligence",
            "company_intelligence",
            "evidence_mapping",
            "resume_strategy",
            "resume_tailoring",
            "claim_validation",
            "human_resume_writer",
            "senior_recruiter_review",
            "hiring_manager_simulation",
        ):
            assert required in merged

    def test_old_prompt_rules_preserved(self):
        checks = merged_prompt_contains_legacy_rules()
        assert all(checks.values()), checks

    def test_knowledge_cache_reuse(self):
        clear_component_caches()
        set_cached_knowledge("abc123", {"resume_facts": {"skills": ["Python"]}, "fact_count": 1})
        hit = get_cached_knowledge("abc123")
        assert hit is not None
        assert hit.get("_cache_hit") is True
        assert hit["fact_count"] == 1
        clear_component_caches()

    def test_stable_entry_id_survives_serialization(self):
        payload = {
            "source_entry_id": stable_source_entry_id(
                section="projects", name="Restaurant App", index=1, existing_id="project_1"
            ),
            "normalized_value": canonical_skill("Firebase"),
        }
        roundtrip = json.loads(json.dumps(payload))
        assert roundtrip["source_entry_id"] == "project_1"
        assert roundtrip["normalized_value"] == "firebase"


class TestProfessionAgnosticFixture:
    def test_non_tech_crm_scope_still_blocks_cross_entry(self):
        profile = {
            "raw_text": "Sales: Used HubSpot for pipeline. Ops: Excel reporting.",
            "skills": {"other": ["HubSpot", "Excel"]},
            "projects": [
                {
                    "name": "CRM Rollout",
                    "technologies": ["HubSpot"],
                    "bullets": ["Configured HubSpot pipeline stages"],
                },
                {
                    "name": "Ops Dashboard",
                    "technologies": ["Excel"],
                    "bullets": ["Built Excel reporting dashboard"],
                },
            ],
        }
        facts = _facts_from_profile(profile)
        tailored = {
            "professional_summary": "Operations analyst.",
            "skills": ["Tools: HubSpot, Excel"],
            "projects": [
                {
                    "name": "Ops Dashboard",
                    "bullets": ["Configured HubSpot inside Ops Dashboard"],
                }
            ],
        }
        gates = evaluate_quality_gates(
            tailored_resume=tailored,
            original_resume_text=profile["raw_text"],
            facts=facts,
            original_projects=profile["projects"],
        )
        assert any("cross_entry_tech" in f for f in gates["failures"])


class TestPrimaryLlmCallBudget:
    def test_intelligence_bundle_records_one_primary_call(self):
        from intelligent_tailoring.llm_utils import begin_llm_metrics, get_llm_metrics
        from intelligent_tailoring.stages.intelligence_bundle import (
            run_intelligence_bundle_llm,
        )

        begin_llm_metrics()
        fake = {
            "job_requirements": {
                "required_skills": ["Python"],
                "preferred_skills": [],
                "responsibilities": ["Build APIs"],
                "tools_technologies": ["Python"],
                "industry_terminology": [],
                "seniority_level": "mid",
                "soft_skills": [],
                "education_certifications": [],
                "ats_keywords": ["Python"],
                "hard_requirements": ["Python"],
                "soft_requirements": [],
                "language": "en",
            },
            "inferred_competencies": [],
        }
        with patch(
            "intelligent_tailoring.stages.intelligence_bundle.call_stage_json",
            return_value=fake,
        ):
            out = run_intelligence_bundle_llm(
                job={
                    "title": "Engineer",
                    "company": "Acme",
                    "description": "We need Python engineers to build APIs and services. " * 5,
                },
                resume_facts={"raw_text": "Python FastAPI developer " * 20, "skills": ["Python"]},
                use_cache=False,
            )
        metrics = get_llm_metrics()
        assert out["primary_llm_calls"] == 1
        assert metrics["primary_llm_calls"] == 1
        assert "candidate_opportunity_intelligence" in metrics["primary_llm_call_agents"]
