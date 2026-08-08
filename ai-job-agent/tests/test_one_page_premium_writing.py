"""Tests for one-page premium writing upgrades (no new agents)."""

from __future__ import annotations

from intelligent_tailoring.quality_gates import evaluate_quality_gates
from intelligent_tailoring.services.one_page_compressor import (
    compress_resume_to_one_page,
    compress_until_likely_fit,
    estimate_page_pressure,
)
from intelligent_tailoring.services.page_count import (
    allow_multi_page_requested,
    assert_one_page,
    estimate_pages_from_resume,
)
from intelligent_tailoring.services.tech_weaver import (
    upgrade_stub_bullet,
    weave_resume_technologies,
    weave_technologies_into_bullet,
)
from intelligent_tailoring.summary_builder import build_summary_plan
from intelligent_tailoring.writing.resume_quality_score import evaluate_resume_quality


def _fat_resume() -> dict:
    return {
        "professional_summary": (
            "Backend engineer with extensive experience building distributed systems "
            "and APIs across many industries including finance retail healthcare and "
            "logistics while collaborating with product design and operations teams "
            "to deliver reliable software that scales under high traffic conditions "
            "and meets strict compliance requirements for enterprise customers."
        ),
        "experience": [
            {
                "company": f"Company {i}",
                "title": "Engineer",
                "bullets": [
                    f"Built service layer number {j} for domain workflows and reporting."
                    for j in range(5)
                ],
                "technologies": ["Python", "PostgreSQL", "FastAPI"],
            }
            for i in range(5)
        ],
        "projects": [
            {
                "name": f"Project {i}",
                "description": "Large platform initiative with many moving parts.",
                "bullets": [
                    "Created database schema",
                    "Implemented request validation endpoints",
                    "Built REST APIs for client integrations",
                    "Added monitoring and logging",
                ],
                "technologies": ["PostgreSQL", "FastAPI", "Docker"],
            }
            for i in range(3)
        ],
        "skills": [
            "Backend: FastAPI, Django",
            "Languages: Python, SQL",
            "Databases: PostgreSQL, Redis",
            "Cloud: AWS, Docker",
            "Frontend: React",
            "Tools: Git, pytest",
            "Other: Linux",
        ],
        "education": [{"school": "Uni", "degree": "BSc"}, {"school": "Uni2", "degree": "MSc"}],
    }


def test_compress_resume_fits_one_page_pressure():
    fat = _fat_resume()
    assert not estimate_page_pressure(fat)["likely_fits_one_page"]
    compressed = compress_until_likely_fit(
        fat,
        strategy={"propagate_terms": ["FastAPI", "PostgreSQL", "Docker"]},
    )
    est = estimate_page_pressure(compressed)
    assert est["likely_fits_one_page"]
    # Minimum-content guarantee: every source role/project is kept; fit is
    # achieved by shortening bullets on lower-ranked entries, not deletion.
    assert est["experience_count"] == 5
    assert est["project_count"] == 3
    assert est["bullet_count"] <= 14
    assert len(compressed["professional_summary"].split()) <= 58
    # Lower-ranked roles should be de-emphasized (≤1 bullet)
    role_bullet_counts = [
        len([b for b in (e.get("bullets") or []) if str(b).strip()])
        for e in compressed["experience"]
    ]
    assert min(role_bullet_counts) >= 1
    assert role_bullet_counts[-1] <= 1


def test_compress_prefers_relevant_bullets():
    resume = {
        "professional_summary": "Backend engineer building APIs with Python.",
        "experience": [
            {
                "company": "Acme",
                "title": "Engineer",
                "bullets": [
                    "Worked on various tickets.",
                    "Designed FastAPI services backed by PostgreSQL for order tracking.",
                    "Helped with meetings.",
                    "Implemented Docker-based local development workflows.",
                ],
            }
        ],
        "projects": [],
        "skills": ["Backend: FastAPI"],
    }
    out = compress_resume_to_one_page(
        resume,
        strategy={"propagate_terms": ["FastAPI", "PostgreSQL", "Docker"]},
    )
    bullets = " ".join(out["experience"][0]["bullets"]).lower()
    assert "fastapi" in bullets
    assert "worked on various" not in bullets or "postgresql" in bullets


def test_tech_weaver_upgrades_stub_and_weaves():
    upgraded = upgrade_stub_bullet(
        "Created database schema",
        ["PostgreSQL", "FastAPI"],
    )
    assert "postgresql" in upgraded.lower()
    assert "created database schema" not in upgraded.lower()

    woven = weave_technologies_into_bullet(
        "Designed backend services exposing REST APIs.",
        ["FastAPI", "SQLAlchemy", "PostgreSQL"],
    )
    low = woven.lower()
    assert "using" in low
    assert "fastapi" in low


def test_weave_resume_uses_sibling_bullet_tech():
    resume = {
        "experience": [
            {
                "company": "Acme",
                "title": "Backend Engineer",
                "bullets": [
                    "Built REST APIs in FastAPI for production traffic.",
                    "Designed backend services for request tracking.",
                ],
            }
        ],
        "projects": [
            {
                "name": "Order Service",
                "technologies": ["PostgreSQL"],
                "bullets": ["Created database schema"],
            }
        ],
        "skills": [],
        "professional_summary": "Backend engineer.",
    }
    out = weave_resume_technologies(resume)
    proj_bullet = out["projects"][0]["bullets"][0].lower()
    assert "postgresql" in proj_bullet
    exp_blob = " ".join(out["experience"][0]["bullets"]).lower()
    assert "fastapi" in exp_blob


def test_page_count_helpers():
    fat = _fat_resume()
    ok, reason = assert_one_page(resume=fat, allow_multi_page=False)
    assert not ok
    assert reason.startswith("page_count:")
    compressed = compress_until_likely_fit(fat, strategy={})
    ok2, _ = assert_one_page(resume=compressed, allow_multi_page=False)
    assert ok2
    assert estimate_pages_from_resume(compressed) <= 1.05
    assert allow_multi_page_requested({"max_pages": 2})
    assert not allow_multi_page_requested({"max_pages": 1})


def test_quality_gates_one_page_flag():
    fat = _fat_resume()
    gates = evaluate_quality_gates(
        tailored_resume=fat,
        original_resume_text="Built REST APIs in Python. PostgreSQL schemas. Docker AWS.",
        require_summary=True,
        require_one_page=True,
    )
    assert any(str(f).startswith("page_count:") for f in gates["failures"])
    compressed = compress_until_likely_fit(fat, strategy={})
    gates2 = evaluate_quality_gates(
        tailored_resume=compressed,
        original_resume_text="Built REST APIs in Python. PostgreSQL schemas. Docker AWS.",
        require_summary=True,
        require_one_page=True,
    )
    assert not any(str(f).startswith("page_count:") for f in gates2["failures"])


def test_summary_plan_defaults_to_one_page_budget():
    plan = build_summary_plan(
        strategy={"honest_title": "Backend Developer", "job_family": "backend"},
        resume_facts={"skills": ["Python"], "projects": [], "experience_roles": []},
        resume_text="Python PostgreSQL FastAPI",
    )
    assert int(plan["maximum_words"]) <= 58


def test_quality_score_includes_one_page_dimension():
    compressed = compress_until_likely_fit(_fat_resume(), strategy={})
    score = evaluate_resume_quality(compressed, strategy={"job_family": "backend"})
    assert "one_page_fit" in score["dimensions"]
    assert score["dimensions"]["one_page_fit"] >= 70
