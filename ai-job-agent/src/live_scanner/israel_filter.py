"""Shared Israel-market location filter for the Live Job Scanner.

Used once after collectors return jobs and before dedupe / DB / matching.
Conservative: remote jobs are accepted only with clear Israel eligibility.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from live_scanner.collectors.base import CollectedJob

MARKET = "Israel"

# ISO / common country codes for Israel
_ISRAEL_COUNTRY_CODES = frozenset({"il", "isr", "israel"})

# Explicit non-Israel remote markers (matched against compacted location text,
# so use space-separated forms — punctuation is folded away by `_compact`).
_FOREIGN_REMOTE_MARKERS = (
    "remote us",
    "remote usa",
    "remote united states",
    "remote uk",
    "remote gb",
    "remote united kingdom",
    "remote eu",
    "remote europe",
    "remote emea",
    "remote apac",
    "remote india",
    "remote canada",
    "remote germany",
    "remote france",
    "united states only",
    "us only",
    "usa only",
    "uk only",
    "europe only",
    "worldwide remote",
    "work from anywhere",
    "anywhere in the world",
)

_ISRAEL_CITY_HINTS = (
    # English
    "israel",
    "tel aviv",
    "tel-aviv",
    "tel aviv-yafo",
    "tel aviv yafo",
    "tlv",
    "haifa",
    "jerusalem",
    "herzliya",
    "herzlia",
    "ramat gan",
    "petah tikva",
    "petach tikva",
    "raanana",
    "ra'anana",
    "rāʿanana",
    "kfar saba",
    "kfar sava",
    "netanya",
    "holon",
    "rishon lezion",
    "rishon le zion",
    "rishon l'tzion",
    "rehovot",
    "ness ziona",
    "nes ziona",
    "yokneam",
    "yoqneam",
    "caesarea",
    "hadera",
    "beer sheva",
    "be'er sheva",
    "beersheba",
    "ashdod",
    "ashkelon",
    "modiin",
    "modi'in",
    "kiryat gat",
    "kiryat shmona",
    "bnei brak",
    "beni brak",
    "givatayim",
    "ramat hasharon",
    "hod hasharon",
    "rosh haayin",
    "rosh ha'ayin",
    "nahariya",
    "acre",
    "akko",
    "eilat",
    "afula",
    "beit shemesh",
    "central district",
    "northern district",
    "southern district",
    "jerusalem district",
    "haifa district",
    "tel aviv district",
    # Hebrew
    "ישראל",
    "תל אביב",
    "תל-אביב",
    "ת״א",
    "ת\"א",
    "חיפה",
    "ירושלים",
    "הרצליה",
    "רמת גן",
    "פתח תקווה",
    "פתח תקוה",
    "רעננה",
    "כפר סבא",
    "נתניה",
    "חולון",
    "ראשון לציון",
    "רחובות",
    "נס ציונה",
    "יקנעם",
    "קיסריה",
    "חדרה",
    "באר שבע",
    "אשדוד",
    "אשקלון",
    "מודיעין",
    "קריית גת",
    "קרית גת",
    "קריית שמונה",
    "בני ברק",
    "גבעתיים",
    "רמת השרון",
    "הוד השרון",
    "ראש העין",
    "נהריה",
    "עכו",
    "אילת",
    "עפולה",
    "בית שמש",
)

_REMOTE_WORDS = ("remote", "work from home", "wfh", "distributed", "hybrid", "מרחוק", "היברידי")


@dataclass(frozen=True)
class LocationDecision:
    accepted: bool
    reason: str  # israel | remote_israel | foreign | remote_unknown | empty


def _fold(text: str) -> str:
    """Lowercase + strip accents for Latin text; keep Hebrew as-is."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", str(text))
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_marks.lower().strip()


def _compact(text: str) -> str:
    return re.sub(r"[\s,_./\\-]+", " ", _fold(text)).strip()


def _country_code_from_value(value: object) -> str:
    raw = _fold(str(value or ""))
    raw = raw.replace(".", "").strip()
    if raw in _ISRAEL_COUNTRY_CODES:
        return "il"
    if raw in {"israel", "state of israel"}:
        return "il"
    return raw


def _extract_structured(job: CollectedJob | dict[str, Any]) -> dict[str, Any]:
    if isinstance(job, CollectedJob):
        raw = job.raw if isinstance(job.raw, dict) else {}
        return {
            "location": job.location or "",
            "country": getattr(job, "country", "") or raw.get("country") or "",
            "country_code": getattr(job, "country_code", "") or raw.get("country_code") or "",
            "is_remote": getattr(job, "is_remote", None),
            "raw": raw,
            "title": job.title or "",
            "description": job.description or "",
        }
    raw = job.get("raw") if isinstance(job.get("raw"), dict) else {}
    return {
        "location": str(job.get("location") or ""),
        "country": str(job.get("country") or raw.get("country") or ""),
        "country_code": str(job.get("country_code") or raw.get("country_code") or ""),
        "is_remote": job.get("is_remote"),
        "raw": raw,
        "title": str(job.get("title") or ""),
        "description": str(job.get("description") or ""),
    }


def _structured_country_signals(raw: dict[str, Any], country: str, country_code: str) -> str | None:
    """Return 'il' if structured fields clearly say Israel, 'foreign' if clearly not, else None."""
    candidates: list[object] = [
        country_code,
        country,
        raw.get("country_code"),
        raw.get("countryCode"),
        raw.get("country"),
        raw.get("country_name"),
        raw.get("countryName"),
    ]
    loc = raw.get("location")
    if isinstance(loc, dict):
        candidates.extend(
            [
                loc.get("country"),
                loc.get("countryCode"),
                loc.get("country_code"),
                loc.get("countryName"),
            ]
        )
    for value in candidates:
        code = _country_code_from_value(value)
        if not code:
            continue
        if code in _ISRAEL_COUNTRY_CODES or code == "il":
            return "il"
        # Clear multi-letter foreign country codes / names
        if code in {
            "us",
            "usa",
            "united states",
            "uk",
            "gb",
            "united kingdom",
            "de",
            "germany",
            "fr",
            "france",
            "in",
            "india",
            "ca",
            "canada",
            "au",
            "australia",
            "nl",
            "netherlands",
            "pl",
            "poland",
            "es",
            "spain",
            "it",
            "italy",
            "ie",
            "ireland",
            "sg",
            "singapore",
            "jp",
            "japan",
            "cn",
            "china",
            "br",
            "brazil",
            "mx",
            "mexico",
            "uae",
            "ae",
            "sa",
            "saudi arabia",
        }:
            return "foreign"
    return None


def _location_blob(fields: dict[str, Any]) -> str:
    raw = fields["raw"] if isinstance(fields["raw"], dict) else {}
    parts: list[str] = [
        fields.get("location") or "",
        fields.get("country") or "",
        fields.get("country_code") or "",
    ]
    loc = raw.get("location")
    if isinstance(loc, dict):
        parts.extend(
            str(loc.get(k) or "")
            for k in (
                "city",
                "region",
                "country",
                "countryCode",
                "fullLocation",
                "name",
                "text",
            )
        )
    elif isinstance(loc, str):
        parts.append(loc)
    for key in ("city", "locationsText", "locations", "workplaceType", "workplace_type"):
        val = raw.get(key)
        if isinstance(val, list):
            parts.extend(str(x) for x in val)
        elif val:
            parts.append(str(val))
    return _compact(" ".join(parts))


def _has_israel_place_hint(blob: str) -> bool:
    if not blob:
        return False
    # Word-ish matches: avoid treating bare "il" inside other words via boundaries
    if re.search(r"(?<![a-z])il(?![a-z])", blob) and (
        "israel" in blob
        or "remote" in blob
        or any(city in blob for city in ("tel aviv", "haifa", "jerusalem", "herzliya"))
    ):
        return True
    return any(hint in blob for hint in _ISRAEL_CITY_HINTS)


def _is_remoteish(fields: dict[str, Any], blob: str) -> bool:
    if fields.get("is_remote") is True:
        return True
    raw = fields["raw"] if isinstance(fields["raw"], dict) else {}
    loc = raw.get("location")
    if isinstance(loc, dict) and (loc.get("remote") is True or loc.get("hybrid") is True):
        return True
    return any(word in blob for word in _REMOTE_WORDS)


def classify_israel_job(job: CollectedJob | dict[str, Any]) -> LocationDecision:
    """Decide whether a collected job belongs to the Israel market."""
    fields = _extract_structured(job)
    structured = _structured_country_signals(
        fields["raw"] if isinstance(fields["raw"], dict) else {},
        str(fields.get("country") or ""),
        str(fields.get("country_code") or ""),
    )
    blob = _location_blob(fields)
    remoteish = _is_remoteish(fields, blob)

    if structured == "il":
        if remoteish:
            return LocationDecision(True, "remote_israel")
        return LocationDecision(True, "israel")

    if structured == "foreign":
        # Structured foreign country wins — even if description mentions Israel later.
        return LocationDecision(False, "foreign")

    if _has_israel_place_hint(blob):
        if remoteish:
            return LocationDecision(True, "remote_israel")
        return LocationDecision(True, "israel")

    # Explicit foreign remote markers in location text
    if any(marker in blob for marker in _FOREIGN_REMOTE_MARKERS):
        return LocationDecision(False, "foreign")

    if remoteish:
        # Bare "Remote" / worldwide without Israel eligibility → reject
        return LocationDecision(False, "remote_unknown")

    if not blob.strip():
        return LocationDecision(False, "empty")

    return LocationDecision(False, "foreign")


def is_israel_job(job: CollectedJob | dict[str, Any]) -> bool:
    return classify_israel_job(job).accepted


def filter_israel_jobs(
    jobs: Iterable[CollectedJob],
) -> tuple[list[CollectedJob], int, int]:
    """Return (israel_jobs, fetched_count, foreign_filtered_count)."""
    jobs_list = list(jobs)
    kept: list[CollectedJob] = []
    for job in jobs_list:
        if is_israel_job(job):
            kept.append(job)
    fetched = len(jobs_list)
    filtered = fetched - len(kept)
    return kept, fetched, filtered
