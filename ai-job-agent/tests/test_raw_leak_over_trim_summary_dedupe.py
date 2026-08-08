"""Addendum #2: raw education leak, over-trimming on weak match, summary/bullet dupes."""

from __future__ import annotations

from intelligent_tailoring.canonical_resume import (
    completeness_failures,
    ensure_minimum_content_from_source,
    find_raw_data_leaks,
    looks_like_raw_data,
    normalize_education_entries,
    restore_missing_content_from_source,
    sanitize_raw_data_fields,
    text_overlap_ratio,
)
from intelligent_tailoring.knowledge_base import (
    build_knowledge_base,
    knowledge_base_to_resume_facts,
)
from intelligent_tailoring.services.one_page_compressor import compress_resume_to_one_page
from intelligent_tailoring.stages.resume_extraction import extract_structured_resume
from intelligent_tailoring.summary_builder import (
    build_professional_summary,
    dedupe_summary_against_bullets,
    summary_passes_checks,
)
from tailor_cv_service import (
    _markdown_has_raw_data,
    render_tailored_cv_markdown,
)


def _weak_match_source_profile() -> dict:
    """Base resume shaped like the reported Gal / Tel Hai candidate."""
    return {
        "contact": {
            "name": "Gal Lifshitz",
            "email": "gal@example.com",
            "linkedin": "https://linkedin.com/in/gallifshitz",
            "github": "https://github.com/gallif",
        },
        "skills": {
            "languages": ["Python", "JavaScript", "SQL", "Go"],
            "frameworks": ["FastAPI", "React", "Node.js"],
            "other": ["pytest", "WebSockets", "AWS", "Git"],
        },
        "experience": {
            "job_titles": ["Capstone Project Lead", "Python Programming Tutor"],
            "companies": ["SCE", "Tel Hai University"],
        },
        "projects": [
            "REST API Development: Built FastAPI services with SQLAlchemy. [FastAPI, pytest]",
            "Server Monitor System: Concurrent health checks with ThreadPoolExecutor. [Python, AWS]",
        ],
        "sections": {
            "experience": (
                "Capstone Project Lead @ SCE (2024 – 2025)\n"
                "• Implemented automated testing using pytest including integration "
                "tests and reusable testing utilities.\n"
                "• Built React and Angular views for core user workflows.\n\n"
                "Python Programming Tutor @ Tel Hai University (2022 – 2023)\n"
                "• Tutored algorithms, data structures, and debugging techniques.\n"
                "• Helped students implement Python assignments."
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
                        "Implemented automated testing using pytest including "
                        "integration tests and reusable testing utilities.",
                        "Built React and Angular views for core user workflows.",
                        "Coordinated frontend/backend integration and debugging.",
                    ],
                },
                {
                    "title": "Python Programming Tutor",
                    "company": "Tel Hai University",
                    "start_date": "2022",
                    "end_date": "2023",
                    "bullet_points": [
                        "Tutored algorithms, data structures, and debugging techniques.",
                        "Helped students implement Python assignments.",
                    ],
                },
            ],
            "projects": [
                {
                    "name": "REST API Development",
                    "description": "Built FastAPI services with SQLAlchemy.",
                    "bullet_points": [
                        "Implemented REST endpoints with FastAPI and SQLAlchemy.",
                        "Added pytest coverage for integration paths.",
                    ],
                    "technologies": ["FastAPI", "SQLAlchemy", "pytest"],
                },
                {
                    "name": "Server Monitor System",
                    "description": "Concurrent health checks with ThreadPoolExecutor.",
                    "bullet_points": [
                        "Built concurrent health checks using ThreadPoolExecutor.",
                        "Deployed monitoring service components on AWS.",
                    ],
                    "technologies": ["Python", "AWS"],
                },
            ],
            "education": [
                {
                    "degree": "B.Sc",
                    "institution": "Tel Hai University",
                    "field": "Computer Science",
                    "year": "2025",
                }
            ],
        },
        # Aggregator shape that previously leaked as str(dict)
        "education": {
            "degrees": ["B.Sc"],
            "institutions": ["Tel Hai University"],
            "fields_of_study": ["Computer Science"],
        },
        "raw_text": (
            "Gal Lifshitz. Capstone Project Lead. Python Programming Tutor. "
            "REST API Development. Server Monitor System. "
            "Python Go WebSockets pytest FastAPI React AWS. "
            "B.Sc Computer Science Tel Hai University. "
            "github.com/gallif linkedin.com/in/gallifshitz"
        ),
    }


def _typescript_nestjs_jd_strategy() -> dict:
    """Weak-match strategy: JD wants 3+ yrs TypeScript/NestJS."""
    return {
        "primary_role": "Backend Engineer",
        "honest_title": "Backend Developer",
        "job_family": "backend",
        "skills_to_emphasize": ["TypeScript", "NestJS", "Node.js"],
        "propagate_terms": ["TypeScript", "NestJS"],
        "must_highlight_in_summary": ["Python", "FastAPI", "pytest"],
        "strongest_evidence": [
            "Implemented automated testing using pytest including integration "
            "tests and reusable testing utilities"
        ],
        "shared_technologies": ["Node.js"],
        "must_keep_skills": ["Node.js"],
        "requirement_phrases": [
            "3+ years of experience",
            "TypeScript",
            "NestJS",
            "Node.js",
        ],
        "facts_to_omit": [
            "Python Programming Tutor",
            "Server Monitor System",
            "Go",
            "WebSockets",
        ],
        "weaker_evidence_to_reduce": [
            "Python Programming Tutor",
            "Server Monitor System",
            "REST API Development",
        ],
    }


# --------------------------------------------------------------------------- #
# Bug 1 — raw structured education data
# --------------------------------------------------------------------------- #


def test_normalize_education_from_aggregator_dict():
    edu = normalize_education_entries(
        {
            "degrees": ["B.Sc"],
            "institutions": ["Tel Hai University"],
            "fields_of_study": ["Computer Science"],
        }
    )
    assert len(edu) == 1
    assert "B.Sc" in edu[0]["degree"]
    assert "Computer Science" in edu[0]["degree"]
    assert edu[0]["institution"] == "Tel Hai University"
    assert not looks_like_raw_data(edu[0]["degree"])


def test_normalize_recovers_stringified_education_dict():
    leaked = str(
        {
            "degrees": ["B.Sc"],
            "institutions": ["Tel Hai University"],
            "fields_of_study": ["Computer Science"],
        }
    )
    assert looks_like_raw_data(leaked)
    edu = normalize_education_entries(
        [{"degree": leaked, "institution": "", "field": "", "dates": ""}]
    )
    assert edu
    assert "Tel Hai" in edu[0]["institution"] or "Tel Hai" in edu[0]["degree"]
    assert not any(looks_like_raw_data(str(v)) for v in edu[0].values())


def test_extract_structured_resume_normalizes_aggregator_education():
    facts = extract_structured_resume(_weak_match_source_profile())
    edu = facts["education"]
    assert edu
    blob = " ".join(str(v) for e in edu for v in e.values())
    assert "{" not in blob
    assert "degrees" not in blob
    assert "Tel Hai" in blob
    assert "B.Sc" in blob or "Computer Science" in blob


def test_knowledge_base_never_stringifies_education_dict():
    profile = {
        "contact": {"name": "Gal"},
        "education": {
            "degrees": ["B.Sc"],
            "institutions": ["Tel Hai University"],
            "fields_of_study": ["Computer Science"],
        },
        "raw_text": "Gal B.Sc Computer Science Tel Hai University",
        # No master_profile — the leak path when aggregator is the only source
    }
    kb = build_knowledge_base(profile)
    facts = knowledge_base_to_resume_facts(kb)
    for entry in facts.get("education") or []:
        assert not looks_like_raw_data(str(entry.get("degree") or ""))
        assert not looks_like_raw_data(str(entry.get("institution") or ""))
    md = render_tailored_cv_markdown(
        {"education": facts.get("education") or []},
        name="Gal",
    )
    assert "{'degrees'" not in md
    assert "fieldsofstudy" not in md.lower()
    assert "fields_of_study" not in md
    assert "Tel Hai" in md or "B.Sc" in md


def test_render_rejects_raw_education_dict():
    leaked = {
        "education": [
            {
                "degree": str(
                    {
                        "degrees": ["B.Sc"],
                        "institutions": ["Tel Hai University"],
                        "fieldsofstudy": ["Computer Science"],
                    }
                ),
                "institution": "",
            }
        ]
    }
    sanitized = sanitize_raw_data_fields(leaked)
    assert not find_raw_data_leaks(sanitized)
    md = render_tailored_cv_markdown(sanitized, name="Gal")
    assert not _markdown_has_raw_data(md)
    assert "{'degrees'" not in md
    assert "Tel Hai" in md or "B.Sc" in md or "## Education" not in md


def test_completeness_flags_raw_data_leak():
    broken = {
        "professional_summary": "Backend developer with Python experience across projects.",
        "experience": [
            {"title": "Capstone", "company": "SCE", "bullets": ["Built APIs."]}
        ],
        "projects": [],
        "skills": ["Python"],
        "education": [
            {
                "degree": "{'degrees': ['B.Sc'], 'institutions': ['Tel Hai University']}",
                "institution": "",
            }
        ],
    }
    fails = completeness_failures(broken)
    assert any(f.startswith("raw_data_leak:") for f in fails)


# --------------------------------------------------------------------------- #
# Bug 2 — over-trimming on weak match
# --------------------------------------------------------------------------- #


def test_compressor_keeps_all_experience_and_projects_on_weak_match():
    facts = extract_structured_resume(_weak_match_source_profile())
    resume = {
        "professional_summary": "Backend developer with Python and FastAPI experience.",
        "experience": list(facts["experience_roles"]),
        "projects": list(facts["projects"]),
        "skills": list(facts["skills"]),
        "education": list(facts["education"]),
    }
    strategy = _typescript_nestjs_jd_strategy()
    compressed = compress_resume_to_one_page(resume, strategy=strategy, aggressive=True)
    titles = {
        str(e.get("title") or "").lower()
        for e in compressed.get("experience") or []
        if isinstance(e, dict)
    }
    names = {
        str(p.get("name") or "").lower()
        for p in compressed.get("projects") or []
        if isinstance(p, dict)
    }
    assert any("tutor" in t for t in titles), titles
    assert any("capstone" in t for t in titles), titles
    assert any("rest api" in n for n in names), names
    assert any("server monitor" in n for n in names), names


def test_ensure_minimum_content_restores_dropped_entries():
    facts = extract_structured_resume(_weak_match_source_profile())
    # Simulate aggressive Agent-2 / compressor drop
    thin = {
        "professional_summary": "Backend developer focused on Node.js.",
        "experience": [
            {
                "title": "Capstone Project Lead",
                "company": "SCE",
                "dates": "2024 – 2025",
                "bullets": [
                    "Implemented automated testing using pytest including "
                    "integration tests and reusable testing utilities."
                ],
            }
        ],
        "projects": [
            {
                "name": "REST API Development",
                "description": "Built FastAPI services.",
                "bullets": ["Implemented REST endpoints with FastAPI."],
            }
        ],
        "skills": ["Languages: TypeScript", "Backend: NestJS, Node.js"],
        "education": [
            {
                "degree": str(
                    {
                        "degrees": ["B.Sc"],
                        "institutions": ["Tel Hai University"],
                        "fields_of_study": ["Computer Science"],
                    }
                )
            }
        ],
        "contact": {},
    }
    repaired = ensure_minimum_content_from_source(thin, resume_facts=facts)
    titles = [str(e.get("title") or "") for e in repaired["experience"]]
    names = [str(p.get("name") or "") for p in repaired["projects"]]
    assert any("Tutor" in t for t in titles)
    assert any("Server Monitor" in n for n in names)
    skill_blob = " ".join(repaired["skills"]).lower()
    assert "go" in skill_blob or "websockets" in skill_blob or "python" in skill_blob
    assert not find_raw_data_leaks(repaired)
    edu_blob = " ".join(
        str(v) for e in repaired.get("education") or [] for v in e.values()
    )
    assert "{" not in edu_blob
    assert "Tel Hai" in edu_blob


def test_restore_keeps_all_source_roles_not_just_top_three():
    facts = extract_structured_resume(_weak_match_source_profile())
    # Empty tailored — restore must bring back BOTH roles and BOTH projects
    empty = {
        "experience": [],
        "projects": [],
        "skills": [],
        "education": facts["education"],
    }
    restored = restore_missing_content_from_source(empty, resume_facts=facts)
    assert len(restored["experience"]) >= 2
    assert len(restored["projects"]) >= 2


def test_completeness_flags_missing_source_entries():
    facts = extract_structured_resume(_weak_match_source_profile())
    thin = {
        "professional_summary": (
            "Backend developer with hands-on experience in Python and FastAPI "
            "across completed academic projects and tutoring roles."
        ),
        "experience": [
            {
                "title": "Capstone Project Lead",
                "company": "SCE",
                "bullets": ["Built React views."],
            }
        ],
        "projects": [
            {
                "name": "REST API Development",
                "bullets": ["Implemented REST endpoints."],
            }
        ],
        "skills": ["Python", "FastAPI"],
        "education": facts["education"],
    }
    fails = completeness_failures(thin, resume_facts=facts)
    assert any(f.startswith("missing_source_experience:") for f in fails)
    assert any(f.startswith("missing_source_project:") for f in fails)


def test_weak_match_render_keeps_links_and_full_background():
    facts = extract_structured_resume(_weak_match_source_profile())
    strategy = _typescript_nestjs_jd_strategy()
    resume = {
        "professional_summary": (
            "Backend developer with hands-on experience in Python and FastAPI."
        ),
        "experience": list(facts["experience_roles"]),
        "projects": list(facts["projects"]),
        "skills": list(facts.get("display_skills") or facts["skills"]),
        "education": list(facts["education"]),
        "contact": dict(facts.get("contact") or {}),
    }
    compressed = compress_resume_to_one_page(resume, strategy=strategy, aggressive=True)
    repaired = ensure_minimum_content_from_source(compressed, resume_facts=facts)
    from intelligent_tailoring.requirement_coverage import preserve_contact

    repaired = preserve_contact(
        repaired,
        source_contact=facts.get("contact"),
        resume_facts=facts,
    )
    md = render_tailored_cv_markdown(
        repaired,
        name="Gal Lifshitz",
        contact_line=(
            f"{repaired['contact'].get('email')} | "
            f"{repaired['contact'].get('linkedin')} | "
            f"{repaired['contact'].get('github')}"
        ),
        target_role="Backend Engineer",
    )
    assert "Python Programming Tutor" in md
    assert "REST API Development" in md
    assert "Server Monitor" in md
    assert "github.com" in md.lower() or "github" in md.lower()
    assert "linkedin.com" in md.lower() or "linkedin" in md.lower()
    assert not _markdown_has_raw_data(md)
    skill_section = md.split("## Skills")[1].split("##")[0].lower() if "## Skills" in md else ""
    assert "go" in skill_section or "websockets" in skill_section or "python" in skill_section


# --------------------------------------------------------------------------- #
# Bug 3 — summary duplicates experience bullet
# --------------------------------------------------------------------------- #


def test_summary_does_not_copy_experience_bullet_verbatim():
    bullet = (
        "Implemented automated testing using pytest including integration "
        "tests and reusable testing utilities"
    )
    facts = extract_structured_resume(_weak_match_source_profile())
    strategy = _typescript_nestjs_jd_strategy()
    result = build_professional_summary(
        strategy=strategy,
        resume_facts=facts,
        resume_text=facts["raw_text"],
        output_language="en",
        existing_summary="",
        tailored_resume={
            "experience": facts["experience_roles"],
            "projects": facts["projects"],
        },
    )
    summary = result["summary"]
    assert bullet.lower() not in summary.lower()
    ok, errors = summary_passes_checks(
        summary,
        resume_text=facts["raw_text"],
        bullet_texts=[bullet],
    )
    assert "summary_duplicates_bullet" not in errors
    assert text_overlap_ratio(summary, bullet) < 0.80 or ok


def test_dedupe_summary_against_bullets_rewrites_overlap():
    bullet = (
        "Implemented automated testing using pytest including integration "
        "tests and reusable testing utilities."
    )
    summary = (
        "Backend developer with Python experience. "
        "Implemented automated testing using pytest including integration "
        "tests and reusable testing utilities. "
        "Comfortable working across FastAPI when the work requires it."
    )
    cleaned = dedupe_summary_against_bullets(summary, [bullet])
    assert bullet.rstrip(".").lower() not in cleaned.lower()
    assert "pytest" in cleaned.lower() or "testing" in cleaned.lower() or "Backend" in cleaned
    ok, errors = summary_passes_checks(
        cleaned,
        resume_text="Python pytest FastAPI Backend developer Capstone",
        bullet_texts=[bullet],
    )
    assert "summary_duplicates_bullet" not in errors


def test_completeness_flags_summary_bullet_duplicate():
    bullet = (
        "Implemented automated testing using pytest including integration "
        "tests and reusable testing utilities."
    )
    resume = {
        "professional_summary": (
            "Backend developer with Python. " + bullet
        ),
        "experience": [
            {
                "title": "Capstone Project Lead",
                "company": "SCE",
                "bullets": [bullet],
            }
        ],
        "projects": [],
        "skills": ["Python"],
    }
    fails = completeness_failures(resume)
    assert "summary_duplicates_bullet" in fails
