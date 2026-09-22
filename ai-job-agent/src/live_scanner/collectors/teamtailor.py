"""Teamtailor public careers JSON Feed collector."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json


class TeamtailorCollector(BaseCollector):
    """GET https://{careers-host}/jobs.json (JSON Feed 1.1).

    board_identifier: careers hostname (e.g. career.example.com or
    company.teamtailor.com) or full careers URL.

    Location is preferentially taken from schema.org ``_jobposting.jobLocation``
    when the top-level ``location`` field is empty (common on Teamtailor feeds).
    """

    provider = "teamtailor"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        host = self._resolve_host(board_identifier, careers_url)
        if not host:
            return (
                "Teamtailor board_identifier must be a careers host "
                "(e.g. career.example.com or company.teamtailor.com)"
            )
        if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
            return f"Invalid Teamtailor host: {host!r}"
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        host = self._resolve_host(board_identifier, careers_url)
        err = self.validate_config(board_identifier=host or board_identifier, careers_url=careers_url)
        if err:
            return CollectResult(status="error", error=err)

        url = f"https://{host}/jobs.json"
        try:
            data, status = http_json(url)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(status="error", error=str(exc))

        if not isinstance(data, dict):
            return CollectResult(
                status="error",
                error="Unexpected Teamtailor response shape (expected JSON Feed object)",
                http_status=status,
            )

        items = data.get("items")
        if not isinstance(items, list):
            return CollectResult(
                status="error",
                error="Unexpected Teamtailor feed: missing items[]",
                http_status=status,
            )

        company = str(data.get("title") or host.split(".")[0]).strip() or host
        jobs: list[CollectedJob] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ext_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            job_url = str(item.get("url") or "").strip()
            if not title:
                continue
            if not ext_id:
                ext_id = job_url.rstrip("/").split("/")[-1] if job_url else ""
            if not ext_id:
                continue

            location = str(item.get("location") or "").strip()
            country = ""
            country_code = ""
            is_remote = None
            jobposting = item.get("_jobposting") if isinstance(item.get("_jobposting"), dict) else {}
            places = _places_from_jobposting(jobposting)
            if places:
                # Prefer an Israel place when a posting lists multiple countries
                # (global boards often include IL among several offices).
                chosen = _prefer_israel_place(places) or places[0]
                city = chosen.get("city") or ""
                country = chosen.get("country") or ""
                country_code = chosen.get("country_code") or ""
                region = chosen.get("region") or ""
                if not location:
                    location = ", ".join(p for p in (city, region, country or country_code) if p)
            if jobposting.get("jobLocationType"):
                jlt = str(jobposting.get("jobLocationType") or "").lower()
                if "remote" in jlt or "telecommute" in jlt:
                    is_remote = True
            if location and "remote" in location.lower():
                is_remote = True if is_remote is None else is_remote

            jobs.append(
                CollectedJob(
                    external_job_id=ext_id,
                    title=title,
                    company=company,
                    job_url=job_url or f"https://{host}/jobs/{ext_id}",
                    location=location,
                    country=country,
                    country_code=country_code,
                    is_remote=is_remote,
                    posted_date=_tt_date(item.get("date_published")),
                    raw=item,
                )
            )

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=status)
        return CollectResult(jobs=jobs, status="ok", http_status=status)

    @staticmethod
    def _resolve_host(board_identifier: str, careers_url: str | None) -> str:
        raw = (board_identifier or "").strip()
        if raw and "://" not in raw and "/" not in raw and "." in raw:
            return raw.lower()
        url = careers_url or board_identifier or ""
        if "://" not in url and url and "." in url and "/" not in url:
            return url.lower()
        if "://" in url:
            host = (urlparse(url).netloc or "").lower()
            return host
        return ""


def _places_from_jobposting(jobposting: dict) -> list[dict[str, str]]:
    raw_loc = jobposting.get("jobLocation")
    if raw_loc is None:
        return []
    entries = raw_loc if isinstance(raw_loc, list) else [raw_loc]
    places: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address") if isinstance(entry.get("address"), dict) else entry
        if not isinstance(address, dict):
            continue
        city = str(address.get("addressLocality") or "").strip()
        region = str(address.get("addressRegion") or "").strip()
        country = str(address.get("addressCountry") or "").strip()
        country_code = country if len(country) <= 3 else ""
        if country and len(country) > 3:
            # Full country name — keep as country, leave code empty for filter text
            country_code = ""
        places.append(
            {
                "city": city,
                "region": region,
                "country": country,
                "country_code": country_code,
            }
        )
    return places


def _prefer_israel_place(places: list[dict[str, str]]) -> dict[str, str] | None:
    for place in places:
        code = (place.get("country_code") or place.get("country") or "").strip().lower()
        if code in {"il", "isr", "israel"}:
            return place
        blob = " ".join(
            [
                place.get("city") or "",
                place.get("region") or "",
                place.get("country") or "",
            ]
        ).lower()
        if "israel" in blob or "tel aviv" in blob or "תל אביב" in blob or "ישראל" in blob:
            return place
    return None


def _tt_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None
