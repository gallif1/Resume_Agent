"""Comeet careers API collector.

Comeet's structured careers API requires a company UID + public token.
board_identifier formats:
- ``{company_uid}:{token}``
- careers URL containing both (best-effort parse)

Without a token the adapter is marked unsupported rather than scraping HTML.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json


class ComeetCollector(BaseCollector):
    provider = "comeet"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        parsed = self._parse(board_identifier, careers_url)
        if not parsed:
            return (
                "Comeet requires board_identifier as company_uid:token "
                "(public careers API credentials)"
            )
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        parsed = self._parse(board_identifier, careers_url)
        if not parsed:
            return CollectResult(
                status="unsupported",
                error=(
                    "Comeet public API requires company_uid:token. "
                    "HTML scraping is not enabled for this provider."
                ),
            )

        company_uid, token = parsed
        url = (
            "https://www.comeet.co/careers-api/2.0/"
            f"company/{company_uid}/positions?token={token}&details=true"
        )
        try:
            data, status = http_json(url)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(status="error", error=str(exc))

        listings = data if isinstance(data, list) else (
            data.get("positions") if isinstance(data, dict) else None
        )
        if not isinstance(listings, list):
            return CollectResult(
                status="error",
                error="Unexpected Comeet response shape",
                http_status=status,
            )

        jobs: list[CollectedJob] = []
        for item in listings:
            if not isinstance(item, dict):
                continue
            ext_id = str(item.get("uid") or item.get("id") or "").strip()
            title = str(item.get("name") or item.get("title") or "").strip()
            if not ext_id or not title:
                continue
            job_url = str(
                item.get("url_comeet_hosted_page")
                or item.get("url_active_page")
                or item.get("email_url")
                or ""
            ).strip()
            if not job_url:
                job_url = f"https://www.comeet.com/jobs/position/{ext_id}"
            location = ""
            country = ""
            country_code = ""
            is_remote = None
            loc = item.get("location")
            if isinstance(loc, dict):
                city = str(loc.get("city") or loc.get("name") or "").strip()
                country = str(
                    loc.get("country") or loc.get("country_name") or ""
                ).strip()
                country_code = str(
                    loc.get("country_code") or loc.get("countryCode") or ""
                ).strip()
                location = str(loc.get("name") or "").strip()
                if not location:
                    location = ", ".join(p for p in (city, country or country_code) if p)
                if loc.get("is_remote") is not None:
                    is_remote = bool(loc.get("is_remote"))
                elif loc.get("remote") is not None:
                    is_remote = bool(loc.get("remote"))
            elif isinstance(loc, str):
                location = loc.strip()
            company = str(
                (item.get("company") or {}).get("name")
                if isinstance(item.get("company"), dict)
                else item.get("department")
                or company_uid
            ).strip()
            desc = str(item.get("description") or item.get("details") or "").strip()
            jobs.append(
                CollectedJob(
                    external_job_id=ext_id,
                    title=title,
                    company=company or company_uid,
                    job_url=job_url,
                    location=location,
                    country=country,
                    country_code=country_code,
                    is_remote=is_remote,
                    description=desc[:4000] if desc else "",
                    posted_date=_comeet_date(item.get("time_updated") or item.get("time_created")),
                    raw=item,
                )
            )

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=status)
        return CollectResult(jobs=jobs, status="ok", http_status=status)

    @staticmethod
    def _parse(board_identifier: str, careers_url: str | None) -> tuple[str, str] | None:
        raw = (board_identifier or "").strip()
        if ":" in raw and "://" not in raw:
            uid, token = raw.split(":", 1)
            uid, token = uid.strip(), token.strip()
            if uid and token and re.fullmatch(r"[A-Za-z0-9_-]+", uid):
                return uid, token

        for candidate in (raw, careers_url or ""):
            if not candidate or "://" not in candidate:
                continue
            parsed = urlparse(candidate)
            qs = parse_qs(parsed.query)
            token = (qs.get("token") or [""])[0].strip()
            # path .../company/{uid}/...
            m = re.search(r"/company/([^/]+)/", parsed.path or "")
            uid = m.group(1) if m else ""
            if uid and token:
                return uid, token
        return None


def _comeet_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None
