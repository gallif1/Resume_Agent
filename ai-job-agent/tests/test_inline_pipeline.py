"""Tests for the inline collect -> enrich -> match pipeline.

Covers the orchestration introduced so each freshly-collected job is
enriched + scored immediately (and streamed to the UI) instead of waiting
for the whole collection batch to finish first:

- ``match_jobs.build_match_context`` / ``match_jobs.score_one_job``
- the ``on_job_saved`` hook in ``collect_jobs.save_jobs_to_db``
- the per-source dispatcher ``enrich_jobs.enrich_job_inline``

DB-touching internals of ``score_one_job`` are mocked rather than exercised
against a real sqlite file: ``match_jobs.py`` intentionally has no
``db_path`` plumbing (it always targets the process-wide ``config.DB_PATH``,
matching production's one-subprocess-per-scan model), so in a shared test
process those calls would otherwise hit the real on-disk ``data/jobs.db``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import match_jobs
from collect_jobs import save_jobs_to_db
from enrich_jobs import ENRICH_SUCCESS, enrich_job_inline
from job_identity import normalize_job_url
from match_jobs import MatchContext, score_one_job


def _context(**overrides) -> MatchContext:
    defaults = dict(
        cv_id=None,
        scan_id=None,
        profile={"min_match_score": 50},
        cv_profile={},
        strategy={},
        strategy_hash="hash-1",
        universal={},
        candidate=object(),
        min_score=50,
    )
    defaults.update(overrides)
    return MatchContext(**defaults)


def _job(**overrides) -> dict:
    job = {
        "id": 1,
        "title": "Python Dev",
        "company": "Acme",
        "description": "Python backend developer role with APIs, databases, and cloud deployment.",
    }
    job.update(overrides)
    return job


# --- score_one_job -----------------------------------------------------------


def test_score_one_job_returns_none_for_jobless_input():
    assert score_one_job({}, _context()) is None
    assert score_one_job({"title": "no id"}, _context()) is None


def test_score_one_job_skips_global_mode_when_already_matched(monkeypatch):
    ctx = _context(cv_id=None, scan_id=42)
    monkeypatch.setattr(match_jobs, "job_needs_matching", lambda *a, **k: False)
    ensure_profile = MagicMock()
    monkeypatch.setattr(match_jobs, "_ensure_job_profile", ensure_profile)
    refresh = MagicMock()
    monkeypatch.setattr(match_jobs, "refresh_cv_job_match_scan", refresh)

    result = score_one_job(_job(), ctx)

    assert result == {"action": "skipped"}
    ensure_profile.assert_not_called()
    # Global (no-cv) legacy mode never touches the per-scan refresh.
    refresh.assert_not_called()


def test_score_one_job_skips_with_cv_refreshes_scan_visibility(monkeypatch):
    ctx = _context(cv_id="cv-1", scan_id=42)
    monkeypatch.setattr(match_jobs, "cv_job_needs_matching", lambda *a, **k: False)
    refresh = MagicMock()
    monkeypatch.setattr(match_jobs, "refresh_cv_job_match_scan", refresh)

    result = score_one_job(_job(id=7), ctx)

    assert result == {"action": "skipped"}
    refresh.assert_called_once_with("cv-1", 7, 42)


def test_score_one_job_scores_analyzes_stores_and_streams(monkeypatch):
    ctx = _context(cv_id="cv-1", scan_id=42, min_score=10)
    monkeypatch.setattr(match_jobs, "cv_job_needs_matching", lambda *a, **k: True)
    monkeypatch.setattr(match_jobs, "job_needs_analysis", lambda *a, **k: True)
    monkeypatch.setattr(match_jobs, "_ensure_job_profile", lambda job, **k: object())

    fake_legacy = MagicMock(match_score=40)
    monkeypatch.setattr(match_jobs, "classify_job_with_strategy", lambda *a, **k: fake_legacy)

    pm_result = MagicMock(
        score=80,
        score_label="Good Match",
        exclusion_hit=False,
        matched_skills=["Python"],
        missing_skills=[],
        score_reasons=["good profile fit"],
    )
    monkeypatch.setattr(match_jobs, "profile_score", lambda *a, **k: pm_result)

    ats_result = MagicMock(
        ats_score=70,
        score_label="Good Match",
        hard_constraint_failed=False,
        mandatory_failed=False,
        is_potential_junior_match=False,
        domain_mismatch=False,
        matched_required_skills=["Python"],
        missing_required_skills=[],
        score_reasons=["ats ok"],
    )
    ats_result.to_db_fields.return_value = {}
    monkeypatch.setattr(match_jobs, "ats_score", lambda *a, **k: ats_result)

    store_mock = MagicMock(return_value=99)
    monkeypatch.setattr(match_jobs, "_store_match_result", store_mock)
    emit_mock = MagicMock()
    monkeypatch.setattr(match_jobs, "_emit_scored_job", emit_mock)

    job = _job(id=7)
    result = score_one_job(job, ctx)

    assert result["action"] == "scored"
    assert result["analyzed"] is True
    assert result["match_id"] == 99
    assert result["matched"] is True  # final_score should clear min_score=10

    store_mock.assert_called_once()
    stored_job_id, stored_fields = store_mock.call_args.args[:2]
    assert stored_job_id == 7
    assert stored_fields["match_score"] == result["final_score"]
    assert store_mock.call_args.kwargs == {"cv_id": "cv-1", "scan_id": 42}

    emit_mock.assert_called_once_with(job, stored_fields, match_id=99, scan_id=42)


def test_score_one_job_rematch_forces_rescore_even_if_matched(monkeypatch):
    ctx = _context(cv_id=None, scan_id=None)
    needs_matching = MagicMock(return_value=True)
    monkeypatch.setattr(match_jobs, "job_needs_matching", needs_matching)
    monkeypatch.setattr(match_jobs, "job_needs_analysis", lambda *a, **k: False)
    monkeypatch.setattr(match_jobs, "_ensure_job_profile", lambda job, **k: object())
    monkeypatch.setattr(
        match_jobs, "classify_job_with_strategy", lambda *a, **k: MagicMock(match_score=10)
    )
    pm_result = MagicMock(score=20, score_label="Weak Match", exclusion_hit=False,
                           matched_skills=[], missing_skills=[], score_reasons=[])
    ats_result = MagicMock(ats_score=20, score_label="Weak Match",
                            hard_constraint_failed=False, mandatory_failed=False,
                            is_potential_junior_match=False, domain_mismatch=False,
                            matched_required_skills=[], missing_required_skills=[],
                            score_reasons=[])
    ats_result.to_db_fields.return_value = {}
    monkeypatch.setattr(match_jobs, "profile_score", lambda *a, **k: pm_result)
    monkeypatch.setattr(match_jobs, "ats_score", lambda *a, **k: ats_result)
    monkeypatch.setattr(match_jobs, "_store_match_result", lambda *a, **k: None)
    monkeypatch.setattr(match_jobs, "_emit_scored_job", lambda *a, **k: None)

    score_one_job(_job(is_matched=1), ctx, rematch=True)

    _, kwargs = needs_matching.call_args
    assert kwargs["rematch"] is True


# --- build_match_context ------------------------------------------------------


def test_build_match_context_resolves_workspace_scoped_cv_id(monkeypatch):
    monkeypatch.setattr(match_jobs, "AGENT_USER_ID", "user-42")
    monkeypatch.setattr(match_jobs, "AGENT_CV_ID", None)
    monkeypatch.setattr(match_jobs, "workspace_scope_id", lambda uid: f"workspace:{uid}")
    monkeypatch.setattr(match_jobs, "load_profile", lambda: {"min_match_score": 33})
    monkeypatch.setattr(match_jobs, "load_cv_profile", lambda: {"skills": {}})
    monkeypatch.setattr(match_jobs, "load_matching_strategy", lambda: {"job_categories": []})
    monkeypatch.setattr(match_jobs, "compute_candidate_strategy_hash", lambda *a, **k: "hash-x")
    monkeypatch.setattr(match_jobs, "load_pipeline_state", lambda: {})
    monkeypatch.setattr(match_jobs, "mark_all_jobs_for_rematch", lambda: 0)
    monkeypatch.setattr(match_jobs, "get_universal_profile", lambda *a, **k: {"canonical_skills": []})
    monkeypatch.setattr(match_jobs, "register_profile_terms", lambda *a, **k: None)
    monkeypatch.setattr(match_jobs, "build_ats_candidate", lambda *a, **k: "candidate-obj")

    ctx = match_jobs.build_match_context()

    assert ctx.cv_id == "workspace:user-42"
    assert ctx.min_score == 33
    assert ctx.candidate == "candidate-obj"
    assert ctx.strategy_hash == "hash-x"


def test_build_match_context_respects_explicit_cv_and_scan_id(monkeypatch):
    monkeypatch.setattr(match_jobs, "load_profile", lambda: {"min_match_score": 0})
    monkeypatch.setattr(match_jobs, "load_cv_profile", lambda: {})
    monkeypatch.setattr(match_jobs, "load_matching_strategy", lambda: {"job_categories": []})
    monkeypatch.setattr(match_jobs, "compute_candidate_strategy_hash", lambda *a, **k: "hash-y")
    monkeypatch.setattr(match_jobs, "load_pipeline_state", lambda: {})
    monkeypatch.setattr(match_jobs, "get_universal_profile", lambda *a, **k: {})
    monkeypatch.setattr(match_jobs, "register_profile_terms", lambda *a, **k: None)
    monkeypatch.setattr(match_jobs, "build_ats_candidate", lambda *a, **k: "candidate-obj")

    ctx = match_jobs.build_match_context(cv_id="explicit-cv", scan_id=7)

    assert ctx.cv_id == "explicit-cv"
    assert ctx.scan_id == 7


# --- collect_jobs.save_jobs_to_db(on_job_saved=...) ---------------------------


def test_save_jobs_to_db_invokes_on_job_saved_only_for_new_jobs(monkeypatch):
    def fake_upsert(**kwargs):
        if "existing" in kwargs["job_url"]:
            return 1, False  # touched an already-known job — not new
        return 100, True

    monkeypatch.setattr("collect_jobs.upsert_collected_job", fake_upsert)

    scraped = [
        {
            "title": "Brand New",
            "job_url": "https://www.linkedin.com/jobs/view/999",
            "company": "Beta",
            "location": "TLV",
            "source": "linkedin",
        },
        {
            "title": "Already There",
            "job_url": "https://www.linkedin.com/jobs/view/existing-1",
            "company": "Acme",
            "location": "TLV",
            "source": "linkedin",
        },
    ]

    saved_ids: list[int] = []
    save_jobs_to_db(
        scraped,
        source_query="Fullstack",
        source_category="fullstack",
        source_strategy_hash=None,
        seen_job_keys=set(),
        known_db_keys=set(),
        touched_job_keys=set(),
        known_job_urls=set(),
        stop_on_known=True,
        on_job_saved=saved_ids.append,
    )

    assert saved_ids == [100]


def test_save_jobs_to_db_on_job_saved_failure_does_not_abort_collection(monkeypatch, capsys):
    monkeypatch.setattr(
        "collect_jobs.upsert_collected_job",
        lambda **kwargs: (100, True),
    )

    def boom(job_id: int) -> None:
        raise RuntimeError("inline enrich/match blew up")

    result = save_jobs_to_db(
        [
            {
                "title": "Brand New",
                "job_url": normalize_job_url("https://www.linkedin.com/jobs/view/999"),
                "company": "Beta",
                "location": "TLV",
                "source": "linkedin",
            }
        ],
        source_query="Fullstack",
        source_category="fullstack",
        source_strategy_hash=None,
        seen_job_keys=set(),
        known_db_keys=set(),
        touched_job_keys=set(),
        known_job_urls=set(),
        stop_on_known=True,
        on_job_saved=boom,
    )

    inserted = result[5]
    assert inserted == 1  # collection bookkeeping still succeeds
    assert "inline enrich/match blew up" in capsys.readouterr().out


# --- enrich_jobs.enrich_job_inline --------------------------------------------


def test_enrich_job_inline_dispatches_by_source(monkeypatch):
    import enrich_jobs

    monkeypatch.setattr(enrich_jobs, "enrich_linkedin_one", lambda job: ("li", "li-desc", None))
    monkeypatch.setattr(enrich_jobs, "enrich_gotfriends_one", lambda job: ("gf", "gf-desc", None))
    monkeypatch.setattr(
        enrich_jobs, "enrich_one", lambda page, job, **k: ("drushim-ok", "drushim-desc", None)
    )

    assert enrich_job_inline({"source": "linkedin"}) == ("li", "li-desc", None)
    assert enrich_job_inline({"source": "gotfriends"}) == ("gf", "gf-desc", None)
    assert enrich_job_inline({"source": "drushim"}, drushim_page=object()) == (
        "drushim-ok",
        "drushim-desc",
        None,
    )


def test_enrich_job_inline_skips_drushim_without_a_page():
    assert enrich_job_inline({"source": "drushim"}, drushim_page=None) == (None, None, None)


def test_enrich_job_inline_passes_through_sources_without_per_job_enrichment():
    job = {"source": "alljobs", "description": "short listing snippet"}
    assert enrich_job_inline(job) == (ENRICH_SUCCESS, "short listing snippet", None)


# --- process_job_inline gate -------------------------------------------------


def test_process_job_inline_defers_when_enrich_cannot_run(monkeypatch):
    """Drushim without a page must not score/stream — wait for batch enrich."""
    from collect_jobs import process_job_inline

    monkeypatch.setattr(
        "collect_jobs.get_job_by_id",
        lambda job_id: {
            "id": job_id,
            "source": "drushim",
            "title": "Dev",
            "company": "Acme",
            "description": "short listing",
        },
    )
    enrich_mock = MagicMock(return_value=(None, None, None))
    score_mock = MagicMock()
    record_mock = MagicMock()
    monkeypatch.setattr("collect_jobs.enrich_job_inline", enrich_mock)
    monkeypatch.setattr("collect_jobs.score_one_job", score_mock)
    monkeypatch.setattr("collect_jobs.record_enrichment_attempt", record_mock)

    assert process_job_inline(42, _context()) == "deferred"
    enrich_mock.assert_called_once()
    score_mock.assert_not_called()
    record_mock.assert_not_called()


def test_process_job_inline_enriches_then_scores_linkedin(monkeypatch):
    from collect_jobs import process_job_inline

    row = {
        "id": 7,
        "source": "linkedin",
        "title": "Backend",
        "company": "Co",
        "description": "Python APIs and cloud deployment experience required here.",
    }
    monkeypatch.setattr("collect_jobs.get_job_by_id", lambda job_id: dict(row))
    monkeypatch.setattr(
        "collect_jobs.enrich_job_inline",
        lambda job, **k: (ENRICH_SUCCESS, "full linkedin description " * 3, None),
    )
    record_mock = MagicMock()
    monkeypatch.setattr("collect_jobs.record_enrichment_attempt", record_mock)
    score_mock = MagicMock(return_value={"action": "scored", "matched": True})
    monkeypatch.setattr("collect_jobs.score_one_job", score_mock)

    assert process_job_inline(7, _context()) == "scored"
    record_mock.assert_called_once()
    score_mock.assert_called_once()
    scored_row = score_mock.call_args.args[0]
    assert scored_row["enrich_status"] == ENRICH_SUCCESS
    assert "full linkedin" in (scored_row.get("full_description") or "")


def test_process_job_inline_scores_sources_without_board_enrichment(monkeypatch):
    from collect_jobs import process_job_inline

    monkeypatch.setattr(
        "collect_jobs.get_job_by_id",
        lambda job_id: {
            "id": job_id,
            "source": "alljobs",
            "title": "Dev",
            "description": "Enough listing text from collection already present.",
        },
    )
    enrich_mock = MagicMock()
    monkeypatch.setattr("collect_jobs.enrich_job_inline", enrich_mock)
    score_mock = MagicMock(return_value={"action": "scored"})
    monkeypatch.setattr("collect_jobs.score_one_job", score_mock)

    assert process_job_inline(3, _context()) == "scored"
    enrich_mock.assert_not_called()
    score_mock.assert_called_once()
