"""Tests for interview-first philosophy + live generation progress."""

from __future__ import annotations

from intelligent_tailoring.interview_philosophy import (
    build_generation_report,
    bullet_interview_score,
    select_top_interview_reasons,
)
from intelligent_tailoring.progress import ProgressReporter
from intelligent_tailoring.services.decision_log import build_decision_log
from intelligent_tailoring.services.evidence_amplifier import build_highlight_plan
import tailor_stream


def test_top_interview_reasons_prefer_hard_matches():
    evidence = [
        {
            "requirement": "PostgreSQL",
            "candidate_status": "MATCH",
            "importance": "hard",
            "evidence_strength": "Explicit Evidence",
        },
        {
            "requirement": "FastAPI",
            "candidate_status": "MATCH",
            "importance": "hard",
            "evidence_strength": "Explicit Evidence",
        },
        {
            "requirement": "Nice to have GraphQL",
            "candidate_status": "PARTIAL",
            "importance": "soft",
            "evidence_strength": "Weak Inference",
        },
    ]
    reasons = select_top_interview_reasons(
        highlight_plan={"must_highlight": ["PostgreSQL", "FastAPI"]},
        evidence_map=evidence,
        strategy={"skills_to_emphasize": ["Docker"]},
        limit=3,
    )
    assert reasons[0] == "PostgreSQL"
    assert "FastAPI" in reasons
    assert len(reasons) <= 3


def test_highlight_plan_includes_top_interview_reasons():
    evidence = [
        {
            "requirement": "Patient care documentation",
            "candidate_status": "MATCH",
            "importance": "hard",
            "evidence_strength": "Explicit Evidence",
            "inference_category": "Explicit",
        },
        {
            "requirement": "EHR systems",
            "candidate_status": "MATCH",
            "importance": "hard",
            "evidence_strength": "Explicit Evidence",
            "inference_category": "Explicit",
        },
        {
            "requirement": "Kubernetes",
            "candidate_status": "MISSING",
            "importance": "hard",
            "evidence_strength": "No Evidence",
        },
    ]
    plan = build_highlight_plan(
        evidence_map=evidence,
        skills_to_emphasize=["Patient care documentation", "EHR systems"],
    )
    assert plan["top_interview_reasons"]
    assert "Kubernetes" in plan["unsupported_hard"]
    assert plan["propagate_terms"][0] in plan["top_interview_reasons"]


def test_bullet_interview_score_prefers_value_over_duties():
    emphasize = ["PostgreSQL", "FastAPI"]
    strong = bullet_interview_score(
        "Designed FastAPI services backed by PostgreSQL for order tracking.",
        emphasize,
    )
    weak = bullet_interview_score("Responsible for various duties.", emphasize)
    assert strong > weak


def test_decision_log_builds_trust_messages():
    log = build_decision_log(
        strategy={
            "top_interview_reasons": ["PostgreSQL", "FastAPI"],
            "facts_to_omit": ["Unrelated retail hobby project"],
            "project_priority": ["Order Service"],
            "highlight_plan": {"unsupported_hard": ["Kubernetes"]},
        },
        evidence_map=[],
        one_page={"compressed": True},
        writing_report={"hm_refine_pass": True},
    )
    texts = " ".join(i["text"] for i in log)
    assert "PostgreSQL" in texts
    assert "Kubernetes" in texts
    assert "one-page" in texts.lower() or "one page" in texts.lower() or "Reduced" in texts


def test_generation_report_shape():
    report = build_generation_report(
        result={
            "evidence_map": [{"candidate_status": "MATCH"}, {"candidate_status": "MISSING"}],
            "inferred_competencies": [{"statement": "x"}],
            "change_log": [{"section": "summary"}, {"section": "experience"}],
            "writing_report": {"review_cycles": 2},
            "recruiter_review": {"would_interview": True},
            "hiring_manager_feedback": {"overall_fit": 80},
            "tailoring_strategy": {"top_interview_reasons": ["Python"]},
            "pipeline_version": "multi_agent_v1_3",
        },
        elapsed_seconds=12.3,
    )
    assert report["job_requirements_analyzed"] == 2
    assert report["candidate_strengths_identified"] == 1
    assert report["ats_optimization_completed"] is True
    assert report["generation_time_seconds"] == 12.3
    assert "Improved Summary" in report["sections_changed"]


def test_progress_reporter_emits_stages_and_decisions():
    events: list[dict] = []
    reporter = ProgressReporter(events.append)
    reporter.started("job_intelligence", "Analyzing…")
    reporter.decision(
        "job_intelligence",
        {"action": "emphasize", "text": "Highlighting Python", "target": "Python"},
    )
    reporter.completed("job_intelligence", "Done")
    kinds = [e.get("event") for e in events]
    assert "stage" in kinds
    assert "decision" in kinds
    assert events[0]["status"] == "started"
    assert events[-1]["status"] == "completed"


def test_tailor_stream_run_lifecycle():
    run_id = tailor_stream.begin_run(user_id="u1", cv_id="cv1", job_id=9)
    cb = tailor_stream.make_progress_callback(
        user_id="u1", cv_id="cv1", job_id=9, run_id=run_id
    )
    cb(
        {
            "event": "stage",
            "stage": "resume_strategy",
            "status": "started",
            "message": "Selecting strongest evidence…",
            "index": 4,
            "total": 11,
        }
    )
    cb(
        {
            "event": "decision",
            "stage": "resume_strategy",
            "decision": {
                "action": "emphasize",
                "text": "Highlighting PostgreSQL because of strong demand",
            },
            "message": "Highlighting PostgreSQL because of strong demand",
        }
    )
    snap = tailor_stream.get_run(run_id)
    assert snap is not None
    assert snap["status"] == "running"
    assert snap["current_stage"] == "resume_strategy"
    assert snap["decisions"]
    tailor_stream.finish_run(
        user_id="u1",
        run_id=run_id,
        report={"status": "success", "resume_revisions": 3},
    )
    done = tailor_stream.get_run(run_id)
    assert done["status"] == "completed"
    assert done["generation_report"]["resume_revisions"] == 3
