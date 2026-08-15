"""Regression: Bylith Frontend tailor must not invent Bylith employer or project headings."""

from __future__ import annotations

from intelligent_tailoring.canonical_resume import restore_missing_content_from_source
from intelligent_tailoring.claim_validator import (
    looks_like_bullet_project_name,
    organization_supported,
    project_name_supported,
    validate_claims,
)
from intelligent_tailoring.jd_contamination import scrub_target_employer_claims
from intelligent_tailoring.services.one_page_compressor import scrub_resume_duplicate_content
from tests.test_foundational_identity_hallucination import (
    GAL_TEL_HAI_SOURCE,
    _gal_resume_facts,
)
from tailor_cv_service import render_tailored_cv_markdown
import pdf_generator_service as pdf


def _bylith_screenshot_resume() -> dict:
    """Mirrors the live Bylith Frontend preview failure (employer + project spam)."""
    monitor_bullets = [
        "Developed REST API using FastAPI and PostgreSQL",
        "Implemented background worker performing parallel health checks (HTTP, TCP, SSH)",
        "Used ThreadPoolExecutor for concurrent server monitoring",
        "Designed database schema for server health tracking and request history",
    ]
    return {
        "professional_title": "Frontend Developer",
        "professional_summary": (
            "Proficient full-stack, responsive, mission-focused developer skilled in "
            "crafting user-interfaces with React and Angular."
        ),
        "skills": [
            "Frontend: React, Angular, HTML, CSS",
            "Backend: FastAPI, Node.js",
        ],
        "experience": [
            {
                "title": "Front-end Project Lead",
                "company": "Bylith Platform",
                "dates": "Jan 2019 — Present",
                "bullets": [
                    "Built interactive interfaces with React and Angular.",
                    "Designed backend architecture using FastAPI and PostgreSQL",
                ],
            }
        ],
        "projects": [
            {
                "name": "Server Monitor System",
                "description": "",
                "bullets": list(monitor_bullets),
            },
            {
                "name": "Backend Data Ordering App",
                "description": "",
                "bullets": list(monitor_bullets),
            },
            {
                "name": "Used ThreadPoolExecutor for concurrent server monitoring",
                "description": "",
                "bullets": [
                    "Used ThreadPoolExecutor for concurrent server monitoring",
                ],
            },
            {
                "name": "Used ThreadPoolExecutor for concurrent server monitoring",
                "description": "",
                "bullets": [],
            },
            {
                "name": (
                    "Android application for food ordering including item selection, "
                    "quantities and other requirements (Shop app)"
                ),
                "description": (
                    "Android application for food ordering including item selection, "
                    "quantities and other requirements (Shop app)"
                ),
                "bullets": [
                    "Android application for food ordering including item selection, "
                    "quantities and other requirements (Shop app)",
                    "SQLite and asynchronous orders on Firebase",
                ],
            },
            {
                "name": "SQLite and asynchronous orders on Firebase",
                "description": "",
                "bullets": ["SQLite and asynchronous orders on Firebase"],
            },
        ],
        "education": [
            {
                "institution": "Tel-Aviv University",
                "degree": "B.Sc in Computer Science",
                "dates": "2019 – 2023",
            }
        ],
        "certifications": [],
    }


def test_bullet_like_project_names_are_rejected():
    src = GAL_TEL_HAI_SOURCE
    assert project_name_supported("Server Monitor System", src)
    assert project_name_supported("Restaurant Menu Ordering App", src)
    assert not project_name_supported("Backend Data Ordering App", src)
    assert not project_name_supported(
        "Used ThreadPoolExecutor for concurrent server monitoring", src
    )
    assert looks_like_bullet_project_name(
        "Used ThreadPoolExecutor for concurrent server monitoring"
    )
    assert not organization_supported("Bylith Platform", src)


def test_scrub_target_employer_blanks_bylith():
    resume = _bylith_screenshot_resume()
    scrubbed = scrub_target_employer_claims(
        resume,
        source_text=GAL_TEL_HAI_SOURCE,
        target_company="Bylith",
    )
    assert scrubbed["experience"][0]["company"] == ""


def test_claim_validation_drops_bylith_and_fake_project_headings():
    result = validate_claims(
        original_resume_text=GAL_TEL_HAI_SOURCE,
        tailored_resume=_bylith_screenshot_resume(),
    )
    cleaned = result.cleaned_resume.to_dict()
    companies = [str(e.get("company") or "") for e in cleaned.get("experience") or []]
    assert not any("bylith" in c.lower() for c in companies)
    names = [str(p.get("name") or "") for p in cleaned.get("projects") or []]
    assert not any("threadpool" in n.lower() for n in names)
    assert not any("backend data ordering" in n.lower() for n in names)
    assert not any("sqlite and asynchronous" in n.lower() for n in names)


def test_restore_locks_tel_hai_education_and_identity():
    facts = _gal_resume_facts()
    broken = _bylith_screenshot_resume()
    # After claim validation would blank Bylith; restore from thin/wrong shells.
    broken["experience"] = [
        {
            "title": "Capstone Project Lead – Tribe Platform",
            "company": "Bylith Platform",
            "dates": "Jan 2019 — Present",
            "bullets": ["Designed backend architecture using FastAPI"],
        }
    ]
    broken["projects"] = [
        {
            "name": "Server Monitor System",
            "bullets": ["Developed REST API using FastAPI and PostgreSQL"],
        }
    ]
    restored = restore_missing_content_from_source(broken, resume_facts=facts)
    assert "Tel Hai" in str(restored["experience"][0].get("company") or "")
    assert "Bylith" not in str(restored["experience"][0].get("company") or "")
    assert "2024" in str(restored["experience"][0].get("dates") or "")
    edu = restored.get("education") or []
    assert edu
    assert "Tel Hai" in str(edu[0].get("institution") or "")
    assert "Aviv" not in str(edu[0].get("institution") or "")


def test_scrub_merges_whole_duplicate_project_blocks():
    """Screenshot regression: Server Monitor System emitted twice end-to-end."""
    desc = (
        "Built a backend monitoring system that continuously checks server "
        "health using multiple protocols."
    )
    bullets = [
        "Developed REST API using FastAPI and PostgreSQL",
        "Implemented background worker performing parallel health checks",
        "Used ThreadPoolExecutor for concurrent server monitoring",
    ]
    resume = {
        "experience": [],
        "projects": [
            {"name": "Server Monitor System", "description": desc, "bullets": list(bullets)},
            {"name": "Server Monitor System", "description": desc, "bullets": list(bullets)},
            {
                "name": "Restaurant Menu Ordering App",
                "description": "Android application for food ordering",
                "bullets": ["Built React Native mobile UI"],
            },
            {
                "name": "Restaurant Menu Ordering App",
                "description": "Android application for food ordering",
                "bullets": ["Built React Native mobile UI", "Synced SQLite to Firebase"],
            },
        ],
    }
    out = scrub_resume_duplicate_content(resume)
    names = [str(p.get("name")) for p in out["projects"]]
    assert names.count("Server Monitor System") == 1
    assert names.count("Restaurant Menu Ordering App") == 1
    monitor = next(p for p in out["projects"] if p["name"] == "Server Monitor System")
    assert len(monitor["bullets"]) == 3
    md = render_tailored_cv_markdown(out, name="Gal Lifshitz")
    assert md.count("### Server Monitor System") == 1
    assert md.lower().count("threadpoolexecutor") == 1


def test_scrub_and_render_collapse_bylith_project_spam():
    scrubbed = scrub_resume_duplicate_content(_bylith_screenshot_resume())
    names = [str(p.get("name") or "") for p in scrubbed.get("projects") or []]
    assert not any("threadpool" in n.lower() for n in names if n)
    assert not any(looks_like_bullet_project_name(n) for n in names if n)

    # Cross-entry monitor bullets must not repeat under the fake ordering app.
    all_bullets = [
        str(b)
        for p in scrubbed.get("projects") or []
        for b in (p.get("bullets") or [])
    ]
    monitor = [
        b for b in all_bullets if "threadpool" in b.lower() or "fastapi and postgresql" in b.lower()
    ]
    # At most one copy of each monitor claim after cross-entry scrub.
    assert len([b for b in monitor if "threadpool" in b.lower()]) <= 1

    md = render_tailored_cv_markdown(scrubbed, name="Gal Lifshitz")
    parsed = pdf.parse_resume_markdown(md)
    project_section = next((s for s in parsed.sections if s.kind == "projects"), None)
    assert project_section is not None
    titles = [e.title for e in project_section.entries]
    assert sum(1 for t in titles if "ThreadPool" in (t or "")) == 0
    assert md.lower().count("threadpoolexecutor") <= 1
