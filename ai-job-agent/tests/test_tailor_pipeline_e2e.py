"""End-to-end tests for the production tailored-CV flow.

These go through the endpoint the web app actually calls
(``POST /jobs/{job_id}/tailor-cv``) rather than the match-report endpoints, so a
regression that re-introduces a second, unfixed tailoring path fails here.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import api_server
import config
import cv_service
import db
import match_tailor_service
import pdf_generator_service as pdf
import pytest
import tailor_cv_service


@pytest.fixture
def workspace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path):
    cvs_dir = tmp_path / "cvs"
    users_dir = tmp_path / "users"
    cvs_dir.mkdir()
    users_dir.mkdir()
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    monkeypatch.setattr(config, "USERS_DIR", users_dir)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "ensure_multi_cv_storage", lambda: None)

    user_id = db.DEFAULT_USER_ID
    user_db = config.user_db_path(user_id)
    db.init_db(user_db)
    return {"db_path": db_path, "user_db": user_db, "user_id": user_id}


CANDIDATE_PROFILE = {
    "contact": {
        "name": "Gal Lifshitz",
        "email": "gal@example.com",
        "phone": "+972-50-000-0000",
        "location": "Tel Aviv",
        "github": "github.com/gal",
    },
    "raw_text": (
        "Gal Lifshitz — Backend Developer\n"
        "Acme (2023-2025): built Python/FastAPI services on AWS Lambda, "
        "storing data in Postgres and caching with Redis. Set up CICD in "
        "GitHub Actions and communicated with stakeholders weekly.\n"
        "Project: Job Agent — React front end over a FastAPI backend."
    ),
    "skills": {
        "programming_languages": ["Python", "SQL"],
        "frameworks": ["FastAPI", "React"],
        "cloud_devops_tools": ["AWS", "Docker"],
    },
    "experience": {
        "job_titles": ["Backend Developer"],
        "years_of_experience_estimate": 2,
        "seniority_level": "junior",
    },
}


def _write_profile(user_id: str, profile: dict[str, Any] | None = None) -> None:
    profile_dir = config.user_data_dir(user_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "cv_profile.json").write_text(
        json.dumps(profile or CANDIDATE_PROFILE), encoding="utf-8"
    )


def _inflated_salesforce_response() -> dict[str, Any]:
    """Model output that overrates a Salesforce role and pads the skills list.

    ``PostgreSQL`` / ``CI/CD`` / ``Stakeholder communication`` are all supported by
    the profile under different spellings and must survive. ``Salesforce Apex`` is
    not supported anywhere and must be stripped.
    """
    return {
        "requirement_extraction": {
            "hard_requirements": [
                {
                    "requirement": "3+ years Salesforce Apex development",
                    "candidate_status": "MISSING",
                    "evidence_or_gap": "No Salesforce experience on the resume",
                },
                {
                    "requirement": "Lightning Web Components",
                    "candidate_status": "MISSING",
                    "evidence_or_gap": "No LWC experience",
                },
                {
                    "requirement": "Python scripting",
                    "candidate_status": "MATCH",
                    "evidence_or_gap": "Python/FastAPI services at Acme",
                },
            ],
            "soft_requirements": [
                {
                    "requirement": "AWS familiarity",
                    "candidate_status": "MATCH",
                    "evidence_or_gap": "AWS Lambda automation",
                },
                {
                    "requirement": "Stakeholder communication",
                    "candidate_status": "MATCH",
                    "evidence_or_gap": "Weekly stakeholder updates",
                },
            ],
        },
        "scoring": {
            "hard_score_pct": 33,
            "soft_score_pct": 100,
            "hard_cap_applied": False,
            "realistic_match_score": 88,
            "score_rationale": "Great Python overlap makes this a strong fit.",
        },
        "key_matching_points": ["Python/FastAPI services", "AWS Lambda automation"],
        "missing_critical_skills": [],
        "transferable_skills_framing": [
            {
                "gap": "Salesforce Apex",
                "how_to_honestly_frame_existing_experience": (
                    "Python business-logic services show the same modelling skills."
                ),
            }
        ],
        "tailored_cv": {
            "summary": "Backend developer with two years of Python and AWS delivery.",
            "skills": [
                "Languages: Python, SQL",
                "Databases: PostgreSQL, Redis",
                "CI/CD",
                "Stakeholder communication",
                "Salesforce Apex",
            ],
            "experience": [
                {
                    "company": "Acme",
                    "title": "Backend Developer",
                    "dates": "2023-2025",
                    "bullets": [
                        "Built Python/FastAPI services on AWS Lambda serving internal teams.",
                        "Modelled relational data in PostgreSQL and cached hot reads in Redis.",
                    ],
                }
            ],
            "projects": [
                {
                    "name": "Job Agent",
                    "description": "Full-stack job search automation platform",
                    "bullets": ["Built a React front end over a FastAPI backend."],
                }
            ],
            "education": [],
        },
        "recommendation": "STRONG_APPLY",
    }


def _patch_engine(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]) -> dict:
    calls: dict[str, Any] = {"count": 0, "prompts": []}

    def _fake_openai(system_prompt, user_prompt, **_kwargs):
        calls["count"] += 1
        calls["prompts"].append((system_prompt, user_prompt))
        return dict(response)

    monkeypatch.setattr(match_tailor_service, "is_ai_available", lambda: True)
    monkeypatch.setattr(match_tailor_service, "call_openai_json", _fake_openai)
    return calls


def _seed_job(user_db: Path, *, title: str, company: str, description: str) -> int:
    job_id = db.insert_job(
        title=title,
        job_url=f"https://example.com/job/{title.replace(' ', '-')}",
        company=company,
        description=description,
        db_path=user_db,
    )
    assert job_id is not None
    scan_id = db.create_scan(db.WORKSPACE_CV_ID, db_path=user_db)
    db.upsert_cv_job_match(
        db.WORKSPACE_CV_ID,
        job_id,
        {
            "match_score": 76,
            "match_reason": "keyword scan",
            "match_method": "local",
            "matched_skills": "[]",
            "missing_skills": "[]",
            "candidate_strategy_hash": "h",
        },
        scan_id=scan_id,
        db_path=user_db,
    )
    return int(job_id)


def test_production_tailor_flow_caps_the_dot_compliance_salesforce_score(
    workspace_env, monkeypatch: pytest.MonkeyPatch
):
    """The real tailor endpoint must apply the hard cap, not the model's 88."""
    user_db = workspace_env["user_db"]
    _write_profile(workspace_env["user_id"])
    job_id = _seed_job(
        user_db,
        title="Salesforce Developer (Apex, LWC)",
        company="Dot Compliance",
        description="3+ years Apex, LWC. Python/AWS scripting a plus.",
    )
    calls = _patch_engine(monkeypatch, _inflated_salesforce_response())

    cv = cv_service.upload_cv("a.pdf", b"e2e-bytes", db_path=workspace_env["db_path"])
    real_get_cv = db.get_cv
    monkeypatch.setattr(
        api_server.db,
        "get_cv",
        lambda cv_id, **kw: real_get_cv(cv_id, db_path=workspace_env["db_path"]),
    )

    from conftest import authed_client

    with authed_client() as client:
        res = client.post(
            f"/jobs/{job_id}/tailor-cv",
            params={"source_cv_id": cv["id"]},
            json={"force": True},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert calls["count"] == 1, "the tailor endpoint must call the honest engine"

    # Hard cap rule and score validation are exercised by the production flow.
    assert body["score_validation"]["model_reported_score"] == 88
    assert body["score_validation"]["score_overridden"] is True
    assert body["score_validation"]["cap"] is not None
    assert body["realistic_match_score"] <= 55
    assert body["score_after"] == body["realistic_match_score"]
    assert body["estimated_ats_score"] == body["realistic_match_score"]
    assert body["recommendation"] != "STRONG_APPLY"

    # The gap is named rather than papered over.
    assert any(
        "salesforce" in item.lower() or "apex" in item.lower()
        for item in body["missing_critical_skills"]
    )
    assert any("Salesforce" in caveat or "Apex" in caveat for caveat in body["caveats"])

    # No inflated "we improved your match" claim against the scan estimate of 76.
    assert "שיפרנו" not in body["markdown"]
    assert str(body["realistic_match_score"]) in body["markdown"]

    # An unsupported skill is stripped; differently-spelled real ones are kept.
    resume = body["cv_markdown"]
    assert "Salesforce Apex" not in resume
    assert "PostgreSQL" in resume
    assert "CI/CD" in resume
    assert "Stakeholder communication" in resume
    assert body["score_validation"]["dropped_unsupported_skills"] == ["Salesforce Apex"]

    # The job card is updated so the list cannot contradict the tailored view.
    match = db.get_cv_job_match(db.WORKSPACE_CV_ID, job_id, db_path=user_db)
    assert match["match_score"] == body["realistic_match_score"]
    assert match["match_method"] == "match_tailor"
    assert match["initial_score"] == 76, "the frozen scan baseline is preserved"


def test_tailored_cv_renders_skills_into_the_downloaded_pdf(
    workspace_env, monkeypatch: pytest.MonkeyPatch
):
    """JSON -> saved draft -> HTML/PDF: a populated Skills section must survive."""
    user_db = workspace_env["user_db"]
    _write_profile(workspace_env["user_id"])
    job_id = _seed_job(
        user_db,
        title="Backend Engineer",
        company="Acme",
        description="Python, PostgreSQL, AWS.",
    )
    _patch_engine(monkeypatch, _inflated_salesforce_response())

    cv = cv_service.upload_cv("b.pdf", b"pdf-bytes", db_path=workspace_env["db_path"])
    real_get_cv = db.get_cv
    monkeypatch.setattr(
        api_server.db,
        "get_cv",
        lambda cv_id, **kw: real_get_cv(cv_id, db_path=workspace_env["db_path"]),
    )

    from conftest import authed_client

    with authed_client() as client:
        res = client.post(
            f"/jobs/{job_id}/tailor-cv",
            params={"source_cv_id": cv["id"]},
            json={"force": True},
        )
        assert res.status_code == 200, res.text
        pdf_res = client.get(
            f"/cvs/{db.WORKSPACE_CV_ID}/jobs/{job_id}/tailored-cv/download-pdf"
        )

    saved = tailor_cv_service.load_saved_tailored_cv(db.WORKSPACE_CV_ID, job_id)
    assert saved is not None
    body = tailor_cv_service.extract_cv_markdown_for_copy(saved)

    # The real renderer, not a stub: skills reach the printed document.
    html_doc = pdf.markdown_to_resume_html(body)
    assert 'class="skills-container"' in html_doc
    assert "skills-line" in html_doc
    for skill in ("Python", "SQL", "PostgreSQL", "Redis", "CI/CD"):
        assert skill in html_doc, f"{skill} missing from the rendered resume"
    # Header facts come from the verified profile, never from the model.
    assert "Gal Lifshitz" in html_doc
    assert "gal@example.com" in html_doc
    assert "Backend Engineer" in html_doc  # Target Role
    # Every section that has source material renders with content.
    for section in ("Experience", "Projects", "Skills"):
        assert f">{section}</h2>" in html_doc or section.upper() in html_doc

    if pdf_res.status_code == 503:  # Chromium not installed in this environment
        pytest.skip("Playwright Chromium unavailable")
    assert pdf_res.status_code == 200, pdf_res.text
    assert pdf_res.content.startswith(b"%PDF")
    assert len(pdf_res.content) > 2000


def test_skills_survive_into_the_pdf_file_bytes(
    workspace_env, monkeypatch: pytest.MonkeyPatch
):
    """API call -> PDF file: the Skills section of the printed page is not empty.

    Asserting on HTML was not enough to catch the production regression, so this
    reads the text back out of the generated PDF bytes and checks the skills sit
    under the SKILLS heading rather than only inside experience bullets.
    """
    pypdf = pytest.importorskip("pypdf")

    user_db = workspace_env["user_db"]
    _write_profile(workspace_env["user_id"])
    job_id = _seed_job(
        user_db,
        title="Backend Engineer",
        company="Acme",
        description="Python, PostgreSQL, AWS.",
    )
    _patch_engine(monkeypatch, _inflated_salesforce_response())

    cv = cv_service.upload_cv("c.pdf", b"pdf-bytes", db_path=workspace_env["db_path"])
    real_get_cv = db.get_cv
    monkeypatch.setattr(
        api_server.db,
        "get_cv",
        lambda cv_id, **kw: real_get_cv(cv_id, db_path=workspace_env["db_path"]),
    )

    from conftest import authed_client

    with authed_client() as client:
        assert (
            client.post(
                f"/jobs/{job_id}/tailor-cv",
                params={"source_cv_id": cv["id"]},
                json={"force": True},
            ).status_code
            == 200
        )
        pdf_res = client.get(
            f"/cvs/{db.WORKSPACE_CV_ID}/jobs/{job_id}/tailored-cv/download-pdf"
        )

    if pdf_res.status_code == 503:  # Chromium not installed in this environment
        pytest.skip("Playwright Chromium unavailable")
    assert pdf_res.status_code == 200, pdf_res.text
    assert pdf_res.content.startswith(b"%PDF")

    reader = pypdf.PdfReader(io.BytesIO(pdf_res.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    flat = re.sub(r"\s+", " ", text)

    heading = re.search(r"skills", flat, re.IGNORECASE)
    assert heading is not None, f"no Skills heading in the PDF text: {flat[:400]}"
    skills_region = flat[heading.end() :]
    assert skills_region.strip(), "the Skills section of the PDF is empty"
    for skill in ("Python", "SQL", "PostgreSQL", "Redis", "CI/CD"):
        assert skill in skills_region, (
            f"{skill} is missing from the PDF Skills section: {skills_region[:400]}"
        )
    assert "Salesforce Apex" not in flat


def test_reopening_a_tailored_job_realigns_a_rescanned_card_score(
    workspace_env, monkeypatch: pytest.MonkeyPatch
):
    """A rescan re-estimates the card; reopening the draft restores agreement."""
    user_db = workspace_env["user_db"]
    _write_profile(workspace_env["user_id"])
    job_id = _seed_job(
        user_db, title="Backend Engineer", company="Acme", description="Python."
    )
    calls = _patch_engine(monkeypatch, _inflated_salesforce_response())

    job = db.get_job_by_id(job_id, db_path=user_db)
    first = tailor_cv_service.tailor_cv_for_job(
        db.WORKSPACE_CV_ID,
        job,
        force=True,
        use_cache=False,
        user_id=workspace_env["user_id"],
        db_path=user_db,
    )
    honest = first["score_after"]

    db.upsert_cv_job_match(
        db.WORKSPACE_CV_ID,
        job_id,
        {"match_score": 81, "match_reason": "rescan"},
        db_path=user_db,
    )
    assert db.get_cv_job_match(db.WORKSPACE_CV_ID, job_id, db_path=user_db)[
        "match_score"
    ] == 81

    replayed = tailor_cv_service.tailor_cv_for_job(
        db.WORKSPACE_CV_ID,
        job,
        user_id=workspace_env["user_id"],
        db_path=user_db,
    )
    assert calls["count"] == 1
    assert replayed["score_after"] == honest
    match = db.get_cv_job_match(db.WORKSPACE_CV_ID, job_id, db_path=user_db)
    assert match["match_score"] == honest


def test_stale_drafts_from_the_old_pipeline_are_regenerated(
    workspace_env, monkeypatch: pytest.MonkeyPatch
):
    """A draft saved before this pipeline must not be replayed with its old score."""
    user_db = workspace_env["user_db"]
    _write_profile(workspace_env["user_id"])
    job_id = _seed_job(
        user_db,
        title="Backend Engineer",
        company="Acme",
        description="Python, PostgreSQL.",
    )

    legacy = (
        "## פירוט שינויים\n- Old pipeline draft\n\n"
        "## ציון התאמה למשרה\n**ציון משוער: 91/100**\n\n---\n\n"
        "## קורות החיים המעודכנים\n\n# Gal Lifshitz\n\n## Skills\nPython\n"
    )
    path = tailor_cv_service.tailored_cv_path(db.WORKSPACE_CV_ID, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(legacy, encoding="utf-8")

    assert tailor_cv_service.saved_draft_is_current(db.WORKSPACE_CV_ID, job_id) is False
    calls = _patch_engine(monkeypatch, _inflated_salesforce_response())

    job = db.get_job_by_id(job_id, db_path=user_db)
    result = tailor_cv_service.tailor_cv_for_job(
        db.WORKSPACE_CV_ID,
        job,
        user_id=workspace_env["user_id"],
        db_path=user_db,
    )

    assert calls["count"] == 1, "an unmarked legacy draft must be regenerated"
    assert result["score_after"] <= 55
    assert "91" not in result["markdown"]
    assert tailor_cv_service.saved_draft_is_current(db.WORKSPACE_CV_ID, job_id) is True

    # A draft written by the current pipeline is served from disk without a call.
    again = tailor_cv_service.tailor_cv_for_job(
        db.WORKSPACE_CV_ID,
        job,
        user_id=workspace_env["user_id"],
        db_path=user_db,
    )
    assert calls["count"] == 1
    assert again["from_cache"] is True
    assert again["score_after"] == result["score_after"]
    # The on-disk marker never leaks into what the user sees.
    assert "tailor-pipeline" not in again["markdown"]
