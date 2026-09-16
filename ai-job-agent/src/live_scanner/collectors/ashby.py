"""Ashby job board API collector."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json, path_of


class AshbyCollector(BaseCollector):
    provider = "ashby"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        board = self._resolve_board(board_identifier, careers_url)
        if not board:
            return "Ashby board_identifier is required"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
            return f"Invalid Ashby board name: {board!r}"
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        board = self._resolve_board(board_identifier, careers_url)
        err = self.validate_config(board_identifier=board or board_identifier, careers_url=careers_url)
        if err:
            return CollectResult(status="error", error=err)

        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        try:
            data, status = http_json(url)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(status="error", error=str(exc))

        listings = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(listings, list):
            return CollectResult(
                status="error",
                error="Unexpected Ashby response shape",
                http_status=status,
            )

        jobs: list[CollectedJob] = []
        for item in listings:
            if not isinstance(item, dict):
                continue
            # Skip closed postings when the API includes isListed
            if item.get("isListed") is False:
                continue
            ext_id = str(item.get("id") or item.get("jobId") or "").strip()
            title = str(item.get("title") or "").strip()
            job_url = str(
                item.get("jobUrl") or item.get("applyUrl") or item.get("url") or ""
            ).strip()
            if not ext_id or not title:
                continue
            if not job_url:
                job_url = f"https://jobs.ashbyhq.com/{board}/{ext_id}"
            location = str(
                item.get("location")
                or item.get("locationName")
                or ""
            ).strip()
            if not location:
                locs = item.get("address")
                if isinstance(locs, dict):
                    location = str(locs.get("postalAddress") or "").strip()
            company = str(item.get("department") or board or "").strip()
            # Prefer board name as company for consistency
            company_name = board.replace("-", " ").title() if board else company
            desc = str(item.get("descriptionPlain") or item.get("descriptionHtml") or "").strip()
            jobs.append(
                CollectedJob(
                    external_job_id=ext_id,
                    title=title,
                    company=company_name,
                    job_url=job_url,
                    location=location,
                    description=desc[:4000] if desc else "",
                    posted_date=_ashby_date(item.get("publishedAt") or item.get("updatedAt")),
                    raw=item,
                )
            )

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=status)
        return CollectResult(jobs=jobs, status="ok", http_status=status)

    @staticmethod
    def _resolve_board(board_identifier: str, careers_url: str | None) -> str:
        board = (board_identifier or "").strip().strip("/")
        if board and "://" not in board and "/" not in board:
            return board
        url = careers_url or board_identifier or ""
        path = path_of(url)
        parts = [p for p in path.split("/") if p]
        host = (urlparse(url).netloc or "").lower()
        if "ashbyhq.com" in host and parts:
            return parts[0]
        return board.split("/")[0] if board else ""


def _ashby_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None
