"""Tests for rule-based best-fit role suggestions from CV signals."""

from __future__ import annotations

from parse_cv import suggest_best_fit_roles


def test_suggest_best_fit_roles_includes_skill_and_past_title_tracks():
    """Any profession: skill signals + past titles become domains, uncapped."""
    skills = {
        "programming_languages": ["Python"],
        "frameworks_libraries": ["FastAPI", "React"],
        "databases": ["PostgreSQL"],
        "cloud_devops_tools": ["Linux", "AWS"],
        "cyber_security": ["Networking", "TCP/IP"],
        "data_ai": [],
        "soft_skills": [],
        "healthcare": [],
        "marketing_sales": [],
        "finance_accounting": [],
        "design_creative": [],
        "operations_logistics": [],
        "hr_admin": [],
    }
    experience = {
        "job_titles": [
            "Technical Support Specialist",
            "Python Programming Tutor",
            "Volunteer Coordinator",
        ],
        "management_experience": False,
    }
    roles = suggest_best_fit_roles(skills, experience)
    roles_l = {r.casefold() for r in roles}

    # Skill-derived families (generic across categories).
    assert "backend developer" in roles_l or "software developer" in roles_l
    # Past titles always surface — profession-agnostic, no fixed count trim.
    assert "technical support specialist" in roles_l
    assert "python programming tutor" in roles_l
    assert "volunteer coordinator" in roles_l
    assert len(roles) >= 5


def test_suggest_best_fit_roles_works_for_non_tech_cv():
    skills = {
        "programming_languages": [],
        "frameworks_libraries": [],
        "databases": [],
        "cloud_devops_tools": [],
        "cyber_security": [],
        "data_ai": [],
        "soft_skills": ["Customer Service"],
        "healthcare": ["Patient Care", "Nursing"],
        "marketing_sales": [],
        "finance_accounting": [],
        "design_creative": [],
        "operations_logistics": [],
        "hr_admin": [],
    }
    experience = {
        "job_titles": ["Registered Nurse", "Clinic Coordinator"],
        "management_experience": False,
    }
    roles = suggest_best_fit_roles(skills, experience)
    roles_l = {r.casefold() for r in roles}
    assert "registered nurse" in roles_l
    assert "clinic coordinator" in roles_l
    assert "healthcare assistant" in roles_l or "physician" in roles_l
