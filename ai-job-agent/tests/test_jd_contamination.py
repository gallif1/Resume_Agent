"""Job-posting leakage / honest-maximal-tailoring regressions (Addendum #3).

Guarantees:
1. Casual second-person JD slogans never appear in title/summary as candidate facts.
2. Motivational JD voice is rejected by validation and stripped from summaries.
3. Earlier invariants still hold (stable ids, contact links, no raw dicts, no bullet dupes).
"""

from __future__ import annotations

from copy import deepcopy

from intelligent_tailoring.jd_contamination import (
    SOURCE_SEPARATION_INSTRUCTION,
    SOURCE_SEPARATION_RULES,
    extract_skill_highlight_tokens,
    find_jd_contamination,
    looks_like_jd_voice,
    strip_jd_contaminated_sentences,
    summary_describes_candidate_only,
    validate_resume_against_jd,
)
from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_2_SYSTEM,
    AGENT_3_SYSTEM,
    build_agent_2_user_prompt,
    build_agent_3_user_prompt,
)
from intelligent_tailoring.structured_resume import assign_stable_ids, stamp_ids_on_resume
from intelligent_tailoring.structured_validation import (
    repair_structured_resume,
    validate_structured_resume,
)
from intelligent_tailoring.summary_builder import (
    build_professional_summary,
    build_summary_plan,
    summary_passes_checks,
)
from match_tailor_service import build_honest_professional_title


CASUAL_BACKEND_JD = """
Senior Backend Developer

Must-Haves:
- You are the best in your team
- We demand a lot from our engineers
- Strong Python and FastAPI experience
- PostgreSQL, REST APIs, and cloud deployment

NOW is the time to join us!
"""

MOTIVATIONAL_SALES_JD = """
Account Executive

We're looking for someone who thrives under pressure.
You must love closing deals. You will own the full funnel.
We are looking for hunters who crush quota.

Nice to have: Salesforce, HubSpot.
"""


def _base_resume_facts() -> dict:
    return assign_stable_ids(
        {
            "contact": {
                "name": "Gal Lifshitz",
                "email": "gal@example.com",
                "phone": "+972-50-000-0000",
                "linkedin": "https://linkedin.com/in/gallifshitz",
                "github": "https://github.com/gallif",
                "location": "Israel",
            },
            "skills": ["Python", "FastAPI", "PostgreSQL", "React", "AWS"],
            "display_skills": ["Python", "FastAPI", "PostgreSQL", "React", "AWS"],
            "experience_roles": [
                {
                    "id": "role_1",
                    "source_entry_id": "role_1",
                    "title": "Capstone Project Lead",
                    "company": "SCE",
                    "dates": "2024 – 2025",
                    "bullets": [
                        "Implemented automated testing using pytest including "
                        "integration tests and reusable testing utilities.",
                        "Built React views for core user workflows.",
                    ],
                },
                {
                    "id": "role_2",
                    "source_entry_id": "role_2",
                    "title": "Python Programming Tutor",
                    "company": "Tel Hai University",
                    "dates": "2022 – 2023",
                    "bullets": [
                        "Tutored algorithms, data structures, and debugging techniques.",
                        "Helped students implement Python assignments.",
                    ],
                },
            ],
            "projects": [
                {
                    "id": "project_1",
                    "source_entry_id": "project_1",
                    "name": "REST API Development",
                    "description": "Backend services for request tracking.",
                    "bullets": [
                        "Built FastAPI services with SQLAlchemy and PostgreSQL.",
                        "Added pytest coverage for core endpoints.",
                    ],
                },
                {
                    "id": "project_2",
                    "source_entry_id": "project_2",
                    "name": "Server Monitor System",
                    "description": "Concurrent health checks.",
                    "bullets": [
                        "Implemented concurrent health checks with ThreadPoolExecutor.",
                        "Deployed monitoring jobs on AWS.",
                    ],
                },
            ],
            "education": [
                {
                    "id": "edu_1",
                    "degree": "B.Sc. Software Engineering",
                    "school": "SCE",
                }
            ],
            "raw_text": (
                "Gal Lifshitz. Python Programming Tutor at Tel Hai University. "
                "Capstone Project Lead at SCE. Built FastAPI services with "
                "SQLAlchemy and PostgreSQL. React views. AWS monitoring. "
                "pytest. REST API Development. Server Monitor System. "
                "https://github.com/gallif https://linkedin.com/in/gallifshitz"
            ),
        }
    )


def _full_resume(summary: str = "") -> dict:
    facts = _base_resume_facts()
    resume = stamp_ids_on_resume(
        {
            "name": "Gal Lifshitz",
            "contact": deepcopy(facts["contact"]),
            "professional_title": "Software Engineer",
            "professional_summary": summary,
            "summary": summary,
            "skills": ["Languages: Python", "Backend: FastAPI, PostgreSQL", "Cloud: AWS"],
            "experience": deepcopy(facts["experience_roles"]),
            "projects": deepcopy(facts["projects"]),
            "education": deepcopy(facts["education"]),
            "certifications": [],
        },
        source_facts=facts,
    )
    return resume


class TestJdVoiceDetection:
    def test_detects_second_person_and_motivational(self):
        assert looks_like_jd_voice("You are the best in your team")
        assert looks_like_jd_voice("We demand a lot from our engineers")
        assert looks_like_jd_voice("NOW is the time to join us")
        assert not looks_like_jd_voice("Built FastAPI services with PostgreSQL")

    def test_skill_tokens_skip_pronouns(self):
        tokens = extract_skill_highlight_tokens(
            "You are the best in your team with Python and FastAPI",
            max_tokens=5,
        )
        joined = " ".join(tokens).lower()
        assert "you" not in tokens
        assert "are" not in tokens
        assert "best" not in tokens
        assert "python" in joined or "fastapi" in joined


class TestHonestTitleNoJdLeak:
    def test_you_are_the_best_does_not_become_title_crumb(self):
        title = build_honest_professional_title(
            "Senior Backend Developer",
            [
                {
                    "requirement": "You are the best in your team",
                    "candidate_status": "MATCH",
                },
                {
                    "requirement": "Strong Python and FastAPI experience",
                    "candidate_status": "MATCH",
                },
            ],
        )
        low = title.lower()
        assert "you are best" not in low
        assert "you are the best" not in low
        assert "with you" not in low
        # Should prefer real skill tokens when available
        assert "python" in low or "fastapi" in low or title == "Professional"


class TestSummaryPlanRejectsJdVoice:
    def test_strongest_evidence_ignores_jd_slogans(self):
        facts = _base_resume_facts()
        plan = build_summary_plan(
            strategy={
                "strongest_evidence": [
                    "You are the best in your team",
                    "Built FastAPI services with SQLAlchemy and PostgreSQL",
                ],
                "top_interview_reasons": ["We demand a lot from our engineers"],
                "skills_to_emphasize": ["Python", "FastAPI"],
                "honest_title": "Backend Developer",
            },
            resume_facts=facts,
            resume_text=facts["raw_text"],
        )
        evidence = " ".join(str(x) for x in (plan.get("strongest_evidence") or [])).lower()
        assert "you are the best" not in evidence
        assert "we demand" not in evidence


class TestSummaryContamination:
    def test_finds_ngram_overlap_and_second_person(self):
        contaminated = (
            "Professional with You Are Best experience Software Engineer "
            "skilled in Python and FastAPI."
        )
        report = find_jd_contamination(
            contaminated, jd_text=CASUAL_BACKEND_JD, min_ngram=5
        )
        assert report["contaminated"] is True
        assert "second_person_voice" in report["issues"] or report["shared_ngrams"]

    def test_summary_passes_checks_rejects_leak(self):
        bad = (
            "Professional with You Are Best experience Software Engineer "
            "skilled in Python FastAPI and PostgreSQL delivery across projects."
        )
        ok, errors = summary_passes_checks(
            bad,
            resume_text=_base_resume_facts()["raw_text"],
            jd_text=CASUAL_BACKEND_JD,
        )
        assert ok is False
        assert any("jd_contamination" in e or "summary_not_candidate" in e for e in errors)

    def test_strip_removes_contaminated_sentences(self):
        text = (
            "Professional with You Are Best experience. "
            "Built FastAPI services with PostgreSQL for request tracking."
        )
        cleaned = strip_jd_contaminated_sentences(text, jd_text=CASUAL_BACKEND_JD)
        low = cleaned.lower()
        assert "you are best" not in low
        assert "fastapi" in low or cleaned == ""

    def test_build_professional_summary_avoids_jd_leak(self):
        facts = _base_resume_facts()
        result = build_professional_summary(
            strategy={
                "honest_title": "Backend Developer",
                "skills_to_emphasize": ["Python", "FastAPI", "PostgreSQL"],
                "strongest_evidence": ["You are the best in your team"],
                "top_interview_reasons": ["NOW is the time to join us"],
                "jd_text": CASUAL_BACKEND_JD,
            },
            resume_facts=facts,
            resume_text=facts["raw_text"],
            existing_summary=(
                "Professional with You Are Best experience Software Engineer "
                "skilled in Python and cloud delivery across academic projects."
            ),
            jd_text=CASUAL_BACKEND_JD,
        )
        summary = (result.get("summary") or "").lower()
        assert summary
        assert "you are best" not in summary
        assert "you are the best" not in summary
        assert "now is the time" not in summary
        assert "we demand" not in summary

    def test_second_motivational_jd_also_blocked(self):
        facts = _base_resume_facts()
        result = build_professional_summary(
            strategy={
                "honest_title": "Software Engineer",
                "skills_to_emphasize": ["Python", "FastAPI"],
                "strongest_evidence": ["You must love closing deals"],
                "jd_text": MOTIVATIONAL_SALES_JD,
            },
            resume_facts=facts,
            resume_text=facts["raw_text"],
            existing_summary=(
                "Contributor who thrives under pressure. You will own the full "
                "funnel across Python projects and tutoring work."
            ),
            jd_text=MOTIVATIONAL_SALES_JD,
        )
        summary = (result.get("summary") or "").lower()
        assert "you will own" not in summary
        assert "thrives under pressure" not in summary
        ok, _ = summary_describes_candidate_only(result.get("summary") or "")
        assert ok is True


class TestStructuredValidationJdGate:
    def test_validate_rejects_jd_leak_in_summary(self):
        resume = _full_resume(
            "Professional with You Are Best experience Software Engineer "
            "skilled in Python FastAPI and PostgreSQL across completed projects."
        )
        report = validate_structured_resume(
            resume,
            source_facts=_base_resume_facts(),
            enforce_fullness=True,
            require_summary=True,
            jd_text=CASUAL_BACKEND_JD,
        )
        assert report.passed is False
        codes = report.error_codes()
        assert any(
            "jd" in c or "second_person" in c or "summary_not_candidate" in c
            for c in codes
        )

    def test_repair_strips_jd_voice_from_summary(self):
        facts = _base_resume_facts()
        facts["jd_text"] = CASUAL_BACKEND_JD
        resume = _full_resume(
            "Professional with You Are Best experience. "
            "Computer Science contributor with hands-on FastAPI and PostgreSQL "
            "delivery across tutoring and capstone project work."
        )
        repaired = repair_structured_resume(resume, source_facts=facts)
        summary = str(
            repaired.get("professional_summary") or repaired.get("summary") or ""
        ).lower()
        assert "you are best" not in summary
        # Content completeness invariants
        titles = " ".join(
            str(r.get("title") or "") for r in (repaired.get("experience") or [])
        ).lower()
        assert "python" in titles and "tutor" in titles
        project_names = " ".join(
            str(p.get("name") or "") for p in (repaired.get("projects") or [])
        ).lower()
        assert "rest api" in project_names
        assert "server monitor" in project_names
        contact = repaired.get("contact") or {}
        assert "github.com" in str(contact.get("github") or "")
        assert "linkedin.com" in str(contact.get("linkedin") or "")

    def test_validate_resume_against_jd_helper(self):
        resume = _full_resume(
            "You are the best in your team and skilled in Python delivery."
        )
        report = validate_resume_against_jd(resume, jd_text=CASUAL_BACKEND_JD)
        assert report["passed"] is False


class TestSourceSeparationPrompts:
    def test_agent_prompts_include_source_separation(self):
        assert "<candidate_facts>" in SOURCE_SEPARATION_RULES or "candidate_facts" in SOURCE_SEPARATION_RULES.lower() or SOURCE_SEPARATION_INSTRUCTION
        assert "candidate_facts" in SOURCE_SEPARATION_INSTRUCTION
        assert "job_posting" in SOURCE_SEPARATION_INSTRUCTION
        assert SOURCE_SEPARATION_RULES in AGENT_2_SYSTEM
        assert SOURCE_SEPARATION_RULES in AGENT_3_SYSTEM

        a2 = build_agent_2_user_prompt(
            language="en",
            strategy_json="{}",
            rebuilt_resume_json="{}",
            ranked_requirements_json="[]",
            evidence_map_compact="[]",
            resume_facts_compact="{}",
        )
        assert "<candidate_facts>" in a2
        assert "<job_posting>" in a2
        assert SOURCE_SEPARATION_INSTRUCTION in a2

        a3 = build_agent_3_user_prompt(
            language="en",
            validated_resume_json="{}",
            strategy_compact="{}",
            evidence_compact="[]",
            rejected_claims="[]",
        )
        assert "<candidate_facts>" in a3
        assert SOURCE_SEPARATION_INSTRUCTION in a3


class TestPriorInvariantsStillHold:
    def test_clean_resume_still_passes_with_jd_check(self):
        summary = (
            "Software contributor with hands-on FastAPI and PostgreSQL delivery "
            "across tutoring and capstone project work. Built REST services and "
            "monitoring utilities using Python, pytest, and AWS."
        )
        resume = _full_resume(summary)
        report = validate_structured_resume(
            resume,
            source_facts=_base_resume_facts(),
            enforce_fullness=True,
            require_summary=True,
            jd_text=CASUAL_BACKEND_JD,
        )
        assert report.checks.get("no_jd_contamination") is True
        assert report.checks.get("stable_ids") is True
        assert report.checks.get("contact_preserved") is True
        assert report.checks.get("no_raw_data") is True
        assert report.checks.get("no_near_duplicate_bullets") is True
        # Must not fail solely due to JD check on a clean summary
        jd_codes = [
            c
            for c in report.error_codes()
            if "jd" in c or "second_person" in c or "summary_not_candidate" in c
        ]
        assert not jd_codes
