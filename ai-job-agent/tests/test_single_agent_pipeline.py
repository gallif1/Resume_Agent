"""Regression tests for the single Resume Generation Agent pipeline."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from intelligent_tailoring.content_deduper import (
    dedupe_resume_content,
    resume_has_duplicate_content,
)
from intelligent_tailoring.education_normalize import (
    education_contains_raw_json,
    normalize_education_list,
)
from intelligent_tailoring.interview_philosophy import (
    TAILOR_STAGES,
    resolve_merged_stage,
)
from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_MERGE_MAP,
    merged_prompt_contains_legacy_rules,
)
from intelligent_tailoring.prompts.resume_generation_agent_prompts import (
    RESUME_GENERATION_AGENT_SYSTEM,
    single_agent_prompt_contains_legacy_rules,
)
from intelligent_tailoring.schemas import PIPELINE_VERSION
from intelligent_tailoring.skill_taxonomy import (
    categorize_skill,
    normalize_skill_lines,
    should_drop_skill_atom,
)
from intelligent_tailoring.stages.deterministic_job_extraction import (
    extract_job_requirements_deterministic,
    run_deterministic_intelligence_bundle,
)
from tailor_cv_service import render_tailored_cv_markdown


class TestSingleAgentArchitecture:
    def test_pipeline_version(self):
        assert PIPELINE_VERSION.startswith("single_agent")

    def test_three_ui_stages(self):
        assert len(TAILOR_STAGES) == 3
        assert TAILOR_STAGES[0]["id"] == "prepare_evidence"
        assert TAILOR_STAGES[1]["id"] == "resume_generation_agent"
        assert TAILOR_STAGES[2]["id"] == "final_hiring_ats_page"

    def test_legacy_ids_map_into_three_stages(self):
        assert resolve_merged_stage("resume_knowledge") == "prepare_evidence"
        assert resolve_merged_stage("candidate_opportunity_intelligence") == (
            "prepare_evidence"
        )
        assert resolve_merged_stage("resume_tailoring") == "resume_generation_agent"
        assert resolve_merged_stage("human_writing_credibility") == (
            "resume_generation_agent"
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

    def test_single_agent_prompt_preserves_legacy_rules(self):
        checks = single_agent_prompt_contains_legacy_rules()
        assert all(checks.values()), checks
        assert "FACTS ARE IMMUTABLE" in RESUME_GENERATION_AGENT_SYSTEM
        assert "NEVER invent employers" in RESUME_GENERATION_AGENT_SYSTEM

    def test_merged_prompt_helper_still_passes(self):
        checks = merged_prompt_contains_legacy_rules()
        assert checks["deep_tailor_in_single"]
        assert checks["human_writer_in_single"]
        assert checks["single_agent_system_present"]


class TestDeterministicJobExtraction:
    def test_extracts_skills_and_responsibilities_without_llm(self):
        job = {
            "title": "Senior Backend Engineer",
            "company": "Acme",
            "description": (
                "Requirements:\n"
                "- 3+ years Python experience\n"
                "- FastAPI and PostgreSQL\n"
                "- Docker and AWS\n"
                "Responsibilities:\n"
                "- Build REST APIs for internal tools\n"
                "- Collaborate with frontend teams using React\n"
                "Preferred:\n"
                "- Kubernetes experience\n"
            )
            * 2,
        }
        reqs = extract_job_requirements_deterministic(job)
        assert reqs.get("sparse") is False
        skills = " ".join(reqs.get("required_skills") or []).lower()
        assert "python" in skills or "fastapi" in skills or "postgresql" in skills
        assert reqs.get("seniority_level") in ("senior", "mid", "lead", "principal")
        assert reqs.get("extraction_method") == "deterministic"

    def test_intelligence_bundle_records_zero_primary_llm_calls(self):
        from intelligent_tailoring.llm_utils import begin_llm_metrics, get_llm_metrics

        begin_llm_metrics()
        out = run_deterministic_intelligence_bundle(
            job={
                "title": "Engineer",
                "company": "Acme",
                "description": "We need Python engineers to build APIs and services. " * 8,
            },
            resume_facts={
                "raw_text": "Python FastAPI developer with PostgreSQL " * 20,
                "skills": ["Python"],
            },
        )
        metrics = get_llm_metrics()
        assert out["primary_llm_calls"] == 0
        assert metrics["primary_llm_calls"] == 0
        assert isinstance(out.get("job_requirements"), dict)


class TestDuplicateAndEducationFixes:
    def test_dedupe_description_and_bullet(self):
        resume = {
            "professional_summary": "Builder of ordering apps. Builder of ordering apps.",
            "skills": ["Frontend: React"],
            "experience": [],
            "projects": [
                {
                    "name": "Restaurant App",
                    "description": "Built an Android ordering application with Firebase.",
                    "bullets": [
                        "Built an Android ordering application with Firebase.",
                        "Built an Android ordering application with Firebase sync.",
                    ],
                }
            ],
            "education": [],
        }
        cleaned = dedupe_resume_content(resume)
        proj = cleaned["projects"][0]
        # Description and near-duplicate bullets collapsed
        texts = [proj.get("description") or ""] + list(proj.get("bullets") or [])
        texts = [t for t in texts if t]
        assert len(texts) == 1
        assert "Builder of ordering apps. Builder of ordering apps." not in (
            cleaned.get("professional_summary") or ""
        )
        assert not resume_has_duplicate_content(cleaned)

    def test_education_aggregator_never_renders_json(self):
        education = {
            "degrees": ["B.Sc. Computer Science"],
            "institutions": ["SCE"],
            "fields_of_study": ["Computer Science"],
        }
        normalized = normalize_education_list(education)
        assert normalized
        assert normalized[0]["institution"] == "SCE"
        assert "B.Sc" in normalized[0]["degree"]
        assert not education_contains_raw_json(normalized)

        md = render_tailored_cv_markdown(
            {
                "professional_summary": "CS graduate building products.",
                "skills": ["Languages: Python"],
                "experience": [
                    {
                        "title": "Tutor",
                        "company": "Private",
                        "dates": "2022-2024",
                        "bullets": ["Tutored algorithms and data structures."],
                    }
                ],
                "projects": [],
                "education": [education],
            },
            name="Gal",
        )
        assert "{" not in md
        assert "degrees" not in md
        assert "SCE" in md or "Computer Science" in md

    def test_empty_projects_section_omitted(self):
        md = render_tailored_cv_markdown(
            {
                "professional_summary": "Engineer.",
                "skills": ["Languages: Python"],
                "experience": [
                    {
                        "title": "Dev",
                        "company": "Co",
                        "dates": "2024",
                        "bullets": ["Built APIs."],
                    }
                ],
                "projects": [{"name": "Empty", "description": "", "bullets": []}],
                "education": [],
            }
        )
        assert "## Projects" not in md


class TestSkillCategories:
    def test_canonical_categories(self):
        assert categorize_skill("Python") == "Languages"
        assert categorize_skill("React") == "Frontend"
        assert categorize_skill("FastAPI") == "Backend"
        assert categorize_skill("PostgreSQL") == "Databases"
        assert categorize_skill("AWS") == "Cloud"
        assert categorize_skill("Docker") == "DevOps"
        assert categorize_skill("pytest") == "Testing"
        assert categorize_skill("Git") == "Version Control"
        assert categorize_skill("Cursor") == "AI"
        assert categorize_skill("Jira") == "Tools"

    def test_no_other_relevant_skills_or_generics(self):
        assert should_drop_skill_atom("api")
        assert should_drop_skill_atom("architecture")
        lines = normalize_skill_lines(
            [
                "Other Relevant Skills: architecture",
                "Other Relevant Skills: api",
                "web",
                "Python",
                "React",
            ]
        )
        joined = "\n".join(lines)
        assert "Other Relevant Skills" not in joined
        assert "architecture" not in joined.lower()
        assert "Languages: Python" in joined
        assert "Frontend: React" in joined


class TestSingleGenerationCallSite:
    def test_generate_resume_records_one_primary_call(self):
        from intelligent_tailoring.llm_utils import begin_llm_metrics, get_llm_metrics
        from intelligent_tailoring.stages.single_resume_generation import (
            generate_resume_single_agent,
        )

        begin_llm_metrics()
        fake = {
            "tailored_resume": {
                "professional_title": "Backend Engineer",
                "professional_summary": (
                    "Computer Science graduate building APIs with FastAPI and PostgreSQL."
                ),
                "skills": ["Languages: Python", "Backend: FastAPI"],
                "experience": [
                    {
                        "company": "SCE",
                        "title": "Capstone Lead",
                        "dates": "2024-2025",
                        "bullets": ["Led a client app integrating REST APIs."],
                    }
                ],
                "projects": [
                    {
                        "name": "API Platform",
                        "description": "Backend services",
                        "bullets": ["Implemented FastAPI services with PostgreSQL."],
                    }
                ],
                "education": [
                    {
                        "institution": "SCE",
                        "degree": "B.Sc. Computer Science",
                        "dates": "2025",
                    }
                ],
                "certifications": [],
            },
            "change_log": [],
            "matched_requirements": ["Python"],
            "missing_requirements": [],
            "removed_or_deprioritized_content": [],
            "ats_keywords_added": [],
        }
        with patch(
            "intelligent_tailoring.stages.single_resume_generation.call_stage_json",
            return_value=fake,
        ):
            out = generate_resume_single_agent(
                resume_facts={
                    "raw_text": "Python FastAPI " * 30,
                    "skills": ["Python"],
                    "experience_roles": fake["tailored_resume"]["experience"],
                    "projects": fake["tailored_resume"]["projects"],
                    "education": fake["tailored_resume"]["education"],
                },
                rebuilt_resume=fake["tailored_resume"],
                strategy={"job_family": "backend", "genuine_gaps": []},
                scores={},
                ranked_requirements=[],
                inferred=[],
                evidence_map=[],
                use_cache=False,
            )
        metrics = get_llm_metrics()
        assert out["primary_llm_calls"] == 1
        assert metrics["primary_llm_calls"] == 1
        assert "resume_generation_agent" in metrics["primary_llm_call_agents"]
        assert out["tailored_resume"]["professional_summary"]
