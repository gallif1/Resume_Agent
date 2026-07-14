"""Tests for on-demand ATS CV tailoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
import tailor_cv_service as svc


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


def test_normalize_tailor_result_requires_markdown():
    with pytest.raises(svc.TailorCvError):
        svc._normalize_tailor_result({"markdown": "  ", "highlights": []})

    out = svc._normalize_tailor_result(
        {
            "markdown": "# CV\n",
            "highlights": ["SQL"],
            "caveats": ["No Kubernetes experience claimed"],
        }
    )
    assert out["markdown"].startswith("# CV")
    assert out["highlights"] == ["SQL"]
    assert "Kubernetes" in out["caveats"][0]


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
    svc.save_tailored_cv(cv_id, 7, "# Cached tailored CV\n")

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
    assert "Cached tailored" in result["markdown"]
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
            "markdown": (
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
    assert "Technical Support Engineer" in result["markdown"]
    assert result["from_cache"] is False
    assert svc.tailored_cv_path(cv_id, 9).exists()
    assert "Did not invent" in result["caveats"][0]


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
