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


def test_normalize_numeric_and_iso():
    assert normalize_posted_date("12-05", reference=REF) == "2026-05-12"
    assert normalize_posted_date("2026-05-12T09:00:00Z") == "2026-05-12"
    assert normalize_posted_date("08/07/2026") == "2026-07-08"


def test_normalize_defaults_to_today_when_missing():
    assert normalize_posted_date(None, reference=REF) == "2026-07-18"
    assert normalize_posted_date("", reference=REF) == "2026-07-18"
    assert normalize_posted_date("not-a-date", default_to_today=False, reference=REF) is None


def test_hebrew_header_and_injection():
    header = format_posted_date_he("2026-07-08")
    assert header == "📅 תאריך פרסום: 08/07/2026"
    text = inject_posted_date_header("תיאור משרה\nשורה שנייה", "2026-07-08")
    assert text.startswith("📅 תאריך פרסום: 08/07/2026")
    assert "תיאור משרה" in text
    # Idempotent — do not stack headers.
    again = inject_posted_date_header(text, "2026-07-08")
    assert again.count("תאריך פרסום") == 1
