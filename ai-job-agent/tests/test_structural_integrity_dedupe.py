"""Regressions for resume merge duplication / cross-contamination bugs.

Freezes the failure mode where Capstone + Tutor appear twice, Tutor's
title/company/date line bleeds into Capstone bullets, and project bullets
embed literal ``•`` markers (sometimes doubled) after merge+render.
"""

from __future__ import annotations

from intelligent_tailoring.services.resume_rewriter import (
    _merge_experience_order,
    _merge_project_order,
)
from intelligent_tailoring.structural_integrity import (
    looks_like_entry_heading,
    strip_bullet_markers,
    structural_failures,
    validate_and_repair_resume_structure,
)
from tailor_cv_service import render_tailored_cv_markdown


REST_DESC = "Developed REST API using FastAPI and PostgreSQL"
MONITOR_DESC = (
    "Built a backend monitoring system that continuously checks server "
    "health using multiple protocols"
)


def _malformed_bug_resume() -> dict:
    """Fixture matching the reported duplication / cross-contam output."""
    return {
        "professional_summary": "Fullstack developer building reliable backends.",
        "experience": [
            {
                "title": "Capstone Project Lead – Tribe Platform",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": [
                    "Led a multi-page client application integrating REST APIs.",
                    "Python Programming Tutor | Tel Hai University | Jul 2022 – Jul 2023",
                    "Built React and Angular views for core user workflows.",
                ],
            },
            {
                "title": "Capstone Project Lead – Tribe Platform",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": [
                    "Coordinated frontend/backend integration and debugging.",
                    "Designed system architecture for high scalability.",
                ],
            },
            {
                "title": "Python Programming Tutor",
                "company": "Tel Hai University",
                "dates": "Jul 2022 – Jul 2023",
                "bullets": [
                    "Tutored algorithms, data structures, and debugging techniques.",
                ],
            },
            {
                "title": "Python Programming Tutor",
                "company": "Tel Hai University",
                "dates": "Jul 2022 – Jul 2023",
                "bullets": [
                    "Helped students implement Python and JavaScript assignments.",
                ],
            },
        ],
        "projects": [
            {
                "name": "REST API Development",
                "description": REST_DESC,
                "bullets": [
                    f"• {REST_DESC}",
                    f"• • {REST_DESC}",
                    "Added WebSockets for live updates and pytest coverage.",
                ],
            },
            {
                "name": "Server Monitor System",
                "description": MONITOR_DESC,
                "bullets": [
                    "Server Monitor System",
                    MONITOR_DESC,
                    "Designed database schema for server health tracking.",
                ],
            },
        ],
        "skills": ["Backend: FastAPI, PostgreSQL", "Languages: Python"],
    }


def test_strip_bullet_markers_handles_doubled_markers():
    assert strip_bullet_markers("• Developed REST API") == "Developed REST API"
    assert strip_bullet_markers("• • Developed REST API") == "Developed REST API"
    assert strip_bullet_markers("- - Developed REST API") == "Developed REST API"
    # Preserve numeric negatives / percentages
    assert strip_bullet_markers("-5% latency reduction using caching") == (
        "-5% latency reduction using caching"
    )


def test_looks_like_entry_heading_detects_tutor_meta_line():
    assert looks_like_entry_heading(
        "Python Programming Tutor | Tel Hai University | Jul 2022 – Jul 2023",
        known_titles={"Python Programming Tutor", "Capstone Project Lead"},
        known_companies={"Tel Hai University", "SCE"},
    )
    assert not looks_like_entry_heading(
        "Built React and Angular views for core user workflows.",
        known_titles={"Python Programming Tutor"},
        known_companies={"Tel Hai University"},
    )


def test_validate_repairs_reported_duplication_bug():
    broken = _malformed_bug_resume()
    before = structural_failures(broken)
    assert any("duplicate_experience" in f for f in before)
    assert any("misplaced_entry_heading" in f for f in before)

    repaired = validate_and_repair_resume_structure(broken)
    titles = [str(e.get("title") or "") for e in repaired["experience"]]
    assert sum(1 for t in titles if "Capstone" in t) == 1
    assert sum(1 for t in titles if "Tutor" in t) == 1

    capstone = next(e for e in repaired["experience"] if "Capstone" in str(e.get("title")))
    tutor = next(e for e in repaired["experience"] if "Tutor" in str(e.get("title")))
    # Union of unique Capstone bullets (no Tutor meta line)
    assert len(capstone["bullets"]) >= 3
    assert not any("Tel Hai" in b for b in capstone["bullets"])
    assert not any("Python Programming Tutor" in b for b in capstone["bullets"])
    # Tutor bullets consolidated under Tutor once
    assert len(tutor["bullets"]) >= 2

    rest = next(p for p in repaired["projects"] if "REST" in str(p.get("name")))
    monitor = next(p for p in repaired["projects"] if "Monitor" in str(p.get("name")))
    # Description kept OR one bullet — not both near-identical copies
    rest_blob = [rest.get("description") or ""] + list(rest.get("bullets") or [])
    assert sum(1 for line in rest_blob if REST_DESC.lower() in str(line).lower()) == 1
    assert not any(str(b).lstrip().startswith(("•", "-", "*")) for b in rest["bullets"])
    assert "Server Monitor System" not in (monitor.get("bullets") or [])
    assert not any(
        MONITOR_DESC.lower() in str(b).lower() for b in (monitor.get("bullets") or [])
    ) or not (monitor.get("description") or "")

    after = structural_failures(repaired)
    assert not any("duplicate_experience" in f for f in after)
    assert not any("misplaced_entry_heading" in f for f in after)
    assert "embedded_bullet_marker" not in after


def test_merge_experience_does_not_duplicate_on_index_drift():
    """LLM Capstone×2 + rebuilt Capstone/Tutor must not yield 3 Capstones."""
    rebuilt = {
        "experience": [
            {
                "title": "Capstone Project Lead",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": [
                    "Led a multi-page client application integrating REST APIs.",
                    "Built React and Angular views for core user workflows.",
                ],
                "source_entry_id": "role_0",
            },
            {
                "title": "Python Programming Tutor",
                "company": "Tel Hai University",
                "dates": "Jul 2022 – Jul 2023",
                "bullets": [
                    "Tutored algorithms, data structures, and debugging techniques.",
                ],
                "source_entry_id": "role_1",
            },
        ]
    }
    # LLM returned Capstone twice (different bullet sets) then Tutor — order
    # that previously index-zipped Capstone#2 onto Tutor and re-appended Capstone.
    tailored = {
        "experience": [
            {
                "title": "Capstone Project Lead – Tribe Platform",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": [
                    "Led a multi-page client application integrating REST APIs.",
                    "Python Programming Tutor | Tel Hai University | Jul 2022 – Jul 2023",
                ],
            },
            {
                "title": "Capstone Project Lead – Tribe Platform",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": [
                    "Coordinated frontend/backend integration and debugging.",
                ],
            },
            {
                "title": "Python Programming Tutor",
                "company": "Tel Hai University",
                "dates": "Jul 2022 – Jul 2023",
                "bullets": [
                    "Helped students implement Python and JavaScript assignments.",
                ],
            },
        ]
    }
    _merge_experience_order(tailored, rebuilt)
    titles = [str(e.get("title") or "") for e in tailored["experience"]]
    assert sum(1 for t in titles if "Capstone" in t) == 1
    assert sum(1 for t in titles if "Tutor" in t) == 1
    capstone = next(e for e in tailored["experience"] if "Capstone" in str(e.get("title")))
    assert not any("Tel Hai" in b for b in capstone["bullets"])
    # Capstone bullets from both LLM passes + rebuilt are consolidated
    assert len(capstone["bullets"]) >= 2


def test_merge_project_consolidates_duplicates_and_strips_markers():
    rebuilt = {
        "projects": [
            {
                "name": "REST API Development",
                "description": REST_DESC,
                "bullets": [REST_DESC, "Added WebSockets for live updates."],
                "source_entry_id": "project_0",
            },
            {
                "name": "Server Monitor System",
                "description": MONITOR_DESC,
                "bullets": ["Designed database schema for server health tracking."],
                "source_entry_id": "project_1",
            },
        ]
    }
    tailored = {
        "projects": [
            {
                "name": "REST API Development",
                "description": REST_DESC,
                "bullets": [f"• {REST_DESC}", f"• • {REST_DESC}"],
            },
            {
                "name": "REST API Development",
                "description": "",
                "bullets": ["Added WebSockets for live updates and pytest coverage."],
            },
            {
                "name": "Server Monitor System",
                "description": MONITOR_DESC,
                "bullets": ["Server Monitor System", MONITOR_DESC],
            },
        ]
    }
    _merge_project_order(tailored, rebuilt)
    names = [str(p.get("name") or "") for p in tailored["projects"]]
    assert names.count("REST API Development") == 1
    assert names.count("Server Monitor System") == 1
    rest = next(p for p in tailored["projects"] if "REST" in str(p.get("name")))
    assert not any(str(b).lstrip().startswith("•") for b in rest["bullets"])


def test_markdown_render_has_single_clean_entries():
    repaired = validate_and_repair_resume_structure(_malformed_bug_resume())
    md = render_tailored_cv_markdown(repaired, name="Gal Lifshitz")
    assert md.count("### Capstone Project Lead") == 1
    assert md.count("### Python Programming Tutor") == 1
    assert md.count("### REST API Development") == 1
    assert md.count("### Server Monitor System") == 1
    assert "• •" not in md
    assert "- •" not in md
    assert "Tel Hai University | Jul 2022" not in md.split("## Experience")[1].split(
        "### Python Programming Tutor"
    )[0]


def test_content_preservation_merge_still_restores_empty_shells():
    """Prior content-dropping fix must not regress."""
    rebuilt = {
        "experience": [
            {
                "title": "Capstone Project Lead",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": [
                    "Led a multi-page client application integrating REST APIs.",
                    "Built React and Angular views for core user workflows.",
                ],
            }
        ],
        "projects": [
            {
                "name": "Restaurant Menu Ordering App",
                "description": "Built an Android ordering application.",
                "bullets": [
                    "Developed item-selection flows using React Native.",
                    "Synchronized records with Firebase.",
                ],
            }
        ],
    }
    tailored = {
        "experience": [
            {
                "title": "Capstone Project Lead",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": [],
            }
        ],
        "projects": [
            {
                "name": "Restaurant Menu Ordering App",
                "description": "",
                "bullets": [],
            }
        ],
    }
    _merge_experience_order(tailored, rebuilt)
    _merge_project_order(tailored, rebuilt)
    assert len(tailored["experience"][0]["bullets"]) >= 2
    assert tailored["projects"][0]["description"] or tailored["projects"][0]["bullets"]


def test_additional_pairs_dedupe_generalizes():
    """2–3 extra identity shapes: soft title variants + empty company."""
    resume = {
        "experience": [
            {
                "title": "Backend Engineer",
                "company": "Acme Corp",
                "dates": "2021 – 2023",
                "bullets": ["Built payment APIs in Go."],
            },
            {
                "title": "Backend Engineer – Payments",
                "company": "Acme",
                "dates": "2021 – 2023",
                "bullets": ["Reduced checkout latency by 40%."],
            },
            {
                "title": "Research Intern",
                "company": "",
                "dates": "2020",
                "bullets": ["Published NLP benchmarks."],
            },
            {
                "title": "Research Intern",
                "company": "Uni Lab",
                "dates": "2020",
                "bullets": ["Fine-tuned transformer models."],
            },
        ],
        "projects": [
            {
                "name": "Chat Bot",
                "description": "Customer support chatbot.",
                "bullets": ["• Customer support chatbot.", "Integrated Slack."],
            },
            {
                "name": "Chat Bot",
                "description": "",
                "bullets": ["Deployed on AWS Lambda."],
            },
        ],
    }
    repaired = validate_and_repair_resume_structure(resume)
    assert len(repaired["experience"]) == 2
    backend = next(e for e in repaired["experience"] if "Backend" in str(e.get("title")))
    assert len(backend["bullets"]) == 2
    assert len(repaired["projects"]) == 1
    assert not any(str(b).startswith("•") for b in repaired["projects"][0]["bullets"])
