"""Deterministic date / years-of-experience calculations for the tailor pipeline.

LLM output must never invent years of experience. All year figures that reach the
tailored resume are computed here from explicit resume date ranges.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

from dateutil import parser as date_parser

_PRESENT_RE = re.compile(
    r"\b(present|current|now|today|ongoing|היום|כיום|נוכחי)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_RANGE_RE = re.compile(
    r"((?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2})"
    r"\s*[-–—to]+\s*"
    r"((?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2}|present|current|now|today|היום|כיום)",
    re.IGNORECASE,
)
_MONTH_YEAR_RE = re.compile(r"[A-Za-z]{3,9}\.?\s+(?:19|20)\d{2}")


def _safe_parse(token: str, *, default: datetime | None = None) -> date | None:
    token = (token or "").strip()
    if not token:
        return None
    if _PRESENT_RE.search(token):
        return date.today()
    try:
        parsed = date_parser.parse(
            token, default=default or datetime(2000, 1, 1), fuzzy=True
        )
        return parsed.date()
    except (ValueError, OverflowError, TypeError):
        year_match = _YEAR_RE.search(token)
        if year_match:
            try:
                return date(int(year_match.group(0)), 1, 1)
            except ValueError:
                return None
    return None


def parse_date_range(text: str) -> tuple[date | None, date | None]:
    """Parse a start/end pair from a free-text dates field."""
    text = (text or "").strip()
    if not text:
        return None, None
    match = _RANGE_RE.search(text)
    if match:
        return _safe_parse(match.group(1)), _safe_parse(match.group(2))
    years = [int(y) for y in _YEAR_RE.findall(text)]
    if len(years) >= 2:
        return date(min(years), 1, 1), date(max(years), 12, 31)
    if len(years) == 1:
        start = date(years[0], 1, 1)
        end = date.today() if _PRESENT_RE.search(text) else date(years[0], 12, 31)
        return start, end
    return None, None


def years_between(start: date | None, end: date | None) -> float | None:
    if start is None or end is None:
        return None
    if end < start:
        start, end = end, start
    days = (end - start).days
    return round(days / 365.25, 2)


def estimate_years_from_text(experience_text: str) -> float | None:
    """Estimate total career span from the earliest to latest date mentioned.

    This mirrors ``parse_cv._estimate_years_of_experience`` but returns a float
    and never invents a number when fewer than two anchors exist.
    """
    if not (experience_text or "").strip():
        return None

    normalized = _PRESENT_RE.sub(str(date.today().year), experience_text)
    parsed_dates: list[date] = []

    for token in _MONTH_YEAR_RE.findall(normalized):
        d = _safe_parse(token)
        if d:
            parsed_dates.append(d)

    for match in _RANGE_RE.finditer(experience_text):
        for group in match.groups():
            d = _safe_parse(group)
            if d:
                parsed_dates.append(d)

    years = [int(y) for y in _YEAR_RE.findall(normalized)]
    for y in years:
        parsed_dates.append(date(y, 1, 1))

    if len(parsed_dates) < 2:
        return None
    span = years_between(min(parsed_dates), max(parsed_dates))
    return span


def years_from_experience_entries(
    entries: Iterable[dict[str, Any]],
) -> float | None:
    """Sum non-overlapping experience from structured role date fields."""
    intervals: list[tuple[date, date]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        dates_text = str(entry.get("dates") or entry.get("date") or "")
        start, end = parse_date_range(dates_text)
        if start and end:
            intervals.append((start, end if end >= start else start))
    if not intervals:
        return None
    intervals.sort(key=lambda pair: pair[0])
    merged: list[tuple[date, date]] = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    total_days = sum((end - start).days for start, end in merged)
    return round(total_days / 365.25, 2)


def claim_years_supported(
    claimed_years: float | int | None,
    *,
    resume_years: float | None,
    tolerance: float = 0.5,
) -> bool:
    """True when a claimed years figure is supported by resume-derived years."""
    if claimed_years is None:
        return True
    if resume_years is None:
        return False
    try:
        claimed = float(claimed_years)
    except (TypeError, ValueError):
        return False
    return claimed <= resume_years + tolerance


_YEARS_CLAIM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*\+?\s*(?:\+)?\s*(?:years?|yrs?|שנים|שנה)",
    re.IGNORECASE,
)

_WORD_YEARS: dict[str, float] = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "eleven": 11.0,
    "twelve": 12.0,
    "fifteen": 15.0,
    "twenty": 20.0,
}

_WORD_YEARS_CLAIM_RE = re.compile(
    r"\b(?:over|more\s+than|at\s+least|nearly|around|approximately|about|"
    r"with|of)?\s*"
    r"(" + "|".join(_WORD_YEARS.keys()) + r")"
    r"\s*\+?\s*(?:years?|yrs?)\b"
    r"(?:\s+of\s+(?:expertise|experience|professional\s+experience))?",
    re.IGNORECASE,
)


def extract_years_claims(text: str) -> list[float]:
    """Pull numeric and worded years-of-experience claims out of generated prose.

    Catches both ``3+ years`` and ``over three years of expertise``.
    """
    claims: list[float] = []
    for match in _YEARS_CLAIM_RE.finditer(text or ""):
        try:
            claims.append(float(match.group(1)))
        except ValueError:
            continue
    for match in _WORD_YEARS_CLAIM_RE.finditer(text or ""):
        word = match.group(1).lower()
        value = _WORD_YEARS.get(word)
        if value is not None:
            claims.append(value)
    return claims


def has_inflated_years_claim(
    text: str,
    *,
    resume_years: float | None,
    professional_years: float | None = None,
    tolerance: float = 0.5,
) -> tuple[bool, str]:
    """True when prose claims more years than professional evidence supports.

    Prefer ``professional_years`` (employment only) over total resume span so
    academic/project dates cannot inflate seniority.
    """
    claims = extract_years_claims(text)
    if not claims:
        return False, ""
    baseline = professional_years if professional_years is not None else resume_years
    for claimed in claims:
        if not claim_years_supported(claimed, resume_years=baseline, tolerance=tolerance):
            return True, f"unsupported_years_claim:{claimed}"
    return False, ""
