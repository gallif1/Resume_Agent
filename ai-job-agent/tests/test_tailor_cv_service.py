"""Tests for the tailored-CV document layer.

Scoring and resume writing belong to match_tailor_service; this module only owns
the document, its persistence and the API-facing payload. The tests therefore
stub the engine's OpenAI call and assert on what the user ends up seeing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import config
import match_tailor_service
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

PROFILE = {
    "contact": {
        "name": "Gal Lifshiz",
        "email": "gal@example.com",
        "phone": "+972-50-000-0000",
        "location": "Tel Aviv",
    },
    "raw_text": (
        "Gal Lifshiz — Technical Support Engineer at Acme. Troubleshot Windows "
        "and SQL issues, automated reports with Python, ran Docker containers "
        "and stored data in Postgres."
    ),
    "skills": {"programming_languages": ["Python", "SQL"]},
    "experience": {
        "job_titles": ["Technical Support Engineer"],
        "years_of_experience_estimate": 2,
        "seniority_level": "junior",
    },
}

JOB = {
    "id": 9,
    "title": "Backend Engineer",
    "company": "Acme",
    "full_description": "Python, SQL and Docker experience required.",
    "job_profile": None,
}


def _engine_response(
    *,
    score: int = 62,
    skills: list[str] | None = None,
    hard_statuses: tuple[str, ...] = ("MATCH", "PARTIAL"),
) -> dict[str, Any]:
    return {
        "requirement_extraction": {
            "hard_requirements": [
                {
                    "requirement": f"Requirement {index}",
                    "candidate_status": status,
                    "evidence_or_gap": "evidence",
                }
                for index, status in enumerate(hard_statuses, start=1)
            ],
            "soft_requirements": [
                {
                    "requirement": "Docker",
                    "candidate_status": "MATCH",
                    "evidence_or_gap": "Ran Docker containers",
                }
            ],
        },
        "scoring": {
            "hard_score_pct": 75,
            "soft_score_pct": 100,
            "hard_cap_applied": False,
            "realistic_match_score": score,
            "score_rationale": "Solid Python and SQL overlap.",
        },
        "key_matching_points": ["Python automation", "SQL troubleshooting"],
        "missing_critical_skills": ["Kubernetes"],
        "transferable_skills_framing": [
            {
                "gap": "Kubernetes",
                "how_to_honestly_frame_existing_experience": (
                    "Docker container work is the closest honest parallel."
                ),
            }
        ],
        "tailored_cv": {
            "summary": "Support engineer moving into backend work.",
            "skills": skills if skills is not None else ["Python", "SQL", "Docker"],
            "experience": [
                {
                    "company": "Acme",
                    "title": "Technical Support Engineer",
                    "dates": "2023-2025",
                    "bullets": [
                        "Troubleshot production Windows and SQL incidents daily.",
                        "Automated recurring reports with Python, saving hours weekly.",
                    ],
                }
            ],
            "projects": [],
            "education": [],
        },
        "recommendation": "APPLY_WITH_HONEST_FRAMING",
    }


@pytest.fixture
def cv_env(cvs_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)

    def _make(cv_id: str, profile: dict[str, Any] | None = None) -> str:
        profile_dir = cvs_dir / cv_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "cv_profile.json").write_text(
            json.dumps(profile or PROFILE), encoding="utf-8"
        )
        return cv_id

    return _make


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch):
    state: dict[str, Any] = {"calls": 0, "response": _engine_response()}

    def _fake_openai(*_args, **_kwargs):
        state["calls"] += 1
        return dict(state["response"])

    monkeypatch.setattr(match_tailor_service, "is_ai_available", lambda: True)
    monkeypatch.setattr(match_tailor_service, "call_openai_json", _fake_openai)
    return state


# --------------------------------------------------------------------------- #
# Document plumbing
# --------------------------------------------------------------------------- #


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
    # The pipeline marker lives on disk only.
    assert "tailor-pipeline" in path.read_text(encoding="utf-8")
    assert "tailor-pipeline" not in loaded
    assert svc.saved_draft_is_current(cv_id, job_id) is True


def test_saved_draft_from_another_pipeline_is_not_current(
    cvs_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    path = svc.tailored_cv_path("cv_legacy", 5)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SAMPLE_STRUCTURED, encoding="utf-8")
    assert svc.load_saved_tailored_cv("cv_legacy", 5) is not None
    assert svc.saved_draft_is_current("cv_legacy", 5) is False


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


# --------------------------------------------------------------------------- #
# tailored_cv JSON -> resume Markdown
# --------------------------------------------------------------------------- #


def test_render_tailored_cv_markdown_covers_every_section():
    markdown = svc.render_tailored_cv_markdown(
        {
            "summary": "Backend developer with production Python experience.",
            "skills": ["Languages: Python, SQL", "Docker", "Linux"],
            "experience": [
                {
                    "title": "Backend Developer",
                    "company": "Acme",
                    "dates": "2023-2025",
                    "bullets": ["Built FastAPI services on AWS."],
                }
            ],
            "projects": [
                {
                    "name": "Job Agent",
                    "description": "Automation platform",
                    "bullets": ["Shipped a React client."],
                }
            ],
            "education": [
                {
                    "degree": "B.Sc. Computer Science",
                    "institution": "Open University",
                    "dates": "2019-2022",
                }
            ],
        },
        name="Gal Lifshiz",
        contact_line="Tel Aviv | gal@example.com",
        target_role="Backend Engineer",
    )

    assert markdown.startswith("# Gal Lifshiz")
    assert "Tel Aviv | gal@example.com" in markdown
    assert "Target Role: Backend Engineer" in markdown
    for heading in ("## Professional Summary", "## Experience", "## Projects", "## Skills", "## Education"):
        assert heading in markdown
    assert "### Backend Developer" in markdown
    assert "Acme | 2023-2025" in markdown
    assert "- Built FastAPI services on AWS." in markdown
    # Grouped rows stay grouped; ungrouped skills share one row.
    assert "Languages: Python, SQL" in markdown
    assert "Docker, Linux" in markdown
    assert "Open University | 2019-2022" in markdown


def test_render_tailored_cv_markdown_omits_sections_without_source_material():
    markdown = svc.render_tailored_cv_markdown(
        {
            "summary": "Junior developer.",
            "skills": ["Python"],
            "experience": [],
            "projects": [],
            "education": [],
        },
        name="Dana",
    )
    assert "## Education" not in markdown
    assert "## Projects" not in markdown
    assert "## Skills" in markdown


def test_header_facts_come_from_the_profile_not_the_model():
    name, contact, role = svc.build_resume_header(PROFILE, JOB)
    assert name == "Gal Lifshiz"
    assert "gal@example.com" in contact
    assert "Tel Aviv" in contact
    assert role == "Backend Engineer"


# --------------------------------------------------------------------------- #
# Production tailoring flow
# --------------------------------------------------------------------------- #


def test_tailor_cv_for_job_uses_the_honest_engine(cv_env, engine):
    cv_id = cv_env("cv_ai")
    result = svc.tailor_cv_for_job(cv_id, JOB, force=True, use_cache=False)

    assert engine["calls"] == 1
    # The rubric recomputes the score server-side; the model's 62 is advisory.
    assert result["score_after"] == 77
    assert result["estimated_ats_score"] == 77
    assert result["realistic_match_score"] == 77
    assert result["recommendation"] == "APPLY_WITH_HONEST_FRAMING"
    assert result["score_validation"]["model_reported_score"] == 62
    assert result["score_validation"]["recomputed_composite_score"] == 77
    assert result["score_validation"]["score_overridden"] is True
    assert result["from_cache"] is False

    # The document is assembled from the evaluation.
    assert "## פירוט שינויים" in result["markdown"]
    assert "## ציון התאמה למשרה" in result["markdown"]
    assert "Solid Python and SQL overlap." in result["markdown"]
    assert result["cv_markdown"].startswith("# Gal Lifshiz")
    assert "Technical Support Engineer" in result["cv_markdown"]
    assert any("Kubernetes" in caveat for caveat in result["caveats"])
    assert svc.tailored_cv_path(cv_id, 9).exists()

    feedback = result["matcher_feedback"]["current"]
    assert feedback["match_score"] == 77
    assert feedback["missing_keywords"] == ["Kubernetes"]


def test_tailor_cv_for_job_serves_a_current_draft_without_calling_the_model(
    cv_env, engine
):
    cv_id = cv_env("cv_cache")
    first = svc.tailor_cv_for_job(cv_id, JOB, force=True, use_cache=False)
    second = svc.tailor_cv_for_job(cv_id, JOB, force=False)

    assert engine["calls"] == 1
    assert second["from_cache"] is True
    assert second["cv_markdown"].startswith("# Gal Lifshiz")
    assert second["estimated_ats_score"] == first["score_after"]


def test_first_generate_reports_one_score_without_a_fake_improvement(cv_env, engine):
    cv_id = cv_env("cv_single_score")
    result = svc.tailor_cv_for_job(cv_id, JOB, force=True, use_cache=False)

    # No prior honest evaluation exists, so there is no progression to claim.
    assert result["score_before"] == result["score_after"]
    assert result["initial_match_score"] == result["score_after"]
    assert "שיפרנו" not in result["markdown"]
    assert f"ציון ההתאמה למשרה: {result['score_after']}" in result["markdown"]


def test_unsupported_skills_are_stripped_but_reworded_ones_survive(cv_env, engine):
    cv_id = cv_env("cv_skills")
    engine["response"] = _engine_response(
        skills=["PostgreSQL", "Docker", "Salesforce Apex", "Kubernetes Operators"]
    )
    result = svc.tailor_cv_for_job(cv_id, JOB, force=True, use_cache=False)

    resume = result["cv_markdown"]
    assert "PostgreSQL" in resume  # profile says "Postgres"
    assert "Docker" in resume
    assert "Salesforce Apex" not in resume
    assert "Kubernetes Operators" not in resume
    dropped = result["score_validation"]["dropped_unsupported_skills"]
    assert "Salesforce Apex" in dropped


def test_tailor_requires_an_api_key(cv_env, monkeypatch: pytest.MonkeyPatch):
    cv_id = cv_env("cv_no_key")
    monkeypatch.setattr(match_tailor_service, "is_ai_available", lambda: False)
    with pytest.raises(svc.TailorCvError) as exc:
        svc.tailor_cv_for_job(cv_id, JOB, force=True)
    assert exc.value.status_code == 503


def test_missing_profile_raises_404(cvs_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    with pytest.raises(svc.TailorCvError) as exc:
        svc.tailor_cv_for_job("cv_unknown", JOB, force=True)
    assert exc.value.status_code == 404


def test_gather_original_source_cvs_includes_master_and_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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
    text = svc.gather_original_source_cvs("cv_solo", cv_profile=profile)
    assert "COMPILED MASTER PROFILE" in text
    assert "Expo" in text or "SQLAlchemy" in text
    assert "Docker" in text or "Compiled summary" in text
    assert "COMPILED STRUCTURED PROFILE" in text


def test_tailoring_sends_original_source_documents_to_the_engine(
    cv_env, monkeypatch: pytest.MonkeyPatch
):
    """Completeness needs full history, not just the parsed profile."""
    cv_id = cv_env("cv_sources")
    captured: dict[str, str] = {}

    def _fake_openai(_system, user_prompt, **_kwargs):
        captured["user"] = user_prompt
        return _engine_response()

    monkeypatch.setattr(match_tailor_service, "is_ai_available", lambda: True)
    monkeypatch.setattr(match_tailor_service, "call_openai_json", _fake_openai)

    svc.tailor_cv_for_job(cv_id, JOB, force=True, use_cache=False)
    assert "ORIGINAL SOURCE DOCUMENTS" in captured["user"]
    assert "Postgres" in captured["user"]


# --------------------------------------------------------------------------- #
# Regenerate ("improve match")
# --------------------------------------------------------------------------- #


def test_regenerate_requires_a_previous_draft(cv_env, engine):
    cv_id = cv_env("cv_regen_missing")
    with pytest.raises(svc.TailorCvError) as exc:
        svc.tailor_cv_for_job(cv_id, {**JOB, "id": 3}, regenerate=True)
    assert exc.value.status_code == 404


def test_regenerate_keeps_the_better_scoring_draft(cv_env, engine):
    cv_id = cv_env("cv_regen")
    job = {**JOB, "id": 5}
    first = svc.tailor_cv_for_job(cv_id, job, force=True, use_cache=False)
    assert first["score_after"] == 77

    # A fresh pass that finds evidence for the partially-met requirement.
    engine["response"] = _engine_response(hard_statuses=("MATCH", "MATCH"))
    improved = svc.tailor_cv_for_job(cv_id, job, regenerate=True)

    assert improved["regenerated"] is True
    assert improved["improved"] is True
    assert improved["no_improvement"] is False
    assert improved["score_before"] == 77
    assert improved["score_after"] == 100
    assert "שיפרנו את ההתאמה למשרה מ־77 ל־100" in improved["markdown"]
    assert improved["matcher_feedback"]["previous"]["match_score"] == 77
    assert improved["matcher_feedback"]["current"]["match_score"] == 100


def test_regenerate_discards_a_draft_that_does_not_score_better(cv_env, engine):
    cv_id = cv_env("cv_regen_guard")
    job = {**JOB, "id": 8}
    first = svc.tailor_cv_for_job(cv_id, job, force=True, use_cache=False)
    saved_before = svc.tailored_cv_path(cv_id, 8).read_text(encoding="utf-8")

    engine["response"] = _engine_response(hard_statuses=("MISSING", "MISSING"))
    result = svc.tailor_cv_for_job(cv_id, job, regenerate=True)

    assert result["no_improvement"] is True
    assert result["improved"] is False
    assert result["regenerated"] is False
    assert result["message"] == svc.NO_IMPROVEMENT_MESSAGE
    assert result["score_after"] == first["score_after"]
    assert svc.tailored_cv_path(cv_id, 8).read_text(encoding="utf-8") == saved_before
    assert (
        result["matcher_feedback"]["discarded"]["match_score"]
        <= result["matcher_feedback"]["previous"]["match_score"]
    )


def test_no_second_tailoring_prompt_exists():
    """Guard against a parallel tailoring path creeping back in."""
    source = Path(svc.__file__).read_text(encoding="utf-8")
    for banned in (
        "SYSTEM_PROMPT",
        "call_openai_json",
        "resume_generator_prompt",
        "ats_scorer",
        "profile_matcher",
    ):
        assert banned not in source, f"{banned} must live in match_tailor_service only"
