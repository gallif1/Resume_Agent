"""Tests for job posting date normalization and Hebrew display injection."""

from __future__ import annotations

from datetime import date

from date_utils import (
    format_posted_date_he,
    inject_posted_date_header,
    normalize_posted_date,
)


REF = date(2026, 7, 18)


def test_normalize_hebrew_relatives():
    assert normalize_posted_date("היום", reference=REF) == "2026-07-18"
    assert normalize_posted_date("אתמול", reference=REF) == "2026-07-17"
    assert normalize_posted_date("לפני יומיים", reference=REF) == "2026-07-16"
    assert normalize_posted_date("לפני 3 ימים", reference=REF) == "2026-07-15"
    assert normalize_posted_date("לפני 2 שבועות", reference=REF) == "2026-07-04"
    assert normalize_posted_date("לפני חודשיים", reference=REF) == "2026-05-19"
    assert normalize_posted_date("לפני שנה", reference=REF) == "2025-07-18"
    assert normalize_posted_date("לפני שנתיים", reference=REF) == "2024-07-18"


def test_normalize_numeric_and_iso():
    assert normalize_posted_date("12-05", reference=REF) == "2026-05-12"
    assert normalize_posted_date("2026-05-12T09:00:00Z") == "2026-05-12"
    assert normalize_posted_date("08/07/2026") == "2026-07-08"


def test_normalize_defaults_to_today_when_missing():
    assert normalize_posted_date(None, reference=REF) == "2026-07-18"
    assert normalize_posted_date("", reference=REF) == "2026-07-18"
    assert normalize_posted_date("not-a-date", default_to_today=False, reference=REF) is None


def test_is_posted_older_than_and_stale_markers():
    from date_utils import filter_jobs_by_max_age, is_posted_older_than, looks_like_stale_posted_text

    assert looks_like_stale_posted_text("לפני חודשיים")
    assert looks_like_stale_posted_text("פורסם לפני שנה")
    assert is_posted_older_than("לפני חודשיים", reference=REF)
    assert is_posted_older_than("2026-05-01", reference=REF)
    assert not is_posted_older_than("לפני 3 ימים", reference=REF)
    assert not is_posted_older_than(None, reference=REF)

    kept, skipped, all_old = filter_jobs_by_max_age(
        [
            {"title": "a", "posted_date": "לפני חודשיים"},
            {"title": "b", "posted_date": "לפני שנה"},
        ],
        reference=REF,
    )
    assert kept == []
    assert skipped == 2
    assert all_old is True

    kept2, skipped2, all_old2 = filter_jobs_by_max_age(
        [
            {"title": "a", "posted_date": "לפני חודשיים"},
            {"title": "b", "posted_date": "היום"},
        ],
        reference=REF,
    )
    assert len(kept2) == 1
    assert skipped2 == 1
    assert all_old2 is False


def test_hebrew_header_and_injection():
    header = format_posted_date_he("2026-07-08")
    assert header == "📅 תאריך פרסום: 08/07/2026"
    text = inject_posted_date_header("תיאור משרה\nשורה שנייה", "2026-07-08")
    assert text.startswith("📅 תאריך פרסום: 08/07/2026")
    assert "תיאור משרה" in text
    # Idempotent — do not stack headers.
    again = inject_posted_date_header(text, "2026-07-08")
    assert again.count("תאריך פרסום") == 1
