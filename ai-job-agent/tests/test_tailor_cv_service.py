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
    assert result["estimated_ats_score"] == 68
    assert svc.tailored_cv_path(cv_id, 9).exists()
    assert "Did not invent" in result["caveats"][0]
    # Cache namespace should include prompt version.
    assert "v3" in svc.TAILOR_PROMPT_VERSION


def test_tailor_system_prompt_is_role_agnostic():
    """Prompt must stay universal — no hardcoded roles/companies as the target path."""
    prompt = svc.TAILOR_SYSTEM_PROMPT
    assert "Target Role:" in prompt
    assert "TRANSITION RULE" in prompt
    assert "SEMANTIC SKILLS MATRIX" in prompt
    assert "base_cv_data" in prompt
    assert "job_description" in prompt
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
