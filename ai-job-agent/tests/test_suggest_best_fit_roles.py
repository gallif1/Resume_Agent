"""Tests for rule-based best-fit role suggestions from CV signals."""

from __future__ import annotations

from parse_cv import suggest_best_fit_roles


def test_suggest_best_fit_roles_covers_backend_support_and_cyber_tracks():
    skills = {
        "programming_languages": ["Python"],
        "frameworks_libraries": ["FastAPI", "React"],
        "databases": ["PostgreSQL"],
        "cloud_devops_tools": ["Linux", "AWS"],
        "cyber_security": ["Networking", "TCP/IP", "Cybersecurity"],
        "data_ai": ["Machine Learning"],
        "soft_skills": [],
    }
    experience = {
        "job_titles": ["Technical Support Specialist", "Python Programming Tutor"],
        "management_experience": False,
    }
    roles = suggest_best_fit_roles(skills, experience)
    roles_l = {r.casefold() for r in roles}

    assert "backend developer" in roles_l or "python developer" in roles_l
    assert "technical support specialist" in roles_l or "it support" in roles_l
    assert "soc analyst" in roles_l or "cybersecurity analyst" in roles_l
    assert "fastapi developer" in roles_l or "python developer" in roles_l
