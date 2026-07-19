"""Tests for rule-based job analysis fallback."""

from __future__ import annotations

from job_analyzer import analyze_job_fallback, job_profile_hash


def test_fallback_extracts_skills_and_years():
    job = {
        "title": "Junior Python Developer",
        "company": "Acme",
        "location": "Tel Aviv",
        "description": "",
        "full_description": (
            "We need a junior Python developer with 2+ years experience. "
            "Must have AWS and Docker. Fluent English required. Remote work."
        ),
    }
    profile = analyze_job_fallback(job)
    assert profile.analyzed_with == "rules"
    assert "Python" in profile.required_skills or "Python" in profile.technologies
    assert profile.years_experience_min == 2.0
    assert profile.seniority == "junior"
    assert "English" in profile.languages
    assert profile.location_type == "remote"
    assert profile.professional_domain
    assert "Python" in profile.professional_domain or "Developer" in profile.professional_domain
    assert profile.hard_constraints  # must-have lines / years become hard constraints


def test_job_profile_hash_changes_with_content():
    job_a = {"title": "Dev", "description": "Python", "full_description": "Python AWS"}
    job_b = {"title": "Dev", "description": "Python", "full_description": "Python Rust"}
    assert job_profile_hash(job_a) != job_profile_hash(job_b)


def test_analyze_job_openai_cache_uses_job_profile_hash(monkeypatch):
    job = {
        "title": "Dev",
        "company": "Acme",
        "location": "TLV",
        "description": "short",
        "full_description": "Python developer",
        "job_url": "https://example.com/1",
    }
    seen: list[str] = []

    def fake_call_openai_json(*_args, **kwargs):
        seen.append(kwargs.get("cache_payload", ""))
        return {
            "title": "Dev",
            "professional_domain": "Software Development",
            "seniority": "junior",
            "required_skills": ["Python"],
            "preferred_skills": [],
            "mandatory_requirements": [],
            "hard_constraints": ["2+ years Python experience"],
            "years_experience_min": 2,
            "education": [],
            "languages": [],
            "certifications": [],
            "location_type": "onsite",
            "location": "TLV",
            "technologies": ["Python"],
            "responsibilities": [],
        }

    monkeypatch.setattr("job_analyzer.is_ai_available", lambda: True)
    monkeypatch.setattr("job_analyzer.call_openai_json", fake_call_openai_json)

    from job_analyzer import analyze_job_with_openai

    profile = analyze_job_with_openai(job)
    assert profile.title == "Dev"
    assert seen
    assert job_profile_hash(job) in seen[0]
