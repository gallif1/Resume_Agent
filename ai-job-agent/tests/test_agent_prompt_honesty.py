"""Regressions for honesty-first prompts, identity lock, and cross-entry dedupe."""

from __future__ import annotations

from intelligent_tailoring.interview_philosophy import PIPELINE_PHILOSOPHY
from intelligent_tailoring.jd_contamination import (
    SOURCE_SEPARATION_INSTRUCTION,
    SOURCE_SEPARATION_RULES,
)
from intelligent_tailoring.prompts.human_writer_prompts import HUMAN_RESUME_WRITER_SYSTEM
from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_2_SYSTEM,
    AGENT_3_SYSTEM,
    MERGED_AGENT_2_PROMPT_VERSION,
    MERGED_AGENT_3_PROMPT_VERSION,
    merged_prompt_contains_legacy_rules,
)
from intelligent_tailoring.prompts.stage_prompts import DEEP_TAILOR_REWRITE_SYSTEM
from intelligent_tailoring.services.one_page_compressor import scrub_resume_duplicate_content
from intelligent_tailoring.services.resume_rewriter import (
    _merge_experience_order,
    _merge_project_order,
)
from intelligent_tailoring.structured_validation import _restore_full_source_bullets


def test_merged_prompt_legacy_rules_still_present():
    checks = merged_prompt_contains_legacy_rules()
    assert all(checks.values()), checks
    assert "honesty" in MERGED_AGENT_2_PROMPT_VERSION
    assert "honesty" in MERGED_AGENT_3_PROMPT_VERSION


def test_philosophy_prioritizes_honesty_and_identity():
    text = PIPELINE_PHILOSOPHY.lower()
    assert "honesty before persuasion" in text
    assert "identity lock" in text
    assert "anti-duplication" in text
    assert "never invent" in text
    assert "interview-probability" in text  # still present for legacy checks


def test_deep_tailor_blocks_jd_skill_echo_and_duplication():
    text = DEEP_TAILOR_REWRITE_SYSTEM.lower()
    assert "honesty before persuasion" in text
    assert "identity lock" in text
    assert "academic context lock" in text
    assert "anti-duplication" in text
    assert "years of experience" in text
    assert "tribe" in text and "tel hai" in text
    assert AGENT_2_SYSTEM.count("NEVER invent employers") >= 1


def test_source_separation_forbids_inventing_jd_skills():
    blob = f"{SOURCE_SEPARATION_RULES}\n{SOURCE_SEPARATION_INSTRUCTION}".lower()
    assert "never echo a jd skill" in blob or "never used to invent a\njd skill" in blob or "invent a jd skill" in blob
    assert "academic" in blob
    assert SOURCE_SEPARATION_RULES in AGENT_2_SYSTEM
    assert SOURCE_SEPARATION_RULES in AGENT_3_SYSTEM


def test_human_writer_honesty_and_anti_duplication():
    text = HUMAN_RESUME_WRITER_SYSTEM.lower()
    assert "honesty before persuasion" in text
    assert "facts are immutable" in text
    assert "15-second rule" in text
    assert "anti-duplication" in text
    assert "tel hai" in text
    assert text in AGENT_3_SYSTEM.lower() or HUMAN_RESUME_WRITER_SYSTEM in AGENT_3_SYSTEM


def test_scrub_drops_cross_entry_near_duplicate_bullets():
    shared = (
        "Built a backend monitoring system that continuously checks server "
        "health using multiple protocols"
    )
    resume = {
        "experience": [],
        "projects": [
            {
                "name": "Server Monitor",
                "description": "",
                "bullets": [shared, "Designed database schema for health tracking"],
            },
            {
                "name": "Restaurant App",
                "description": "",
                "bullets": [
                    shared + ".",
                    "Synchronized offline SQLite orders to Firebase",
                ],
            },
        ],
    }
    out = scrub_resume_duplicate_content(resume)
    monitor = out["projects"][0]["bullets"]
    restaurant = out["projects"][1]["bullets"]
    assert any("monitoring" in b.lower() for b in monitor)
    assert not any("monitoring" in b.lower() for b in restaurant)
    assert any("firebase" in b.lower() for b in restaurant)


def test_merge_overwrites_invented_employer_identity():
    rebuilt = {
        "experience": [
            {
                "id": "role_0",
                "source_entry_id": "role_0",
                "title": "Capstone Project Lead",
                "company": "Tribe Platform | Tel Hai University",
                "dates": "2024 – 2025",
                "bullets": ["Designed backend architecture using FastAPI"],
            }
        ],
        "projects": [],
    }
    tailored = {
        "experience": [
            {
                "id": "role_0",
                "source_entry_id": "role_0",
                "title": "Capstone Project Lead",
                "company": "Trike / Yoda Labs",
                "dates": "October 2021 – Present",
                "bullets": ["Designed backend architecture using FastAPI and PostgreSQL"],
            }
        ],
        "projects": [],
    }
    _merge_experience_order(tailored, rebuilt)
    assert len(tailored["experience"]) == 1
    role = tailored["experience"][0]
    assert "Tribe" in str(role.get("company"))
    assert "Trike" not in str(role.get("company"))
    assert "Yoda" not in str(role.get("company"))
    assert "2024" in str(role.get("dates"))


def test_restore_locks_identity_from_source_facts():
    source_facts = {
        "experience_roles": [
            {
                "id": "role_0",
                "source_entry_id": "role_0",
                "title": "Python Programming Tutor",
                "company": "Tel Hai University",
                "dates": "Jul 2022 – Jul 2023",
                "bullets": ["Delivered weekly tutoring sessions for CS students"],
            }
        ],
        "projects": [
            {
                "id": "project_0",
                "source_entry_id": "project_0",
                "name": "Server Monitor System",
                "description": "Backend monitoring system",
                "bullets": ["Developed REST API using FastAPI and PostgreSQL"],
            }
        ],
    }
    resume = {
        "experience": [
            {
                "id": "role_0",
                "source_entry_id": "role_0",
                "title": "Python Programming Tutor",
                "company": "Tel Aviv University",
                "dates": "October 2021 – Present",
                "bullets": ["Delivered weekly tutoring sessions for CS students"],
            }
        ],
        "projects": [
            {
                "id": "project_0",
                "source_entry_id": "project_0",
                "name": "Server Monitor",
                "description": "",
                "bullets": ["Developed REST API using FastAPI and PostgreSQL"],
            }
        ],
    }
    out = _restore_full_source_bullets(resume, source_facts=source_facts)
    assert out["experience"][0]["company"] == "Tel Hai University"
    assert "2022" in str(out["experience"][0]["dates"])
    assert "Server Monitor" in str(out["projects"][0]["name"])
    assert out["projects"][0]["source_entry_id"] == "project_0"
