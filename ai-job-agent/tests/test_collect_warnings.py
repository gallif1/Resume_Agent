"""Tests for collection warning aggregation."""

from __future__ import annotations

from collect_jobs import _SiteTotals, _finalize_site_warnings


def test_finalize_site_warnings_when_all_jobs_already_in_db():
    totals = {
        "drushim": _SiteTotals(
            raw=10,
            new=0,
            already_in_db=10,
            queries=2,
            queries_with_raw=2,
        )
    }
    warnings = _finalize_site_warnings(totals)
    assert len(warnings) == 1
    assert "כבר קיימות במסד הנתונים" in warnings[0]


def test_finalize_site_warnings_when_no_jobs_found():
    totals = {
        "drushim": _SiteTotals(
            raw=0,
            queries=3,
            issues=["דרושים חסם את הגישה"],
        )
    }
    warnings = _finalize_site_warnings(totals)
    assert warnings[0].startswith("דרושים:")
    assert "לא נמצאו משרות" in warnings[0]
