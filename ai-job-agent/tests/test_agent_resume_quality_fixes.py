"""Regressions for agent-generated resume quality bugs.

Covers: duplicate project lines, miscategorized skills (REST/WebSockets under
Database), and junk/placeholder candidate names in the header.
"""

from __future__ import annotations

from intelligent_tailoring.services.one_page_compressor import (
    compress_resume_to_one_page,
    scrub_duplicate_entry_content,
    texts_are_near_duplicates,
)
from intelligent_tailoring.skill_taxonomy import (
    categorize_skill,
    normalize_skill_lines,
)
from parse_cv import sanitize_person_name
import pdf_generator_service as pdf
import tailor_cv_service as tailor


MONITOR_BULLET = (
    "Built a backend monitoring system that continuously checks server "
    "health using multiple protocols"
)
SCHEMA_BULLET = "Designed database schema for server health tracking and request history"


def test_near_duplicate_bullets_are_detected():
    assert texts_are_near_duplicates(MONITOR_BULLET, MONITOR_BULLET + ".")
    assert texts_are_near_duplicates(
        MONITOR_BULLET,
        "Built a backend monitoring system that continuously checks server health",
    )


def test_scrub_drops_description_that_mirrors_bullet():
    entry = scrub_duplicate_entry_content(
        {
            "name": "Server Monitor",
            "description": MONITOR_BULLET + ".",
            "bullets": [MONITOR_BULLET, SCHEMA_BULLET, SCHEMA_BULLET],
        }
    )
    assert entry["description"] == ""
    assert entry["bullets"] == [MONITOR_BULLET, SCHEMA_BULLET]


def test_compress_collapses_project_description_duplicates():
    resume = {
        "professional_summary": "Fullstack developer building reliable backends.",
        "experience": [
            {
                "title": "Capstone Project Lead",
                "company": "Title Platform",
                "bullets": ["Designed system architecture for high scalability."],
            }
        ],
        "projects": [
            {
                "name": "Server Monitor",
                "description": MONITOR_BULLET + ".",
                "bullets": [MONITOR_BULLET, SCHEMA_BULLET, SCHEMA_BULLET],
            }
        ],
        "skills": ["Backend: FastAPI, REST APIs, WebSockets", "Languages: Python"],
    }
    out = compress_resume_to_one_page(
        resume, strategy={"propagate_terms": ["FastAPI", "WebSockets"]}
    )
    project = out["projects"][0]
    assert project["description"] in {"", None}
    assert project["bullets"].count(MONITOR_BULLET) == 1
    assert project["bullets"].count(SCHEMA_BULLET) == 1


def test_markdown_render_skips_duplicate_project_description():
    md = tailor.render_tailored_cv_markdown(
        {
            "projects": [
                {
                    "name": "Server Monitor",
                    "description": MONITOR_BULLET + ".",
                    "bullets": [MONITOR_BULLET, SCHEMA_BULLET],
                }
            ]
        },
        name="Gal Lifshitz",
    )
    assert md.count(MONITOR_BULLET) == 1


def test_markdown_and_pdf_drop_title_echoed_as_description():
    """Screenshot regression: project name must not render twice as a heading."""
    resume = {
        "projects": [
            {
                "name": "Server Monitor System",
                "description": "Server Monitor System",
                "bullets": [MONITOR_BULLET, SCHEMA_BULLET, SCHEMA_BULLET],
            },
            {
                "name": "Restaurant Menu Ordering App",
                "description": "Restaurant Menu Ordering App",
                "bullets": [
                    "Android application for offline menu ordering with Firebase sync.",
                    "Android application for offline menu ordering with Firebase sync.",
                ],
            },
        ]
    }
    md = tailor.render_tailored_cv_markdown(resume, name="Gal Lifshitz")
    assert md.count("### Server Monitor System") == 1
    # Description line that only restates the title must be omitted.
    body_after_heading = md.split("### Server Monitor System", 1)[1]
    assert not body_after_heading.lstrip().startswith("Server Monitor System\n")
    assert md.count(MONITOR_BULLET) == 1
    assert md.count(SCHEMA_BULLET) == 1
    assert md.count("Android application for offline menu ordering") == 1

    parsed = pdf.parse_resume_markdown(md)
    projects = next(s for s in parsed.sections if s.kind == "projects")
    monitor = next(e for e in projects.entries if "Server Monitor" in (e.title or ""))
    assert (monitor.subtitle or "").strip().lower() != "server monitor system"
    assert MONITOR_BULLET in " ".join(monitor.bullets)
    assert " ".join(monitor.bullets).count("Designed database schema") == 1


def test_repair_scrubs_restored_title_and_bullet_dupes():
    from intelligent_tailoring.structured_validation import repair_structured_resume

    broken = {
        "professional_summary": (
            "Full-stack engineer with experience building monitoring systems "
            "and mobile ordering applications."
        ),
        "contact": {"email": "gal@example.com", "github": "https://github.com/g"},
        "experience": [
            {
                "id": "role_0",
                "title": "Capstone Project Lead",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": ["Led a multi-page client application integrating REST APIs."],
            }
        ],
        "projects": [
            {
                "id": "project_0",
                "name": "Server Monitor System",
                "description": "Server Monitor System",
                "bullets": [MONITOR_BULLET, MONITOR_BULLET, SCHEMA_BULLET],
            }
        ],
        "skills": ["Languages: Python"],
        "education": [],
    }
    facts = {
        "contact": broken["contact"],
        "experience_roles": broken["experience"],
        "projects": [
            {
                "id": "project_0",
                "name": "Server Monitor System",
                "description": "Server Monitor System",
                "bullets": [MONITOR_BULLET, SCHEMA_BULLET],
            }
        ],
        "skills": ["Python"],
        "education": [],
    }
    repaired = repair_structured_resume(broken, source_facts=facts)
    project = repaired["projects"][0]
    assert project["description"] in {"", None}
    assert project["bullets"].count(MONITOR_BULLET) == 1
    assert project["bullets"].count(SCHEMA_BULLET) == 1


def test_rest_and_websockets_categorize_as_backend():
    assert categorize_skill("REST APIs") == "Backend"
    assert categorize_skill("WebSockets") == "Backend"
    assert categorize_skill("SQL/Alchemy") == "Backend"
    assert categorize_skill("Lucene") == "Databases"
    assert categorize_skill("Ajax") == "Frontend"

    lines = normalize_skill_lines(
        [
            "Database: Lucene, REST APIs, WebSockets",
            "Backend & Frameworks: Node.js, FastAPI, SQL/Alchemy",
            "Languages: SQL, Python",
        ]
    )
    joined = "\n".join(lines)
    backend = next(line for line in lines if line.startswith("Backend:"))
    databases = next(line for line in lines if line.startswith("Databases:"))
    assert "REST APIs" in backend
    assert "WebSockets" in backend
    assert "SQLAlchemy" in backend
    assert "REST" not in databases
    assert "WebSockets" not in databases
    assert "Lucene" in databases
    assert "Python" in joined


def test_pdf_rebalance_moves_rest_and_websockets_out_of_database():
    rebalanced = pdf._rebalance_skill_lines(
        [
            ("Backend & Frameworks", "Node.js, FastAPI, SQL/Alchemy"),
            ("Database", "Lucene, REST APIs, WebSockets"),
            ("Languages", "SQL, Python"),
        ]
    )
    by_cat = {cat: values for cat, values in rebalanced}
    assert "REST APIs" in by_cat["Backend & Frameworks"]
    assert "WebSockets" in by_cat["Backend & Frameworks"]
    assert "SQLAlchemy" in by_cat["Backend & Frameworks"]
    database_values = by_cat.get("Database", "") + by_cat.get(
        "Databases & Caching", ""
    )
    assert "REST" not in database_values
    assert "WebSockets" not in database_values
    assert "Lucene" in database_values


def test_sanitize_rejects_filename_style_names():
    assert sanitize_person_name("מתן 1 .HRZ") == ""
    assert sanitize_person_name("cv_final.pdf") == ""
    assert sanitize_person_name("Fullstack Developer") == ""
    assert sanitize_person_name("Gal Lifshitz") == "Gal Lifshitz"
    assert sanitize_person_name("מתן כהן") == "מתן כהן"


def test_pdf_parser_ignores_junk_name_and_role_fallback():
    md = """# מתן 1 .HRZ

Israel | 050-0000000 | name@gmail.com

Fullstack Developer

## Skills
Database: Lucene, REST APIs, WebSockets
Backend & Frameworks: Node.js, FastAPI

## Projects
### Server Monitor

Built a backend monitoring system that continuously checks server health using multiple protocols

- Built a backend monitoring system that continuously checks server health using multiple protocols
- Designed database schema for server health tracking and request history
"""
    parsed = pdf.parse_resume_markdown(md)
    assert parsed.name == ""
    skills = next(s for s in parsed.sections if s.kind == "skills")
    joined = " | ".join(f"{c}: {v}" for c, v in skills.skill_lines)
    assert "REST APIs" in joined
    assert "Backend" in joined
    # Description + identical bullet collapse to one line.
    projects = next(s for s in parsed.sections if s.kind == "projects")
    monitor = projects.entries[0]
    assert sum(1 for b in monitor.bullets if "monitoring system" in b.lower()) == 1


def test_build_resume_header_strips_junk_name():
    name, contact, role = tailor.build_resume_header(
        {
            "contact": {
                "name": "מתן 1 .HRZ",
                "email": "name@gmail.com",
                "phone": "050-0000000",
                "location": "Israel",
            }
        },
        {"title": "Fullstack Developer"},
    )
    assert name == ""
    assert "name@gmail.com" in contact
    assert role == "Fullstack Developer"
