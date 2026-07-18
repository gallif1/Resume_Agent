"""Normalize scraped job posting dates into ISO YYYY-MM-DD and Hebrew display."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Hebrew relative / absolute fragments commonly shown on Israeli boards.
_HE_TODAY = re.compile(r"^(היום|today)$", re.IGNORECASE)
_HE_YESTERDAY = re.compile(r"^(אתמול|yesterday)$", re.IGNORECASE)
_HE_TWO_DAYS = re.compile(r"^(לפני\s+יומיים|יומיים)$")
_HE_DAYS = re.compile(
    r"^(?:לפני\s+)?(\d+)\s*(?:ימים|יום|days?|d)$",
    re.IGNORECASE,
)
_HE_HOURS = re.compile(
    r"^(?:לפני\s+)?(\d+)\s*(?:שעות|שעה|hours?|hrs?|h)$",
    re.IGNORECASE,
)
_HE_WEEKS = re.compile(
    r"^(?:לפני\s+)?(\d+)\s*(?:שבועות|שבוע|weeks?|w)$",
    re.IGNORECASE,
)
_HE_MONTHS = re.compile(
    r"^(?:לפני\s+)?(\d+)\s*(?:חודשים|חודש|months?|mo)$",
    re.IGNORECASE,
)
_HE_MINUTES = re.compile(
    r"^(?:לפני\s+)?(\d+)\s*(?:דקות|דקה|minutes?|mins?|m)$",
    re.IGNORECASE,
)

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DMY_SLASH = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})$")
_DMY_DASH = re.compile(r"^(\d{1,2})-(\d{1,2})(?:-(\d{2,4}))?$")
_MD_ONLY = re.compile(r"^(\d{1,2})-(\d{1,2})$")  # e.g. 12-05 → day-month (IL)

POSTED_DATE_HEADER_RE = re.compile(
    r"^📅\s*תאריך\s*פרסום\s*:\s*\d{1,2}/\d{1,2}/\d{4}\s*\n*",
    re.UNICODE,
)


def today_iso() -> str:
    """Current UTC calendar date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).date().isoformat()


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_numeric_date(text: str, *, reference: date) -> date | None:
    text = text.strip()
    m = _ISO_DATE.match(text)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = _DMY_SLASH.match(text)
    if m:
        day, month, year_s = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(year_s)
        if year < 100:
            year += 2000 if year < 70 else 1900
        # Prefer day-month-year (IL); fall back to month-day-year if invalid.
        parsed = _safe_date(year, month, day)
        if parsed is None:
            parsed = _safe_date(year, day, month)
        return parsed

    m = _DMY_DASH.match(text)
    if m:
        a, b, year_s = int(m.group(1)), int(m.group(2)), m.group(3)
        if year_s:
            year = int(year_s)
            if year < 100:
                year += 2000 if year < 70 else 1900
            parsed = _safe_date(year, b, a)  # DD-MM-YYYY
            if parsed is None:
                parsed = _safe_date(year, a, b)  # MM-DD-YYYY
            return parsed
        # DD-MM without year (e.g. "12-05") — assume current year, IL day-month.
        year = reference.year
        parsed = _safe_date(year, b, a)
        if parsed is None:
            parsed = _safe_date(year, a, b)
        if parsed is not None and parsed > reference + timedelta(days=1):
            # Future date without year → previous year.
            parsed = _safe_date(year - 1, parsed.month, parsed.day) or parsed
        return parsed

    return None


def _parse_relative_hebrew(text: str, *, reference: date) -> date | None:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return None

    if _HE_TODAY.match(cleaned):
        return reference
    if _HE_YESTERDAY.match(cleaned):
        return reference - timedelta(days=1)
    if _HE_TWO_DAYS.match(cleaned):
        return reference - timedelta(days=2)

    m = _HE_MINUTES.match(cleaned) or _HE_HOURS.match(cleaned)
    if m:
        return reference

    m = _HE_DAYS.match(cleaned)
    if m:
        return reference - timedelta(days=int(m.group(1)))

    m = _HE_WEEKS.match(cleaned)
    if m:
        return reference - timedelta(weeks=int(m.group(1)))

    m = _HE_MONTHS.match(cleaned)
    if m:
        # Approximate month length; good enough for sorting buckets.
        return reference - timedelta(days=30 * int(m.group(1)))

    # English relatives: "2 days ago", "3 weeks ago"
    m = re.match(
        r"^(\d+)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?|months?)\s+ago$",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("min") or unit.startswith("hour") or unit.startswith("hr"):
            return reference
        if unit.startswith("day"):
            return reference - timedelta(days=n)
        if unit.startswith("week"):
            return reference - timedelta(weeks=n)
        if unit.startswith("month"):
            return reference - timedelta(days=30 * n)

    return None


def normalize_posted_date(
    value: Any,
    *,
    default_to_today: bool = True,
    reference: date | None = None,
) -> str | None:
    """Convert diverse scraped date values into YYYY-MM-DD.

    Accepts ISO timestamps, DD/MM/YYYY, DD-MM, Hebrew relatives ("היום",
    "לפני יומיים"), and English "N days ago". When nothing can be parsed and
    ``default_to_today`` is True, returns today's UTC date.
    """
    ref = reference or datetime.now(timezone.utc).date()

    def _default() -> str | None:
        return ref.isoformat() if default_to_today else None

    if value is None:
        return _default()

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        # Treat large numbers as unix seconds/ms.
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return _default()

    text = str(value).strip()
    if not text:
        return _default()

    # Full ISO / fromisoformat-friendly strings.
    try:
        iso_candidate = text.replace("Z", "+00:00")
        if "T" in iso_candidate or (" " in iso_candidate and re.search(r"\d{2}:\d{2}", iso_candidate)):
            parsed_dt = datetime.fromisoformat(iso_candidate)
            return parsed_dt.date().isoformat()
    except ValueError:
        pass

    parsed = _parse_numeric_date(text, reference=ref)
    if parsed is not None:
        return parsed.isoformat()

    parsed = _parse_relative_hebrew(text, reference=ref)
    if parsed is not None:
        return parsed.isoformat()

    # dateutil as last resort when available.
    try:
        from dateutil import parser as date_parser

        parsed_dt = date_parser.parse(text, dayfirst=True, fuzzy=True)
        return parsed_dt.date().isoformat()
    except Exception:
        pass

    return _default()


def format_posted_date_he(iso_date: str | None) -> str:
    """Elegant Hebrew publication-date line, e.g. '📅 תאריך פרסום: 18/07/2026'."""
    normalized = normalize_posted_date(iso_date, default_to_today=True) or today_iso()
    year, month, day = normalized.split("-")
    return f"📅 תאריך פרסום: {int(day):02d}/{int(month):02d}/{year}"


def inject_posted_date_header(description: str | None, posted_date: str | None) -> str:
    """Prepend a Hebrew publication-date header to job description text (RTL-safe)."""
    body = (description or "").strip()
    # Avoid duplicating the header on repeated serialization.
    body = POSTED_DATE_HEADER_RE.sub("", body).lstrip()
    header = format_posted_date_he(posted_date)
    if not body:
        return header
    return f"{header}\n\n{body}"


def pick_raw_posted_date(*candidates: Any) -> Any:
    """Return the first non-empty candidate suitable for normalize_posted_date."""
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, (date, datetime, int, float)):
            return value
        text = str(value).strip()
        if text:
            return text
    return None
