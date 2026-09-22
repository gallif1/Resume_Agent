"""Workable public widget API collector."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json, path_of


class WorkableCollector(BaseCollector):
    """GET https://apply.workable.com/api/v1/widget/accounts/{account}

    Returns publicly listed jobs when the account exposes the widget feed.
    """

    provider = "workable"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        slug = self._resolve_slug(board_identifier, careers_url)
        if not slug:
            return "Workable board_identifier (account slug) is required"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
            return f"Invalid Workable account slug: {slug!r}"
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        slug = self._resolve_slug(board_identifier, careers_url)
        err = self.validate_config(board_identifier=slug or board_identifier, careers_url=careers_url)
        if err:
            return CollectResult(status="error", error=err)

        url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        try:
            data, status = http_json(url)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(status="error", error=str(exc))

        if not isinstance(data, dict):
            return CollectResult(
                status="error",
                error="Unexpected Workable response shape",
                http_status=status,
            )

        listings = data.get("jobs")
        if not isinstance(listings, list):
            return CollectResult(
                status="error",
                error="Unexpected Workable jobs list",
                http_status=status,
            )

        company = str(data.get("name") or slug).strip() or slug
        jobs: list[CollectedJob] = []
        for item in listings:
            if not isinstance(item, dict):
                continue
            ext_id = str(
                item.get("shortcode") or item.get("code") or item.get("id") or ""
            ).strip()
            title = str(item.get("title") or "").strip()
            if not ext_id or not title:
                continue
            city = str(item.get("city") or "").strip()
            country = str(item.get("country") or "").strip()
            state = str(item.get("state") or "").strip()
            location = str(item.get("location") or "").strip()
            if not location:
                location = ", ".join(p for p in (city, state, country) if p)
            job_url = str(
                item.get("url")
                or item.get("shortlink")
                or item.get("application_url")
                or ""
            ).strip()
            if not job_url:
                job_url = f"https://apply.workable.com/{slug}/j/{ext_id}"
            tele = item.get("telecommuting")
            jobs.append(
                CollectedJob(
                    external_job_id=ext_id,
                    title=title,
                    company=company,
                    job_url=job_url,
                    location=location,
                    country=country,
                    country_code=country,
                    is_remote=bool(tele) if tele is not None else None,
                    posted_date=_ww_date(item.get("published_on") or item.get("created_at")),
                    raw=item,
                )
            )

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=status)
        return CollectResult(jobs=jobs, status="ok", http_status=status)

    @staticmethod
    def _resolve_slug(board_identifier: str, careers_url: str | None) -> str:
        slug = (board_identifier or "").strip().strip("/")
        if slug and "://" not in slug and "/" not in slug:
            return slug
        url = careers_url or board_identifier or ""
        host = (urlparse(url).netloc or "").lower()
        path = path_of(url)
        parts = [p for p in path.split("/") if p]
        if "workable.com" in host and parts:
            return parts[0]
        return slug.split("/")[0] if slug else ""


def _ww_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None
