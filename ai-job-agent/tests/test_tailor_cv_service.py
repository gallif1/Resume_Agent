"""Tests for on-demand ATS CV tailoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
import tailor_cv_service as svc


SAMPLE_STRUCTURED = """## פירוט שינויים
- הודגשו כישורי troubleshooting ו-SQL מתפקיד Technical Support.
- שופץ תקציר מקצועי סביב מילות מפתח של Backend.

## ציון התאמה למשרה
**ציון משוער: 68/100** — התאמה טובה יותר לדרישות החובה.

---

## קורות החיים המעודכנים

# Gal Lifshiz

## Experience
### Technical Support
- Troubleshooting and SQL queries for production systems
"""


def test_save_and_load_tailored_cv(cvs_dir: Path, monkeypatch: pytest.MonkeyPatch):
    cv_id = "cv_test"
    job_id = 42
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)

    path = svc.save_tailored_cv(cv_id, job_id, "# Hello\n\n- bullet\n")
    assert path.exists()
    assert path == cvs_dir / cv_id / "tailored_cvs" / "42.md"
    loaded = svc.load_saved_tailored_cv(cv_id, job_id)
    assert loaded is not None
    assert loaded.startswith("# Hello")


def test_extract_cv_markdown_for_copy_accepts_result_dict():
    """Regression: workspace API used to pass the whole result dict and 500."""
    body = svc.extract_cv_markdown_for_copy(
        {
            "markdown": SAMPLE_STRUCTURED,
            "cv_markdown": "# Gal Lifshiz\n\n## Experience\n",
            "highlights": [],
        }
    )
    assert body.startswith("# Gal Lifshiz")
    assert "פירוט שינויים" not in body

    from_markdown_only = svc.extract_cv_markdown_for_copy(
        {"markdown": SAMPLE_STRUCTURED}
    )
    assert from_markdown_only.startswith("# Gal Lifshiz")


def test_split_tailored_markdown_on_horizontal_rule():
    preamble, body = svc.split_tailored_markdown(SAMPLE_STRUCTURED)
    assert "פירוט שינויים" in preamble
    assert "ציון התאמה" in preamble
    assert body.startswith("# Gal Lifshiz")
    assert "פירוט שינויים" not in body
    assert svc.extract_cv_markdown_for_copy(SAMPLE_STRUCTURED).startswith("# Gal")


def test_normalize_tailor_result_requires_markdown():
    with pytest.raises(svc.TailorCvError):
        svc._normalize_tailor_result({"markdown": "  ", "highlights": []})

    out = svc._normalize_tailor_result(
        {
            "markdown": SAMPLE_STRUCTURED,
            "changes_breakdown": ["הודגשו כישורי SQL"],
            "estimated_ats_score": 68,
            "cv_markdown": "# Gal Lifshiz\n\n## Experience\n",
            "highlights": ["SQL"],
            "caveats": ["No Kubernetes experience claimed"],
        }
    )
    assert "## פירוט שינויים" in out["markdown"]
    assert "---" in out["markdown"]
    assert out["cv_markdown"].startswith("# Gal")
    assert out["estimated_ats_score"] == 68
    assert out["changes_breakdown"] == ["הודגשו כישורי SQL"]
    assert "Kubernetes" in out["caveats"][0]


def test_normalize_assembles_markdown_from_parts():
    out = svc._normalize_tailor_result(
        {
            "changes_breakdown": ["Reframed support bullets around SQL"],
            "estimated_ats_score": 55,
            "cv_markdown": "# Name\n\n## Summary\nBackend-leaning support engineer.\n",
            "caveats": [],
        }
    )
    assert "## פירוט שינויים" in out["markdown"]
    assert "## ציון התאמה למשרה" in out["markdown"]
    assert "---" in out["markdown"]
    assert "## קורות החיים המעודכנים" in out["markdown"]
    assert out["estimated_ats_score"] == 55
    assert "Backend-leaning" in out["cv_markdown"]


def test_tailor_cv_for_job_uses_cache(
    cvs_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cv_id = "cv_cache"
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    profile_dir = cvs_dir / cv_id
    profile_dir.mkdir(parents=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps(
            {
                "raw_text": "Gal Lifshiz\nTechnical Support\nPython, SQL",
                "experience": {
                    "job_titles": ["Technical Support"],
                    "years_of_experience_estimate": 1,
                    "seniority_level": "junior",
                },
                "skills": {"programming_languages": ["Python", "SQL"]},
            }
        ),
        encoding="utf-8",
    )
    svc.save_tailored_cv(cv_id, 7, SAMPLE_STRUCTURED)

    called = {"n": 0}

    def _fake_openai(*_args, **_kwargs):
        called["n"] += 1
        return {"markdown": "# Should not be used\n", "highlights": [], "caveats": []}

    monkeypatch.setattr(svc, "call_openai_json", _fake_openai)
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)

    result = svc.tailor_cv_for_job(
        cv_id,
        {"id": 7, "title": "Software Engineer", "company": "Acme", "full_description": "Python"},
        force=False,
    )
    assert result["from_cache"] is True
    assert "פירוט שינויים" in result["markdown"]
    assert result["cv_markdown"].startswith("# Gal")
    assert result["estimated_ats_score"] == 68
    assert called["n"] == 0


def test_tailor_cv_for_job_calls_openai(
    cvs_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cv_id = "cv_ai"
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    profile_dir = cvs_dir / cv_id
    profile_dir.mkdir(parents=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps(
            {
                "raw_text": "Name\nTechnical Support Engineer\n- Troubleshot Windows and SQL",
                "experience": {
                    "job_titles": ["Technical Support Engineer"],
                    "years_of_experience_estimate": 1,
                    "seniority_level": "junior",
                },
                "skills": {"programming_languages": ["Python", "SQL"]},
            }
        ),
        encoding="utf-8",
    )

    def _fake_openai(*_args, **_kwargs):
        return {
            "markdown": SAMPLE_STRUCTURED.replace("Gal Lifshiz", "Name").replace(
                "### Technical Support", "### Technical Support Engineer"
            ),
            "changes_breakdown": ["Highlighted SQL troubleshooting"],
            "estimated_ats_score": 68,
            "cv_markdown": (
                "# Name\n\n## Experience\n### Technical Support Engineer\n"
                "- Troubleshooting and SQL queries for production systems\n"
            ),
            "highlights": ["SQL troubleshooting"],
            "caveats": ["Did not invent Software Engineer title"],
            "_from_cache": False,
        }

    monkeypatch.setattr(svc, "call_openai_json", _fake_openai)
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)

    result = svc.tailor_cv_for_job(
        cv_id,
        {
            "id": 9,
            "title": "Software Engineer",
            "company": "Acme",
            "full_description": "Need Python and SQL",
            "job_profile": None,
        },
        force=True,
        use_cache=False,
    )
    assert "Technical Support Engineer" in result["cv_markdown"]
    assert result["from_cache"] is False
    # Score is computed deterministically — not the LLM's estimated_ats_score (68).
    assert isinstance(result["estimated_ats_score"], int)
    assert result["estimated_ats_score"] == result.get("score_after")
    assert svc.tailored_cv_path(cv_id, 9).exists()
    assert "Did not invent" in result["caveats"][0]
    # Cache namespace should include prompt version.
    assert "v6" in svc.TAILOR_PROMPT_VERSION
    assert "ONE-PAGE" in svc.TAILOR_SYSTEM_PROMPT or "ONE PAGE" in svc.TAILOR_SYSTEM_PROMPT.upper()
    assert "NEVER OMIT REAL EMPLOYMENT" in svc.TAILOR_SYSTEM_PROMPT
    assert "SQLAlchemy" in svc.TAILOR_SYSTEM_PROMPT


def test_tailor_system_prompt_is_role_agnostic():
    """Prompt must stay universal — no hardcoded roles/companies as the target path."""
    prompt = svc.TAILOR_SYSTEM_PROMPT
    assert "Target Role:" in prompt
    assert "TRANSITION RULE" in prompt
    assert "SEMANTIC SKILLS MATRIX" in prompt
    assert "base_cv_data" in prompt
    assert "job_description" in prompt
    assert "NEVER OMIT REAL EMPLOYMENT" in prompt
    assert "HIDE GHOST SECTIONS" in prompt
    assert "ONE-PAGE DENSITY" in prompt
    assert "CAREER-PIVOT SAFETY RAILS" in prompt
    assert "Core Professional Domain" in prompt
    assert "NEVER hallucinate fake job titles" in prompt
    # Examples of specific career paths must not be baked in as the default narrative.
    for banned in (
        "Technical Support",
        "Backend Developer",
        "keep \"Technical Support\"",
    ):
        assert banned not in prompt


def test_build_tailor_user_prompt_labels_inputs():
    user = svc.build_tailor_user_prompt(
        base_cv_data="RAW CV TEXT HERE",
        job_description="Title: React Frontend Developer\nDescription: React, TypeScript",
    )
    assert "===== base_cv_data =====" in user
    assert "===== job_description =====" in user
    assert "RAW CV TEXT HERE" in user
    assert "React Frontend Developer" in user
    assert "Target Role:" in user


def test_tailor_requires_api_key(cvs_dir: Path, monkeypatch: pytest.MonkeyPatch):
    cv_id = "cv_no_key"
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    profile_dir = cvs_dir / cv_id
    profile_dir.mkdir(parents=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps({"raw_text": "hello", "experience": {"job_titles": ["Support"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(svc, "is_ai_available", lambda: False)
    with pytest.raises(svc.TailorCvError) as exc:
        svc.tailor_cv_for_job(
            cv_id,
            {"id": 1, "title": "Dev", "full_description": "x"},
            force=True,
        )
    assert exc.value.status_code == 503


def test_format_matcher_feedback_lists_gaps():
    text = svc.format_matcher_feedback(
        {
            "ats_score": 54,
            "score_label": "Partial Match",
            "missing_keywords": ["Docker", "Kubernetes"],
            "missing_mandatory_requirements": ["3+ years experience"],
            "cv_improvements": ["Add Docker to skills"],
            "score_reasons": ["Required skills: 1/3 matched"],
            "component_scores": {"required_skills": 33.0},
            "profile_match_score": 48,
        }
    )
    assert "54/100" in text
    assert "Docker" in text
    assert "Kubernetes" in text
    assert "3+ years experience" in text
    assert "original_source_cvs" in text
    assert "latest_tailored_draft" in text


def test_gather_original_source_cvs_includes_master_and_raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CVS_DIR", tmp_path / "cvs")
    (tmp_path / "cvs").mkdir(parents=True)
    profile = {
        "raw_text": "Compiled summary with Docker and Kubernetes",
        "master_profile": {
            "unified_summary": "Master summary mentioning Expo and SQLAlchemy",
            "source_cv_count": 2,
        },
        "skills": {"programming_languages": ["Python"]},
        "experience": {"job_titles": ["Developer"]},
    }
    text = svc.gather_original_source_cvs(
        "cv_solo",
        cv_profile=profile,
    )
    assert "COMPILED MASTER PROFILE" in text
    assert "Expo" in text or "SQLAlchemy" in text
    assert "Docker" in text or "Compiled summary" in text
    assert "COMPILED STRUCTURED PROFILE" in text


def test_evaluate_tailored_draft_detects_missing_skills(cvs_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from job_analyzer import JobProfile

    profile = {
        "raw_text": "Gal\nTechnical Support\nPython SQL",
        "experience": {
            "job_titles": ["Technical Support"],
            "years_of_experience_estimate": 2,
            "seniority_level": "junior",
        },
        "skills": {"programming_languages": ["Python", "SQL"]},
        "universal_profile": {
            "canonical_skills": ["Python", "SQL"],
            "seniority_level": "junior",
            "years_of_experience": 2,
        },
    }
    draft = "# Gal\n\n## Skills\nPython | SQL\n\n## Experience\n- Support work\n"
    job = {"id": 1, "title": "Backend Engineer", "full_description": "Need Python Docker"}
    job_profile = JobProfile(
        title="Backend Engineer",
        required_skills=["Python", "Docker", "Kubernetes"],
        technologies=["Python", "Docker"],
        seniority="junior",
        years_experience_min=1,
    )
    feedback = svc.evaluate_tailored_draft(
        cv_profile=profile,
        draft_markdown=draft,
        job=job,
        job_profile=job_profile,
    )
    assert feedback["ats_score"] is not None
    assert "Docker" in feedback["missing_required_skills"] or "Docker" in feedback["missing_keywords"]
    assert "Python" in feedback["matched_required_skills"]


def test_build_regenerate_user_prompt_includes_sections():
    prompt = svc.build_regenerate_user_prompt(
        original_source_cvs="RAW SOURCE + MASTER",
        latest_tailored_draft="# Draft\nPython",
        ats_feedback_gaps={
            "ats_score": 40,
            "score_label": "Weak Match",
            "missing_keywords": ["Go"],
            "cv_improvements": ["Add Go evidence"],
        },
        job_description="Title: Dev",
    )
    assert "===== ats_feedback_gaps =====" in prompt
    assert "===== latest_tailored_draft =====" in prompt
    assert "===== original_source_cvs =====" in prompt
    assert "===== job_description =====" in prompt
    assert "40/100" in prompt
    assert "Go" in prompt
    assert "dual-lookup" in prompt.lower() or "deep-scan" in prompt.lower() or "Deep-scan" in prompt
    assert "RAW SOURCE + MASTER" in prompt


def test_build_regenerate_user_prompt_accepts_legacy_aliases():
    prompt = svc.build_regenerate_user_prompt(
        base_cv_data="LEGACY_BASE",
        job_description="Title: Dev",
        previous_tailored_cv="# Draft\nPython",
        matcher_feedback={
            "ats_score": 40,
            "score_label": "Weak Match",
            "missing_keywords": ["Go"],
        },
        original_source_cvs="",
        latest_tailored_draft="",
        ats_feedback_gaps="",
    )
    assert "LEGACY_BASE" in prompt
    assert "===== original_source_cvs =====" in prompt
    assert "===== latest_tailored_draft =====" in prompt


def test_regenerate_requires_previous_draft(cvs_dir: Path, monkeypatch: pytest.MonkeyPatch):
    cv_id = "cv_regen_missing"
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    profile_dir = cvs_dir / cv_id
    profile_dir.mkdir(parents=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps({"raw_text": "hello", "experience": {"job_titles": ["Support"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)
    with pytest.raises(svc.TailorCvError) as exc:
        svc.tailor_cv_for_job(
            cv_id,
            {"id": 3, "title": "Dev", "full_description": "x"},
            regenerate=True,
        )
    assert exc.value.status_code == 404


def test_regenerate_sends_matcher_feedback(
    cvs_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cv_id = "cv_regen"
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    profile_dir = cvs_dir / cv_id
    profile_dir.mkdir(parents=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps(
            {
                "raw_text": "Name\nTechnical Support\nPython SQL Docker basics",
                "experience": {
                    "job_titles": ["Technical Support"],
                    "years_of_experience_estimate": 2,
                    "seniority_level": "junior",
                },
                "skills": {
                    "programming_languages": ["Python", "SQL"],
                    "cloud_devops_tools": ["Docker"],
                },
                "universal_profile": {
                    "canonical_skills": ["Python", "SQL", "Docker"],
                    "technologies_tools": ["Docker"],
                    "seniority_level": "junior",
                    "years_of_experience": 2,
                    "core_professional_domain": "Software Development",
                    "preferred_role_titles": ["Backend Engineer", "Technical Support"],
                    "domain_keywords": ["backend", "python", "sql"],
                },
                "core_professional_domain": "Software Development",
            }
        ),
        encoding="utf-8",
    )
    weak_draft = """## פירוט שינויים
- Draft 1

## ציון התאמה למשרה
**ציון משוער: 45/100**

---

## קורות החיים המעודכנים

# Name

## Skills
Python | SQL

## Experience
### Technical Support
- Helped users with Windows
"""
    svc.save_tailored_cv(cv_id, 5, weak_draft)

    captured: dict = {}

    def _fake_openai(system_prompt, user_prompt, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "markdown": "",
            "changes_breakdown": ["Integrated Docker from matcher gaps"],
            "estimated_ats_score": 72,
            "cv_markdown": (
                "# Name\n\n## Skills\nPython | SQL | Docker\n\n"
                "## Experience\n### Technical Support\n"
                "- Supported production apps with Docker containers and SQL\n"
            ),
            "highlights": ["Docker"],
            "caveats": [],
            "_from_cache": False,
        }

    monkeypatch.setattr(svc, "call_openai_json", _fake_openai)
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)

    job = {
        "id": 5,
        "title": "Backend Engineer",
        "company": "Acme",
        "full_description": "Python Docker SQL",
        "job_profile": json.dumps(
            {
                "title": "Backend Engineer",
                "professional_domain": "Software Development",
                "required_skills": ["Python", "Docker", "SQL"],
                "technologies": ["Python", "Docker", "SQL"],
                "seniority": "junior",
                "years_experience_min": 1,
                "hard_constraints": [],
            }
        ),
    }
    result = svc.tailor_cv_for_job(cv_id, job, regenerate=True)
    assert result["regenerated"] is True
    assert result["improved"] is True
    assert result["no_improvement"] is False
    assert result.get("message") is None
    assert "deep-scan" in captured["system"].lower() or "original_source_cvs" in captured["system"]
    assert "===== ats_feedback_gaps =====" in captured["user"]
    assert "===== original_source_cvs =====" in captured["user"]
    assert "===== latest_tailored_draft =====" in captured["user"]
    assert "Docker" in captured["user"]
    assert "Technical Support" in captured["user"] or "Python SQL Docker" in captured["user"]
    assert result["matcher_feedback"]["previous"]["match_score"] is not None
    assert result["matcher_feedback"]["current"]["match_score"] is not None
    assert (
        result["matcher_feedback"]["current"]["match_score"]
        > result["matcher_feedback"]["previous"]["match_score"]
    )
    assert "Docker" in result["cv_markdown"]
    assert svc.tailored_cv_path(cv_id, 5).exists()
    # Deterministic score should be embedded in the returned markdown.
    assert result["estimated_ats_score"] == result["matcher_feedback"]["current"]["match_score"]


def test_regenerate_discards_when_score_not_improved(
    cvs_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cv_id = "cv_regen_guard"
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    profile_dir = cvs_dir / cv_id
    profile_dir.mkdir(parents=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps(
            {
                "raw_text": "Name\nTechnical Support\nPython SQL Docker",
                "experience": {
                    "job_titles": ["Technical Support"],
                    "years_of_experience_estimate": 2,
                    "seniority_level": "junior",
                },
                "skills": {
                    "programming_languages": ["Python", "SQL"],
                    "cloud_devops_tools": ["Docker"],
                },
                "universal_profile": {
                    "canonical_skills": ["Python", "SQL", "Docker"],
                    "technologies_tools": ["Docker"],
                    "seniority_level": "junior",
                    "years_of_experience": 2,
                },
            }
        ),
        encoding="utf-8",
    )
    strong_draft = """## פירוט שינויים
- Strong draft with Python SQL Docker

## ציון התאמה למשרה
**ציון משוער: 80/100**

---

## קורות החיים המעודכנים

# Name

## Skills
Python | SQL | Docker

## Experience
### Technical Support
- Production support with Python, SQL, and Docker
"""
    path = svc.save_tailored_cv(cv_id, 8, strong_draft)
    original_text = path.read_text(encoding="utf-8")

    def _fake_openai(*_args, **_kwargs):
        # Deliberately weaker wording that omits Docker/SQL keywords.
        return {
            "markdown": "",
            "changes_breakdown": ["Rewrote summary"],
            "estimated_ats_score": 90,
            "cv_markdown": (
                "# Name\n\n## Skills\nCommunication\n\n"
                "## Experience\n### Technical Support\n"
                "- Helped users politely\n"
            ),
            "highlights": [],
            "caveats": [],
            "_from_cache": False,
        }

    monkeypatch.setattr(svc, "call_openai_json", _fake_openai)
    monkeypatch.setattr(svc, "is_ai_available", lambda: True)

    job = {
        "id": 8,
        "title": "Backend Engineer",
        "company": "Acme",
        "full_description": "Python Docker SQL",
        "job_profile": json.dumps(
            {
                "title": "Backend Engineer",
                "required_skills": ["Python", "Docker", "SQL"],
                "technologies": ["Python", "Docker", "SQL"],
                "seniority": "junior",
                "years_experience_min": 1,
            }
        ),
    }
    result = svc.tailor_cv_for_job(cv_id, job, regenerate=True)
    assert result["no_improvement"] is True
    assert result["improved"] is False
    assert result["regenerated"] is False
    assert result["message"] == svc.NO_IMPROVEMENT_MESSAGE
    assert "Docker" in result["cv_markdown"]
    assert "Helped users politely" not in result["cv_markdown"]
    assert path.read_text(encoding="utf-8") == original_text
    assert result["matcher_feedback"]["discarded"]["match_score"] <= result[
        "matcher_feedback"
    ]["previous"]["match_score"]