"""Shared fixtures/helpers for Intelligent Resume Tailoring tests."""

from __future__ import annotations

from typing import Any


def intelligent_report(
    *,
    score: int = 62,
    original_score: int | None = None,
    skills: list[str] | None = None,
    summary: str = "Support engineer moving into backend work.",
    hard_statuses: tuple[str, ...] = ("MATCH", "PARTIAL"),
    experience: list[dict[str, Any]] | None = None,
    change_log: list[dict[str, Any]] | None = None,
    inferred: list[dict[str, Any]] | None = None,
    validation_warnings: list[dict[str, Any]] | None = None,
    missing: list[str] | None = None,
    claim_validator_passed: bool = True,
) -> dict[str, Any]:
    """Build a dual-schema report matching ``run_intelligent_tailoring`` output."""
    hard = [
        {
            "requirement": f"Requirement {index}",
            "candidate_status": status,
            "evidence_or_gap": "evidence",
        }
        for index, status in enumerate(hard_statuses, start=1)
    ]
    soft = [
        {
            "requirement": "Docker",
            "candidate_status": "MATCH",
            "evidence_or_gap": "Ran Docker containers",
        }
    ]
    tailored_resume = {
        "professional_title": "Technical Support Engineer",
        "professional_summary": summary,
        "summary": summary,
        "skills": skills if skills is not None else ["Python", "SQL", "Docker"],
        "experience": experience
        if experience is not None
        else [
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
        "certifications": [],
    }
    original = original_score if original_score is not None else score
    missing = missing if missing is not None else ["Kubernetes"]
    log = change_log if change_log is not None else [
        {
            "original_text": "Troubleshot Windows and SQL issues",
            "new_text": "Troubleshot production Windows and SQL incidents daily.",
            "reason": "Emphasized production support language from the JD",
            "supporting_evidence": "Troubleshot Windows and SQL issues",
            "related_job_requirement": "SQL",
            "inference_category": "Explicit",
            "confidence_score": 1.0,
        }
    ]
    inferred_competencies = inferred if inferred is not None else [
        {
            "statement": "Experience with technical troubleshooting and root-cause analysis",
            "supporting_evidence": "Troubleshot Windows and SQL issues",
            "reasoning": "Ontology rule support-troubleshooting",
            "confidence_score": 0.88,
            "related_requirement": "troubleshooting",
            "ontology_rule_id": "support-troubleshooting",
            "inference_category": "Strongly Inferred",
        }
    ]
    evidence_map = []
    for item in hard:
        evidence_map.append(
            {
                "requirement": item["requirement"],
                "importance": "hard",
                "candidate_status": item["candidate_status"],
                "inference_category": (
                    "Explicit" if item["candidate_status"] == "MATCH" else "Unsupported"
                ),
                "supporting_evidence": item["evidence_or_gap"],
                "generated_statement": "",
                "confidence_score": 1.0 if item["candidate_status"] == "MATCH" else 0.0,
            }
        )
    for item in soft:
        evidence_map.append(
            {
                "requirement": item["requirement"],
                "importance": "soft",
                "candidate_status": item["candidate_status"],
                "inference_category": "Explicit",
                "supporting_evidence": item["evidence_or_gap"],
                "generated_statement": item["requirement"],
                "confidence_score": 1.0,
            }
        )

    return {
        "tailored_resume": tailored_resume,
        "tailored_cv": {
            "professional_title": tailored_resume["professional_title"],
            "summary": summary,
            "skills": tailored_resume["skills"],
            "experience": tailored_resume["experience"],
            "projects": [],
            "education": [],
            "certifications": [],
        },
        "matched_requirements": ["Python", "SQL", "Docker"],
        "missing_requirements": missing,
        "inferred_competencies": inferred_competencies,
        "removed_or_deprioritized_content": [],
        "ats_keywords_added": ["SQL", "production support"],
        "change_log": log,
        "validation_warnings": validation_warnings or [],
        "original_match_score": original,
        "tailored_match_score": score,
        "language": "en",
        "evidence_map": evidence_map,
        "job_requirements": {
            "hard_requirements": [h["requirement"] for h in hard],
            "soft_requirements": [s["requirement"] for s in soft],
        },
        "jd_snapshot": "Python, SQL and Docker experience required.",
        "jd_snapshot_hash": "jdhash",
        "resume_hash": "resumehash",
        "from_cache": False,
        "pipeline_version": "intelligent_tailor_v1",
        "claim_validator_passed": claim_validator_passed,
        "rejected_statements": [],
        "requirement_extraction": {
            "hard_requirements": hard,
            "soft_requirements": soft,
        },
        "scoring": {
            "hard_score_pct": 75,
            "soft_score_pct": 100,
            "hard_cap_applied": False,
            "realistic_match_score": score,
            "score_rationale": "Solid Python and SQL overlap.",
        },
        "key_matching_points": ["Python automation", "SQL troubleshooting"],
        "missing_critical_skills": [
            {"skill": m, "reason": "No supporting evidence in original resume"}
            for m in missing
        ],
        "transferable_skills_framing": [
            {
                "gap": "Kubernetes",
                "how_to_honestly_frame_existing_experience": (
                    "Docker container work is the closest honest parallel."
                ),
            }
        ],
        "recommendation": "APPLY_WITH_HONEST_FRAMING",
        "score_validation": {
            "model_reported_score": score,
            "recomputed_composite_score": score,
            "score_overridden": False,
            "cap": None,
            "dropped_unsupported_skills": [],
            "claim_validator_passed": claim_validator_passed,
        },
        "realistic_match_score": score,
    }


def patch_intelligent_pipeline(monkeypatch: Any, report: dict[str, Any] | None = None):
    """Point tailor_cv_service at a stubbed intelligent pipeline result."""
    import tailor_cv_service as svc

    payload = report if report is not None else intelligent_report()

    def _run(**_kwargs: Any) -> dict[str, Any]:
        return dict(payload)

    monkeypatch.setattr(svc, "run_intelligent_tailoring", _run)
    return payload
