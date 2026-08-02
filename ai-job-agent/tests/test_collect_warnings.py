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


def test_finalize_site_warnings_consolidates_empty_query_spam():
    """Many per-query empty notes must collapse to one site-level warning."""
    empties = [
        f"לינקדאין: לא נמצאו משרות לחיפוש '{title}'"
        for title in (
            "Backend Developer",
            "Full Stack Developer",
            "Capstone Project Lead",
            "Python Programming Tutor",
            "Frontend Developer",
            "DevOps Engineer",
            "Project Manager",
            "Product Manager",
            "Web Developer",
        )
    ]
    totals = {
        "linkedin": _SiteTotals(
            raw=0,
            queries=len(empties),
            issues=empties,
        )
    }
    warnings = _finalize_site_warnings(totals)
    assert len(warnings) == 1
    assert "לא נמצאו משרות בכל 9 החיפושים" in warnings[0]
    assert "Capstone Project Lead" not in warnings[0]


def test_finalize_site_warnings_prefers_hard_issue_over_empty_spam():
    totals = {
        "linkedin": _SiteTotals(
            raw=0,
            queries=4,
            issues=[
                "לינקדאין: לא נמצאו משרות לחיפוש 'Backend Developer'",
                "לינקדאין: לא נמצאו משרות לחיפוש 'Frontend Developer'",
                "לינקדאין החזיר תוצאות ריקות ברצף — מפסיקים חיפושים נוספים (ייתכן חסימה זמנית)",
            ],
        )
    }
    warnings = _finalize_site_warnings(totals)
    assert len(warnings) == 1
    assert "חסימה" in warnings[0]
    assert "Backend Developer" not in "".join(warnings)


def test_finalize_site_warnings_partial_empties_consolidated_when_raw_positive():
    totals = {
        "linkedin": _SiteTotals(
            raw=5,
            new=5,
            queries=4,
            queries_with_raw=1,
            issues=[
                "לינקדאין: לא נמצאו משרות לחיפוש 'Capstone Project Lead'",
                "לינקדאין: לא נמצאו משרות לחיפוש 'Python Programming Tutor'",
            ],
        )
    }
    warnings = _finalize_site_warnings(totals)
    assert len(warnings) == 1
    assert "2 חיפושים לא החזירו משרות" in warnings[0]
