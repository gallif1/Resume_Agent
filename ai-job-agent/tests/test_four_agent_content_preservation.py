"""Regression: 4-agent pipeline must not silently empty experience/projects/skills."""

from __future__ import annotations

from intelligent_tailoring.canonical_resume import (
    completeness_failures,
    content_inventory,
    drop_empty_shell_entries,
    estimate_content_density,
    normalize_project_list,
    restore_missing_content_from_source,
)
from intelligent_tailoring.services.resume_rewriter import (
    _merge_experience_order,
    _merge_project_order,
)
from intelligent_tailoring.skill_taxonomy import (
    categorize_skill,
    normalize_skill_lines,
    should_drop_skill_atom,
)
from intelligent_tailoring.stages.resume_extraction import extract_structured_resume
from intelligent_tailoring.summary_builder import (
    _collapse_role_synonyms,
    summary_passes_checks,
)
from tailor_cv_service import render_tailored_cv_markdown


def _screenshot_source_profile() -> dict:
    """Fixture matching the observed empty-shell regression."""
    return {
        "contact": {"name": "Gal Lifshitz"},
        "skills": {
            "languages": ["Python", "JavaScript", "TypeScript", "SQL", "Java", "C++"],
            "frameworks": [
                "React",
                "React Native",
                "Angular",
                "FastAPI",
                "Node.js",
                "Laravel",
            ],
            "databases": ["PostgreSQL", "MongoDB", "SQLite", "Firebase"],
            "cloud": ["AWS", "CI/CD"],
            "other": ["pytest", "WebSockets", "SQLAlchemy", "HTML", "CSS", "Git"],
        },
        "experience": {
            "job_titles": ["Capstone Project Lead", "Computer Science Tutor"],
            "companies": ["SCE", "Private Tutoring"],
        },
        "projects": [
            "Restaurant Menu Ordering App: Built an Android ordering application "
            "with offline storage and Firebase synchronization. "
            "[React Native, SQLite, Firebase]",
            "Backend Services Platform: Developed REST APIs with FastAPI, "
            "SQLAlchemy and PostgreSQL including WebSockets. [FastAPI, PostgreSQL]",
        ],
        "sections": {
            "experience": (
                "Capstone Project Lead @ SCE (2024 – 2025)\n"
                "• Led a multi-page client application integrating REST APIs.\n"
                "• Built React and Angular views for core user workflows.\n"
                "• Coordinated frontend/backend integration and debugging.\n\n"
                "Computer Science Tutor @ Private Tutoring (2022 – 2024)\n"
                "• Tutored algorithms, data structures, and debugging techniques.\n"
                "• Helped students implement Python and JavaScript assignments."
            ),
            "projects": (
                "Restaurant Menu Ordering App: Built an Android ordering application "
                "with offline storage and Firebase synchronization.\n"
                "Backend Services Platform: Developed REST APIs with FastAPI."
            ),
        },
        "master_profile": {
            "work_experience": [
                {
                    "title": "Capstone Project Lead",
                    "company": "SCE",
                    "start_date": "2024",
                    "end_date": "2025",
                    "bullet_points": [
                        "Led a multi-page client application integrating REST APIs.",
                        "Built React and Angular views for core user workflows.",
                        "Coordinated frontend/backend integration and debugging.",
                    ],
                },
                {
                    "title": "Computer Science Tutor",
                    "company": "Private Tutoring",
                    "start_date": "2022",
                    "end_date": "2024",
                    "bullet_points": [
                        "Tutored algorithms, data structures, and debugging techniques.",
                        "Helped students implement Python and JavaScript assignments.",
                    ],
                },
            ],
            "projects": [
                {
                    "name": "Restaurant Menu Ordering App",
                    "description": (
                        "Built an Android ordering application with offline storage "
                        "and Firebase synchronization."
                    ),
                    "bullet_points": [
                        "Developed item-selection and order-entry flows using React Native.",
                        "Stored local order data in SQLite and synchronized records with Firebase.",
                    ],
                    "technologies": ["React Native", "SQLite", "Firebase"],
                },
                {
                    "name": "Backend Services Platform",
                    "description": "REST APIs and realtime services.",
                    "bullet_points": [
                        "Implemented FastAPI services with SQLAlchemy and PostgreSQL.",
                        "Added WebSockets for live updates and pytest coverage.",
                    ],
                    "technologies": ["FastAPI", "SQLAlchemy", "PostgreSQL", "WebSockets", "pytest"],
                },
            ],
            "education": [
                {
                    "degree": "B.Sc. Computer Science",
                    "institution": "SCE",
                    "year": "2025",
                }
            ],
        },
        "education": {"degrees": ["B.Sc. Computer Science"], "institutions": ["SCE"]},
        "raw_text": (
            "Gal Lifshitz. Capstone Project Lead. React Angular FastAPI PostgreSQL "
            "React Native SQLite Firebase Python JavaScript TypeScript AWS CI/CD pytest."
        ),
    }


def test_extract_preserves_master_profile_bullets():
    facts = extract_structured_resume(_screenshot_source_profile())
    inv = content_inventory(
        {
            "experience": facts["experience_roles"],
            "projects": facts["projects"],
            "skills": facts["skills"],
            "education": facts["education"],
        }
    )
    assert inv["experience_entries"] >= 2
    assert inv["experience_bullets"] >= 5
    assert inv["projects"] >= 2
    assert inv["project_bullets"] + inv["projects_with_description"] >= 3
    assert any("Python" in str(s) for s in facts["skills"])
    assert any("React" in str(s) for s in facts["skills"])


def test_normalize_project_string_keeps_description_and_tech():
    projects = normalize_project_list(
        [
            "Restaurant Menu Ordering App: Built an Android ordering application "
            "with offline storage. [React Native, SQLite, Firebase]"
        ]
    )
    assert projects[0]["name"].startswith("Restaurant")
    assert "Android" in projects[0]["description"]
    assert "React Native" in projects[0]["technologies"]


def test_merge_restores_empty_experience_and_project_shells():
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


def test_restore_repairs_screenshot_empty_shell_resume():
    facts = extract_structured_resume(_screenshot_source_profile())
    broken = {
        "professional_title": "Frontend Engineer",
        "professional_summary": "Frontend Engineer Frontend Developer skilled in React.",
        "skills": [
            "Languages: SQL",
            "Backend: React, FastAPI",
            "Other Relevant Skills: architecture",
        ],
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
        "education": [{"degree": "B.Sc. Computer Science", "institution": "SCE"}],
    }
    repaired = restore_missing_content_from_source(broken, resume_facts=facts)
    repaired = drop_empty_shell_entries(repaired)
    inv = content_inventory(repaired)
    assert inv["empty_experience_entries"] == 0
    assert inv["empty_projects"] == 0
    assert inv["experience_bullets"] >= 1
    assert inv["project_bullets"] + inv["projects_with_description"] >= 1

    # Skills taxonomy cleanup
    skills = normalize_skill_lines(
        list(repaired.get("skills") or []) + list(facts.get("skills") or []),
        emphasize=["React", "Angular", "Python"],
    )
    repaired["skills"] = skills
    blob = "\n".join(skills).lower()
    assert "react" in blob
    assert "architecture" not in blob or should_drop_skill_atom("architecture")
    assert categorize_skill("React") == "Frontend"
    assert categorize_skill("FastAPI") == "Backend"

    md = render_tailored_cv_markdown(
        {
            **repaired,
            "professional_summary": _collapse_role_synonyms(
                "Frontend Engineer Frontend Developer skilled in React and Angular."
            ),
        },
        name="Gal Lifshitz",
        target_role="Frontend Engineer",
    )
    assert "Frontend Engineer Frontend Developer" not in md
    assert "Capstone Project Lead" in md
    assert "Led a multi-page" in md or "React" in md
    assert "Restaurant Menu Ordering App" in md
    # Must not render empty project shell without body
    assert "- " in md or "Built an Android" in md


def test_summary_collapses_duplicate_title_phrase():
    cleaned = _collapse_role_synonyms(
        "Frontend Engineer Frontend Developer skilled in React and Angular building apps."
    )
    assert "Frontend Engineer Frontend Developer" not in cleaned
    ok, errors = summary_passes_checks(
        cleaned
        + " Built client workflows with React and Angular across academic projects.",
        resume_text="React Angular Capstone Project Lead Python",
    )
    assert "duplicate_title_phrase" not in errors


def test_completeness_gates_catch_screenshot_failures():
    broken = {
        "professional_summary": "Frontend Engineer Frontend Developer skilled in React.",
        "skills": [
            "Languages: SQL",
            "Backend: React",
            "Other Relevant Skills: architecture",
        ],
        "experience": [
            {"title": "Capstone Project Lead", "company": "SCE", "bullets": []}
        ],
        "projects": [
            {"name": "Restaurant Menu Ordering App", "description": "", "bullets": []}
        ],
    }
    source_inv = {
        "experience_bullets": 5,
        "project_bullets": 4,
        "projects_with_description": 2,
        "skill_atoms": 20,
    }
    fails = completeness_failures(broken, source_inventory=source_inv)
    assert "empty_experience_entries" in fails
    assert "empty_project_entries" in fails
    assert "duplicate_title_phrase_in_summary" in fails
    assert "react_categorized_as_backend" in fails
    assert any(f.startswith("generic_other_skill") for f in fails)


def test_density_flags_underfilled_half_page():
    thin = {
        "professional_summary": "Frontend developer.",
        "skills": ["Languages: SQL"],
        "experience": [{"title": "Capstone", "company": "SCE", "bullets": ["Did work."]}],
        "projects": [{"name": "App", "description": "", "bullets": []}],
    }
    thin = drop_empty_shell_entries(thin)
    density = estimate_content_density(thin)
    assert density["underfilled"] is True


def test_pdf_skill_hints_react_is_frontend():
    from pdf_generator_service import SKILL_CATEGORY_HINTS

    assert SKILL_CATEGORY_HINTS["react"] == "Frontend"
    assert SKILL_CATEGORY_HINTS["react native"] == "Frontend"
    assert SKILL_CATEGORY_HINTS["next.js"] == "Frontend"
