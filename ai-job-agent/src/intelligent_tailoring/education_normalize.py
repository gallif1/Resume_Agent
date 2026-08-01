"""Normalize education entries so renderers never print raw JSON / dict blobs."""

from __future__ import annotations

import ast
import json
import re
from typing import Any


def _looks_like_dict_repr(text: str) -> bool:
    s = (text or "").strip()
    return s.startswith("{") and s.endswith("}") and (
        "degrees" in s or "institutions" in s or ":" in s
    )


def _parse_maybe_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _looks_like_dict_repr(text):
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            data = loader(text)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


def _first_str(items: Any) -> str:
    if isinstance(items, list):
        for item in items:
            text = str(item or "").strip()
            if text and not _looks_like_dict_repr(text):
                return text
        return ""
    if items is None:
        return ""
    text = str(items).strip()
    return "" if _looks_like_dict_repr(text) else text


def flatten_education_aggregator(edu: dict[str, Any]) -> list[dict[str, str]]:
    """Expand aggregator shapes like {degrees, institutions, fields_of_study}."""
    degrees = list(edu.get("degrees") or edu.get("degree_list") or [])
    institutions = list(edu.get("institutions") or edu.get("schools") or [])
    fields = list(
        edu.get("fields_of_study")
        or edu.get("fields")
        or edu.get("majors")
        or []
    )
    dates_list = list(edu.get("dates") or edu.get("years") or edu.get("graduation_years") or [])
    if not isinstance(dates_list, list):
        dates_list = [dates_list] if dates_list else []

    # Already a normal entry
    if edu.get("institution") or edu.get("degree") or edu.get("school"):
        if not (degrees or institutions) or (
            edu.get("institution") and edu.get("degree")
        ):
            return [
                {
                    "institution": str(
                        edu.get("institution") or edu.get("school") or ""
                    ).strip(),
                    "degree": str(edu.get("degree") or edu.get("diploma") or "").strip(),
                    "field": str(
                        edu.get("field") or edu.get("field_of_study") or ""
                    ).strip(),
                    "dates": str(
                        edu.get("dates") or edu.get("year") or edu.get("graduation_year") or ""
                    ).strip(),
                }
            ]

    n = max(len(degrees), len(institutions), len(fields), 1 if edu else 0)
    if n == 0:
        return []
    out: list[dict[str, str]] = []
    for i in range(n):
        degree = str(degrees[i] if i < len(degrees) else (degrees[-1] if degrees else "")).strip()
        institution = str(
            institutions[i]
            if i < len(institutions)
            else (institutions[-1] if institutions else "")
        ).strip()
        field = str(fields[i] if i < len(fields) else (fields[-1] if fields else "")).strip()
        dates = str(
            dates_list[i]
            if i < len(dates_list)
            else (dates_list[-1] if dates_list else "")
        ).strip()
        if not degree and not institution and not field:
            continue
        # Compose a readable degree line when field is separate
        degree_line = degree
        if field and field.lower() not in degree.lower():
            degree_line = f"{degree} in {field}".strip() if degree else field
        out.append(
            {
                "institution": institution,
                "degree": degree_line,
                "field": field,
                "dates": dates,
            }
        )
    return out


def normalize_education_entry(entry: Any) -> list[dict[str, str]]:
    """Normalize one education item into zero-or-more clean dicts."""
    if entry is None:
        return []
    if isinstance(entry, str):
        parsed = _parse_maybe_mapping(entry)
        if parsed is not None:
            return normalize_education_list([parsed])
        text = entry.strip()
        if not text or _looks_like_dict_repr(text):
            return []
        # "B.Sc. Computer Science — SCE — 2024"
        parts = [p.strip() for p in re.split(r"\s+[—\-–]\s+", text) if p.strip()]
        if len(parts) >= 2:
            return [
                {
                    "degree": parts[0],
                    "institution": parts[1],
                    "field": "",
                    "dates": parts[2] if len(parts) > 2 else "",
                }
            ]
        return [{"degree": text, "institution": "", "field": "", "dates": ""}]

    if not isinstance(entry, dict):
        text = str(entry).strip()
        if not text or _looks_like_dict_repr(text):
            return []
        return [{"degree": text, "institution": "", "field": "", "dates": ""}]

    # Aggregator or normal dict
    if any(
        k in entry
        for k in ("degrees", "institutions", "fields_of_study", "degree_list", "schools")
    ):
        return [
            e
            for e in flatten_education_aggregator(entry)
            if e.get("degree") or e.get("institution")
        ]

    # Nested entries
    if isinstance(entry.get("entries"), list):
        return normalize_education_list(entry.get("entries"))
    if isinstance(entry.get("items"), list):
        return normalize_education_list(entry.get("items"))

    degree = str(
        entry.get("degree") or entry.get("diploma") or entry.get("qualification") or ""
    ).strip()
    institution = str(
        entry.get("institution") or entry.get("school") or entry.get("university") or ""
    ).strip()
    field = str(
        entry.get("field") or entry.get("field_of_study") or entry.get("major") or ""
    ).strip()
    dates = str(
        entry.get("dates")
        or entry.get("year")
        or entry.get("graduation_year")
        or entry.get("end_date")
        or ""
    ).strip()

    # Recover from polluted string fields that contain dict reprs
    for label, value in (("degree", degree), ("institution", institution)):
        parsed = _parse_maybe_mapping(value)
        if parsed is not None:
            return normalize_education_list([parsed])

    if _looks_like_dict_repr(degree) or _looks_like_dict_repr(institution):
        return []

    if not degree and not institution and not field:
        return []

    if field and degree and field.lower() not in degree.lower():
        degree_line = f"{degree} in {field}"
    else:
        degree_line = degree or field

    return [
        {
            "institution": institution,
            "degree": degree_line,
            "field": field,
            "dates": dates,
        }
    ]


def normalize_education_list(education: Any) -> list[dict[str, str]]:
    """Normalize any education payload into clean renderer-safe entries."""
    if education is None:
        return []
    if isinstance(education, dict):
        return [
            e
            for e in normalize_education_entry(education)
            if e.get("degree") or e.get("institution")
        ]
    if not isinstance(education, list):
        return normalize_education_entry(education)

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in education:
        for entry in normalize_education_entry(item):
            key = f"{entry.get('degree','').lower()}|{entry.get('institution','').lower()}"
            if not key.strip("|") or key in seen:
                continue
            seen.add(key)
            out.append(entry)
    return out


def education_contains_raw_json(education: Any) -> bool:
    """True if any education field still looks like dumped JSON/dict."""
    for entry in normalize_education_list(education) if not isinstance(education, list) else education:
        if not isinstance(entry, dict):
            if _looks_like_dict_repr(str(entry)):
                return True
            continue
        for key in ("degree", "institution", "field", "dates"):
            if _looks_like_dict_repr(str(entry.get(key) or "")):
                return True
    # Also check raw list items before normalize
    if isinstance(education, list):
        for item in education:
            if isinstance(item, str) and _looks_like_dict_repr(item):
                return True
            if isinstance(item, dict) and any(
                _looks_like_dict_repr(str(item.get(k) or ""))
                for k in ("degree", "institution")
            ):
                return True
    return False
