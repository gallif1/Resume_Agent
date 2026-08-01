"""Preview/reopen must load saved drafts without export gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from tailor_cv_service import (
    assert_safe_to_export,
    load_saved_tailored_result,
    prepare_for_preview,
    save_tailored_cv,
    TailorCvError,
)


def test_load_saved_tailored_result_returns_preview_fields(tmp_path, monkeypatch):
    cv_id = "cv-preview-test"
    job_id = 42

    # Point tailored CV storage at a temp directory.
    monkeypatch.setattr(
        "tailor_cv_service.cv_data_dir",
        lambda _cv_id: tmp_path / "cvdata",
    )

    markdown = (
        "## שינויים\n- Highlighted React\n\n---\n\n"
        "# Jane Doe\n\n## Summary\nBuilt React apps.\n"
    )
    save_tailored_cv(cv_id, job_id, markdown)

    result = load_saved_tailored_result(cv_id, job_id, db_path=None)
    assert result is not None
    assert result["from_cache"] is True
    assert result["preview_allowed"] is True
    assert result["download_blocked"] is False
    assert "Jane Doe" in (result.get("cv_markdown") or result["markdown"])


def test_preview_allows_when_export_blocked():
    report = {
        "quality_gates": {
            "passed": False,
            "failures": ["cross_entry_tech:App:firebase"],
        },
        "claim_validator_passed": True,
        "generation_report": {"resume_revisions": 1},
    }
    preview = prepare_for_preview(report)
    assert preview["preview_allowed"] is True
    assert preview["download_blocked"] is True
    with pytest.raises(TailorCvError):
        assert_safe_to_export(report)
