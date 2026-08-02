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


def test_finalize_site_warnings_skips_full_linkedin_catch_up():
    """Incremental catch-up must not spam 'problems collecting jobs' warnings."""
    totals = {
        "linkedin": _SiteTotals(
            raw=0,
            queries=9,
            caught_up_queries=9,
        )
    }
    assert _finalize_site_warnings(totals) == []


def test_finalize_site_warnings_keeps_real_linkedin_failures():
    totals = {
        "linkedin": _SiteTotals(
            raw=0,
            queries=2,
            caught_up_queries=1,
            issues=["לינקדאין חסם/הגביל בקשות (429)"],
        )
    }
    warnings = _finalize_site_warnings(totals)
    assert len(warnings) == 1
    assert "429" in warnings[0]
