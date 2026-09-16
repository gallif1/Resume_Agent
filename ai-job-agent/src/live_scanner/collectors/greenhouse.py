"""Greenhouse Job Board API collector."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json, path_of


class GreenhouseCollector(BaseCollector):
    provider = "greenhouse"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        token = self._resolve_token(board_identifier, careers_url)
        if not token:
            return "Greenhouse board_identifier (board token) is required"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return f"Invalid Greenhouse board token: {token!r}"
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        token = self._resolve_token(board_identifier, careers_url)
        err = self.validate_config(board_identifier=token or board_identifier, careers_url=careers_url)
        if err:
            return CollectResult(status="error", error=err)

        # content=true adds descriptions; omit for cheap discovery of large boards.
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        try:
            data, status = http_json(url)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(status="error", error=str(exc))

        listings = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(listings, list):
            return CollectResult(
                status="error",
                error="Unexpected Greenhouse response shape",
                http_status=status,
            )

        jobs: list[CollectedJob] = []
        for item in listings:
            if not isinstance(item, dict):
                continue
            ext_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            absolute = str(item.get("absolute_url") or "").strip()
            if not ext_id or not title:
                continue
            if not absolute:
                absolute = f"https://boards.greenhouse.io/{token}/jobs/{ext_id}"
            location = ""
            loc = item.get("location")
            if isinstance(loc, dict):
                location = str(loc.get("name") or "").strip()
            elif isinstance(loc, str):
                location = loc.strip()
            company = str(item.get("company_name") or token or "").strip()
            jobs.append(
                CollectedJob(
                    external_job_id=ext_id,
                    title=title,
                    company=company or (token or "Unknown"),
                    job_url=absolute,
                    location=location,
                    description="",
                    posted_date=_gh_date(item.get("updated_at") or item.get("created_at")),
                    raw=item,
                )
            )

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=status)
        return CollectResult(jobs=jobs, status="ok", http_status=status)

    @staticmethod
    def _resolve_token(board_identifier: str, careers_url: str | None) -> str:
        token = (board_identifier or "").strip().strip("/")
        if token and "://" not in token and "/" not in token:
            return token
        url = careers_url or board_identifier or ""
        path = path_of(url)
        parts = [p for p in path.split("/") if p]
        # boards.greenhouse.io/{token}/jobs/...
        host = (urlparse(url).netloc or "").lower()
        if "greenhouse.io" in host and parts:
            return parts[0]
        return token.split("/")[0] if token else ""


def _gh_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None
