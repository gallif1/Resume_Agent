"""Regression: unsupported_impact must not permanently block export."""

from __future__ import annotations

from intelligent_tailoring.scope_validator import (
    has_unsupported_impact,
    neutralize_unsupported_impact,
    sanitize_resume_unsupported_impact,
)
from tailor_cv_service import (
    assert_safe_to_export,
    repair_report_for_export,
    sanitize_markdown_unsupported_impact,
)


SOURCE = """
Gal Lifshitz
Experience
Directed development of a real-time social activity platform.
Implemented robust activity CRUD services and relational data models.
Conducted weekly tutoring sessions for CS students.
Projects
Developed an Android application to streamline local restaurant ordering.
Used SQLite for data handling and storage.
Skills: FastAPI, PostgreSQL, Android, SQLite, Python
"""


def test_descriptive_bullets_without_impact_verbs_pass():
    assert not has_unsupported_impact(
        "Directed development of a real-time social activity platform.",
        SOURCE,
    )
    assert not has_unsupported_impact(
        "Implemented robust activity CRUD services and relational data models.",
        SOURCE,
    )
    assert not has_unsupported_impact(
        "Conducted weekly tutoring sessions for CS students.",
        SOURCE,
    )
    assert not has_unsupported_impact(
        "Developed an Android application to streamline local restaurant ordering.",
        SOURCE,
    )


def test_novel_ensuring_optimized_flagged_then_neutralized():
    bad = (
        "Optimized data handling and storage using SQLite, ensuring quality "
        "and reliability."
    )
    assert has_unsupported_impact(bad, SOURCE)
    fixed = neutralize_unsupported_impact(bad)
    assert "optimiz" not in fixed.lower()
    assert "ensur" not in fixed.lower()
    assert not has_unsupported_impact(fixed, SOURCE)


def test_source_impact_verb_allowed_without_metric():
    source = "Improved tutoring materials for CS students each week."
    assert not has_unsupported_impact(
        "Improved tutoring materials for CS students each week.",
        source,
    )


def test_invented_metric_still_rejected():
    assert has_unsupported_impact(
        "Improved engagement by 40% using WebSockets",
        "Integrated WebSockets for real-time updates",
    )


def test_sanitize_resume_clears_writer_impact_filler():
    resume = {
        "professional_summary": "Backend developer ensuring reliability.",
        "experience": [
            {
                "company": "Acme",
                "title": "Engineer",
                "bullets": [
                    "Directed development of a real-time social activity platform.",
                    "Optimized activity APIs ensuring performance.",
                ],
            }
        ],
        "projects": [
            {
                "name": "Restaurant App",
                "bullets": [
                    "Developed an Android application to streamline local restaurant ordering.",
                    "Optimized data handling and storage using SQLite, ensuring quality.",
                ],
            }
        ],
        "skills": ["Android", "SQLite"],
    }
    cleaned, changed = sanitize_resume_unsupported_impact(resume, source_text=SOURCE)
    assert changed
    blob = str(cleaned).lower()
    assert "ensuring" not in blob
    assert "optimized" not in blob
    # Factual bullets preserved
    assert "directed development" in blob
    assert "android application" in blob


def test_repair_report_allows_export_for_stale_impact_failures():
    report = {
        "tailored_resume": {
            "professional_summary": "Full stack developer.",
            "experience": [
                {
                    "company": "Acme",
                    "bullets": [
                        "Directed development of a real-time social activity platform, ensuring scalability.",
                    ],
                }
            ],
            "projects": [
                {
                    "name": "App",
                    "bullets": [
                        "Optimized data handling and storage using SQLite, ensuring quality.",
                    ],
                }
            ],
            "skills": ["SQLite"],
        },
        "original_resume_text": SOURCE,
        "quality_gates": {
            "passed": False,
            "failures": [
                "unsupported_impact:experience:Directed development of a real-time social activity platform",
                "unsupported_impact:projects:Optimized data handling and storage using SQLite",
            ],
        },
        "claim_validator_passed": True,
    }
    repaired = repair_report_for_export(report)
    assert repaired["quality_gates"].get("impact_auto_repaired")
    assert not any(
        str(f).startswith("unsupported_impact")
        for f in repaired["quality_gates"]["failures"]
    )
    # Should not raise
    assert_safe_to_export(repaired)


def test_markdown_sanitizer_fixes_bullets():
    md = """## Experience
- Directed development of a real-time social activity platform, ensuring scalability.
- Conducted weekly tutoring sessions for CS students.

## Projects
- Optimized data handling and storage using SQLite, ensuring quality.
"""
    fixed = sanitize_markdown_unsupported_impact(md, source_text=SOURCE)
    assert "ensuring" not in fixed.lower()
    assert "optimized" not in fixed.lower()
    assert "Directed development" in fixed
    assert "Conducted weekly tutoring" in fixed
