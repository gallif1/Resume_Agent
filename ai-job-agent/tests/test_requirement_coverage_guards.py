"""Requirement-coverage guards — never drop JD-matching bullets / shared tech."""

from __future__ import annotations

from intelligent_tailoring.requirement_coverage import (
    build_coverage_strategy_fields,
    bullet_matches_requirements,
    collect_requirement_phrases,
    contact_preservation_report,
    preserve_contact,
    prioritize_skill_lines,
    requirement_term_set,
    restore_requirement_matched_bullets,
    sanitize_professional_title,
    select_bullets_with_coverage,
    shared_technologies,
    validate_requirement_coverage,
)
from intelligent_tailoring.services.one_page_compressor import compress_resume_to_one_page
from intelligent_tailoring.services.tailoring_strategy_builder import (
    build_tailoring_strategy,
)


# ---------------------------------------------------------------------------
# Pair 1 — original bug: pytest / automated testing bullet dropped
# ---------------------------------------------------------------------------

BACKEND_JD_REQUIREMENTS = {
    "hard_requirements": [
        "Develop automated tests: Create unit and integration tests to ensure code quality.",
        "Build REST APIs with Python",
        "Work with PostgreSQL databases",
    ],
    "required_skills": ["Python", "pytest", "PostgreSQL", "REST APIs"],
    "responsibilities": [
        "Develop automated tests",
        "Create unit and integration tests",
        "Implement backend services",
    ],
    "tools_technologies": ["Python", "pytest", "PostgreSQL", "FastAPI"],
    "seniority_level": "",
}

BACKEND_RESUME_FACTS = {
    "contact": {
        "name": "Alex Candidate",
        "email": "alex@example.com",
        "phone": "+1-555-0100",
        "linkedin": "https://linkedin.com/in/alex",
        "github": "https://github.com/alex",
        "portfolio": "https://alex.dev",
    },
    "professional_title": "Software Engineer",
    "display_skills": [
        "Languages: Python, JavaScript",
        "Backend: FastAPI, Node.js",
        "Databases: PostgreSQL",
        "Testing: pytest",
        "Cloud: AWS",
        "Tools: Git",
    ],
    "skills": [
        "Python",
        "JavaScript",
        "FastAPI",
        "Node.js",
        "PostgreSQL",
        "pytest",
        "AWS",
        "Git",
    ],
    "experience_roles": [
        {
            "title": "Backend Engineer",
            "company": "Acme",
            "dates": "2022 – 2024",
            "bullets": [
                "Built FastAPI services backed by PostgreSQL for order tracking.",
                "Implemented automated testing using pytest, including integration "
                "tests and reusable testing utilities.",
                "Participated in standup meetings and status updates.",
                "Helped with various office tasks and documentation.",
                "Worked on miscellaneous tickets across the backlog.",
            ],
        }
    ],
    "projects": [
        {
            "name": "Order API",
            "description": "Internal ordering platform",
            "bullets": [
                "Designed REST endpoints for catalog and checkout flows.",
                "Added monitoring dashboards for request latency.",
            ],
            "technologies": ["FastAPI", "PostgreSQL"],
        }
    ],
    "raw_text": (
        "Alex Candidate alex@example.com +1-555-0100 "
        "linkedin.com/in/alex github.com/alex alex.dev "
        "Implemented automated testing using pytest integration tests "
        "FastAPI PostgreSQL Python AWS"
    ),
}


# ---------------------------------------------------------------------------
# Pair 2 — nurse / healthcare (non-tech) seniority + shared skills
# ---------------------------------------------------------------------------

NURSE_JD_REQUIREMENTS = {
    "hard_requirements": [
        "Provide patient care and medication administration",
        "Document clinical notes in EHR systems",
        "Collaborate with physicians on care plans",
    ],
    "required_skills": ["patient care", "EHR", "medication administration"],
    "responsibilities": ["Monitor patient vitals", "Administer medications"],
    "tools_technologies": ["EHR", "Epic"],
    "seniority_level": "",
}

NURSE_RESUME_FACTS = {
    "contact": {
        "name": "Sam Nurse",
        "email": "sam@example.com",
        "phone": "555-2222",
        "linkedin": "https://linkedin.com/in/samnurse",
    },
    "professional_title": "Registered Nurse",
    "display_skills": [
        "Clinical: patient care, medication administration, wound care",
        "Systems: EHR, Epic",
        "Other: scheduling",
    ],
    "skills": [
        "patient care",
        "medication administration",
        "wound care",
        "EHR",
        "Epic",
        "scheduling",
    ],
    "experience_roles": [
        {
            "title": "Registered Nurse",
            "company": "City Hospital",
            "dates": "2020 – 2024",
            "bullets": [
                "Provided patient care and administered medications on a 24-bed unit.",
                "Documented clinical notes in Epic EHR each shift.",
                "Coordinated lunch schedules for the nursing station.",
                "Helped with supply closet reorganization.",
            ],
        }
    ],
    "projects": [],
    "raw_text": (
        "Sam Nurse patient care medication administration Epic EHR "
        "Registered Nurse City Hospital"
    ),
}


# ---------------------------------------------------------------------------
# Pair 3 — sales role with explicit seniority in JD
# ---------------------------------------------------------------------------

SALES_JD_REQUIREMENTS = {
    "hard_requirements": [
        "Manage enterprise sales pipeline in Salesforce",
        "Negotiate contracts with procurement teams",
    ],
    "required_skills": ["Salesforce", "negotiation", "pipeline management"],
    "responsibilities": ["Close enterprise deals", "Forecast quarterly revenue"],
    "tools_technologies": ["Salesforce", "HubSpot"],
    "seniority_level": "Senior",
}

SALES_RESUME_FACTS = {
    "contact": {
        "name": "Jordan Sales",
        "email": "jordan@example.com",
        "linkedin": "https://linkedin.com/in/jordan",
        "phone": "555-9999",
    },
    "professional_title": "Account Executive",
    "display_skills": [
        "Sales: Salesforce, HubSpot, negotiation",
        "Other: Excel",
    ],
    "skills": ["Salesforce", "HubSpot", "negotiation", "Excel"],
    "experience_roles": [
        {
            "title": "Account Executive",
            "company": "CloudCo",
            "dates": "2019 – 2024",
            "bullets": [
                "Managed enterprise sales pipeline in Salesforce exceeding quota.",
                "Negotiated contracts with procurement teams for multi-year deals.",
                "Organized team happy hours and offsites.",
                "Updated internal wiki pages occasionally.",
            ],
        }
    ],
    "projects": [],
    "raw_text": "Jordan Sales Salesforce HubSpot negotiation enterprise pipeline",
}


def _fat_backend_resume() -> dict:
    """Resume with many low-value bullets so compression must trim."""
    return {
        "professional_title": "Junior Backend Engineer",
        "professional_summary": (
            "Backend engineer building APIs and automated tests with Python."
        ),
        "contact": dict(BACKEND_RESUME_FACTS["contact"]),
        "experience": [
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "dates": "2022 – 2024",
                "bullets": list(BACKEND_RESUME_FACTS["experience_roles"][0]["bullets"])
                + [
                    "Attended weekly planning sessions.",
                    "Reviewed email threads for the team.",
                    "Updated spreadsheet trackers for chores.",
                ],
            }
        ],
        "projects": [
            {
                "name": "Order API",
                "description": "Internal ordering platform",
                "bullets": [
                    "Designed REST endpoints for catalog and checkout flows.",
                    "Added monitoring dashboards for request latency.",
                    "Wrote meeting notes for kickoff sessions.",
                ],
                "technologies": ["FastAPI", "PostgreSQL", "pytest"],
            },
            {
                "name": "Side Hobby",
                "description": "Personal blog",
                "bullets": ["Wrote blog posts about coffee."],
            },
        ],
        "skills": list(BACKEND_RESUME_FACTS["display_skills"])
        + ["Other: cooking", "Misc: photography"],
    }


def test_pytest_bullet_matches_automated_testing_requirement():
    phrases = collect_requirement_phrases(job_requirements=BACKEND_JD_REQUIREMENTS)
    terms = requirement_term_set(phrases)
    bullet = (
        "Implemented automated testing using pytest, including integration "
        "tests and reusable testing utilities."
    )
    info = bullet_matches_requirements(bullet, terms, phrases=phrases)
    assert info["matches"]
    assert info["direct"] or info["score"] >= 35
    assert any(
        t in info["overlap_terms"]
        for t in ("pytest", "testing", "integration", "automated", "tests")
    )


def test_select_keeps_requirement_match_when_trimming():
    phrases = collect_requirement_phrases(job_requirements=BACKEND_JD_REQUIREMENTS)
    terms = requirement_term_set(phrases)
    bullets = list(BACKEND_RESUME_FACTS["experience_roles"][0]["bullets"])
    kept = select_bullets_with_coverage(
        bullets, limit=2, requirement_terms=terms, phrases=phrases
    )
    blob = " ".join(kept).lower()
    assert "pytest" in blob or "integration" in blob
    assert "standup" not in blob
    assert "miscellaneous" not in blob


def test_compressor_never_drops_pytest_match():
    strategy = build_tailoring_strategy(
        job_analysis={
            "job_family": "backend",
            "industry": "software",
            "primary_role": "Backend Engineer",
            "job_title": "Backend Engineer",
            "seniority": "",
            "requirements": BACKEND_JD_REQUIREMENTS,
            "ats_keywords": ["pytest", "Python", "PostgreSQL"],
        },
        resume_facts=BACKEND_RESUME_FACTS,
        evidence_map=[
            {
                "requirement": "Develop automated tests",
                "candidate_status": "MATCH",
                "importance": "hard",
            },
            {
                "requirement": "Python",
                "candidate_status": "MATCH",
                "importance": "hard",
            },
            {
                "requirement": "PostgreSQL",
                "candidate_status": "MATCH",
                "importance": "hard",
            },
        ],
        ranked_requirements=[
            {"requirement": "Develop automated tests", "rank": 1},
            {"requirement": "Build REST APIs with Python", "rank": 2},
        ],
    )
    assert any("pytest" in b.lower() for b in strategy.get("must_keep_bullets") or [])
    out = compress_resume_to_one_page(_fat_backend_resume(), strategy=strategy)
    blob = " ".join(
        " ".join(e.get("bullets") or []) for e in out.get("experience") or []
    ).lower()
    assert "pytest" in blob or "integration test" in blob
    # Low-relevance filler should be preferred for deletion
    assert "spreadsheet trackers" not in blob


def test_validation_flags_and_restores_dropped_match():
    phrases = collect_requirement_phrases(job_requirements=BACKEND_JD_REQUIREMENTS)
    terms = requirement_term_set(phrases)
    tailored = {
        "experience": [
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "bullets": [
                    "Built FastAPI services backed by PostgreSQL for order tracking.",
                    "Participated in standup meetings and status updates.",
                ],
            }
        ],
        "projects": [],
        "skills": ["Backend: FastAPI, PostgreSQL"],
    }
    report = validate_requirement_coverage(
        source_facts=BACKEND_RESUME_FACTS,
        tailored_resume=tailored,
        requirement_phrases=phrases,
        requirement_terms=terms,
    )
    assert not report["passed"]
    assert report["dropped_count"] >= 1
    restored = restore_requirement_matched_bullets(
        tailored,
        source_facts=BACKEND_RESUME_FACTS,
        requirement_phrases=phrases,
        requirement_terms=terms,
    )
    blob = " ".join(restored["experience"][0]["bullets"]).lower()
    assert "pytest" in blob or "integration" in blob
    assert validate_requirement_coverage(
        source_facts=BACKEND_RESUME_FACTS,
        tailored_resume=restored,
        requirement_phrases=phrases,
        requirement_terms=terms,
    )["passed"]


def test_shared_technologies_and_skill_line_priority():
    phrases = collect_requirement_phrases(job_requirements=BACKEND_JD_REQUIREMENTS)
    terms = requirement_term_set(phrases)
    shared = shared_technologies(
        BACKEND_RESUME_FACTS["display_skills"],
        terms,
        resume_text=BACKEND_RESUME_FACTS["raw_text"],
    )
    shared_low = {s.lower() for s in shared}
    assert "pytest" in shared_low or any("pytest" in s for s in shared_low)
    assert "python" in shared_low or any("python" in s for s in shared_low)

    lines = list(BACKEND_RESUME_FACTS["display_skills"]) + [
        "Other: cooking",
        "Hobbies: photography",
        "Misc: gardening",
    ]
    kept = prioritize_skill_lines(lines, shared_tech=shared, max_lines=4)
    blob = " ".join(kept).lower()
    assert "pytest" in blob
    assert "python" in blob


def test_contact_always_preserved():
    tailored = {"experience": [], "projects": [], "skills": []}
    out = preserve_contact(
        tailored,
        source_contact=BACKEND_RESUME_FACTS["contact"],
        resume_facts=BACKEND_RESUME_FACTS,
    )
    contact = out["contact"]
    assert contact["email"] == "alex@example.com"
    assert contact["github"] == "https://github.com/alex"
    assert contact["linkedin"] == "https://linkedin.com/in/alex"
    assert contact["portfolio"] == "https://alex.dev"
    assert contact["phone"] == "+1-555-0100"
    report = contact_preservation_report(
        source_contact=BACKEND_RESUME_FACTS["contact"],
        tailored=out,
    )
    assert report["passed"]


def test_seniority_neutral_when_jd_silent():
    # Pair 1: JD has no seniority — strip Junior
    title = sanitize_professional_title(
        "Junior Backend Engineer",
        job_title="Backend Engineer",
        job_text="We need a Backend Engineer to build APIs.",
        seniority_level="",
    )
    assert "junior" not in title.lower()
    assert "backend engineer" in title.lower()

    # Pair 2: healthcare JD silent — keep neutral
    title2 = sanitize_professional_title(
        "Junior Registered Nurse",
        job_title="Registered Nurse",
        job_text="Provide patient care and document in EHR.",
        seniority_level="",
    )
    assert "junior" not in title2.lower()

    # Pair 3: JD specifies Senior — keep it
    title3 = sanitize_professional_title(
        "Senior Account Executive",
        job_title="Senior Account Executive",
        job_text="Looking for a Senior Account Executive.",
        seniority_level="Senior",
    )
    assert "senior" in title3.lower()


def test_nurse_pair_keeps_ehr_and_patient_care_bullets():
    strategy = build_tailoring_strategy(
        job_analysis={
            "job_family": "healthcare",
            "industry": "healthcare",
            "primary_role": "Registered Nurse",
            "job_title": "Registered Nurse",
            "seniority": "",
            "requirements": NURSE_JD_REQUIREMENTS,
        },
        resume_facts=NURSE_RESUME_FACTS,
        evidence_map=[
            {
                "requirement": "Provide patient care and medication administration",
                "candidate_status": "MATCH",
                "importance": "hard",
            },
            {
                "requirement": "Document clinical notes in EHR systems",
                "candidate_status": "MATCH",
                "importance": "hard",
            },
        ],
        ranked_requirements=[
            {"requirement": "Provide patient care and medication administration"},
            {"requirement": "Document clinical notes in EHR systems"},
        ],
    )
    must = " ".join(strategy.get("must_keep_bullets") or []).lower()
    assert "patient care" in must or "medication" in must
    assert "ehr" in must or "epic" in must

    resume = {
        "professional_title": "Junior Registered Nurse",
        "professional_summary": "Nurse providing bedside care.",
        "experience": list(NURSE_RESUME_FACTS["experience_roles"]),
        "projects": [],
        "skills": list(NURSE_RESUME_FACTS["display_skills"]),
    }
    out = compress_resume_to_one_page(resume, strategy=strategy)
    blob = " ".join(out["experience"][0]["bullets"]).lower()
    assert "patient care" in blob or "medication" in blob
    assert "ehr" in blob or "epic" in blob
    assert "supply closet" not in blob


def test_sales_pair_keeps_salesforce_and_allows_senior_title():
    strategy = build_tailoring_strategy(
        job_analysis={
            "job_family": "sales",
            "industry": "software",
            "primary_role": "Senior Account Executive",
            "job_title": "Senior Account Executive",
            "seniority": "Senior",
            "requirements": SALES_JD_REQUIREMENTS,
        },
        resume_facts=SALES_RESUME_FACTS,
        evidence_map=[
            {
                "requirement": "Manage enterprise sales pipeline in Salesforce",
                "candidate_status": "MATCH",
                "importance": "hard",
            },
            {
                "requirement": "Negotiate contracts with procurement teams",
                "candidate_status": "MATCH",
                "importance": "hard",
            },
        ],
        ranked_requirements=[
            {"requirement": "Manage enterprise sales pipeline in Salesforce"},
            {"requirement": "Negotiate contracts with procurement teams"},
        ],
    )
    shared = {s.lower() for s in (strategy.get("shared_technologies") or [])}
    assert "salesforce" in shared

    resume = {
        "professional_title": "Senior Account Executive",
        "professional_summary": "Enterprise AE closing cloud deals.",
        "experience": list(SALES_RESUME_FACTS["experience_roles"]),
        "projects": [],
        "skills": list(SALES_RESUME_FACTS["display_skills"]),
    }
    out = compress_resume_to_one_page(resume, strategy=strategy)
    blob = " ".join(out["experience"][0]["bullets"]).lower()
    assert "salesforce" in blob
    assert "negotiat" in blob
    # With a 3-bullet budget, protected matches must occupy the first slots
    top_two = " ".join(out["experience"][0]["bullets"][:2]).lower()
    assert "salesforce" in top_two
    assert "negotiat" in top_two

    title = sanitize_professional_title(
        out.get("professional_title") or "Senior Account Executive",
        job_title="Senior Account Executive",
        job_text="Senior Account Executive role",
        seniority_level="Senior",
    )
    assert "senior" in title.lower()


def test_build_coverage_strategy_fields_promotes_matches():
    base = {
        "skills_to_emphasize": ["Python"],
        "propagate_terms": ["Python"],
        "facts_to_omit": [
            "Implemented automated testing using pytest, including integration "
            "tests and reusable testing utilities."
        ],
        "weaker_evidence_to_reduce": [
            "Implemented automated testing using pytest, including integration "
            "tests and reusable testing utilities."
        ],
        "facts_to_preserve": [],
        "facts_to_expand": [],
        "strongest_evidence": [],
    }
    updated = build_coverage_strategy_fields(
        resume_facts=BACKEND_RESUME_FACTS,
        strategy=base,
        job_requirements=BACKEND_JD_REQUIREMENTS,
    )
    must = " ".join(updated["must_keep_bullets"]).lower()
    assert "pytest" in must
    omit_blob = " ".join(updated.get("facts_to_omit") or []).lower()
    assert "pytest" not in omit_blob
    assert any("pytest" in s.lower() for s in updated.get("shared_technologies") or [])


def test_restore_does_not_leak_tech_across_projects():
    """Server Monitor ThreadPoolExecutor must never land on Capstone."""
    source = {
        "experience_roles": [],
        "projects": [
            {
                "name": "Capstone Project",
                "bullets": [
                    "Designed backend architecture using FastAPI and PostgreSQL",
                    "Added pytest integration testing",
                ],
                "technologies": ["FastAPI", "PostgreSQL", "pytest"],
            },
            {
                "name": "Server Monitor",
                "bullets": [
                    "Implemented FastAPI service with ThreadPoolExecutor "
                    "for concurrent health checks",
                ],
                "technologies": ["FastAPI", "ThreadPoolExecutor", "AWS"],
            },
        ],
        "skills": ["FastAPI", "pytest", "AWS"],
        "raw_text": "Capstone FastAPI pytest Server Monitor ThreadPoolExecutor AWS",
    }
    # Capstone kept; Server Monitor dropped by compression — restore must
    # recreate Server Monitor, not inject its bullet into Capstone.
    tailored = {
        "experience": [],
        "projects": [
            {
                "name": "Capstone Project",
                "bullets": [
                    "Designed backend architecture using FastAPI and PostgreSQL",
                ],
            }
        ],
        "skills": ["Backend: FastAPI"],
    }
    phrases = [
        "Write automated tests with pytest",
        "Build monitoring services with concurrent health checks",
        "AWS deployment",
    ]
    terms = requirement_term_set(phrases)
    restored = restore_requirement_matched_bullets(
        tailored,
        source_facts=source,
        requirement_phrases=phrases,
        requirement_terms=terms,
    )
    capstone = next(
        p
        for p in restored["projects"]
        if "capstone" in str(p.get("name") or "").lower()
    )
    capstone_blob = " ".join(capstone.get("bullets") or []).lower()
    assert "threadpool" not in capstone_blob
    # Protected Capstone testing bullet should still be restorable onto Capstone
    assert "pytest" in capstone_blob or any(
        "pytest" in " ".join(p.get("bullets") or []).lower()
        for p in restored["projects"]
    )


def test_compressor_still_respects_one_page_budget():
    strategy = build_tailoring_strategy(
        job_analysis={
            "job_family": "backend",
            "primary_role": "Backend Engineer",
            "job_title": "Backend Engineer",
            "seniority": "",
            "requirements": BACKEND_JD_REQUIREMENTS,
        },
        resume_facts=BACKEND_RESUME_FACTS,
        evidence_map=[
            {
                "requirement": "Develop automated tests",
                "candidate_status": "MATCH",
                "importance": "hard",
            }
        ],
        ranked_requirements=[{"requirement": "Develop automated tests"}],
    )
    out = compress_resume_to_one_page(_fat_backend_resume(), strategy=strategy)
    total = 0
    for e in out.get("experience") or []:
        total += len(e.get("bullets") or [])
    for p in out.get("projects") or []:
        total += len(p.get("bullets") or [])
    assert total <= 14
    assert len(out.get("experience") or []) <= 3
    assert len(out.get("projects") or []) <= 2
