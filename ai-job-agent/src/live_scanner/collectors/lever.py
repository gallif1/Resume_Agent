"""Lever public postings API collector."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json, path_of


class LeverCollector(BaseCollector):
    provider = "lever"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        slug = self._resolve_slug(board_identifier, careers_url)
        if not slug:
            return "Lever board_identifier (company slug) is required"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
            return f"Invalid Lever company slug: {slug!r}"
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        slug = self._resolve_slug(board_identifier, careers_url)
        err = self.validate_config(board_identifier=slug or board_identifier, careers_url=careers_url)
        if err:
            return CollectResult(status="error", error=err)

        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            data, status = http_json(url)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(status="error", error=str(exc))

        if not isinstance(data, list):
            return CollectResult(
                status="error",
                error="Unexpected Lever response shape (expected list)",
                http_status=status,
            )

        jobs: list[CollectedJob] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            ext_id = str(item.get("id") or "").strip()
            title = str(item.get("text") or item.get("title") or "").strip()
            hosted = str(item.get("hostedUrl") or item.get("applyUrl") or "").strip()
            if not ext_id or not title or not hosted:
                continue
            categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
            location = str(
                categories.get("location")
                or item.get("location")
                or ""
            ).strip()
            company = str(item.get("company") or slug or "").strip()
            desc = ""
            desc_obj = item.get("descriptionPlain") or item.get("description")
            if isinstance(desc_obj, str):
                desc = desc_obj.strip()
            jobs.append(
                CollectedJob(
                    external_job_id=ext_id,
                    title=title,
                    company=company or (slug or "Unknown"),
                    job_url=hosted,
                    location=location,
                    description=desc[:4000],
                    posted_date=_lever_posted(item.get("createdAt")),
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
        path = path_of(url)
        # https://jobs.lever.co/{company}/...
        parts = [p for p in path.split("/") if p]
        if parts:
            return parts[0]
        host = urlparse(url).netloc if url else ""
        if host.startswith("jobs.lever.co"):
            return ""
        return slug.split("/")[0] if slug else ""


def _lever_posted(created_at: object) -> str | None:
    if created_at is None:
        return None
    try:
        # Lever often returns epoch ms
        ms = int(created_at)
        if ms > 10_000_000_000:
            ms = ms // 1000
        from datetime import datetime, timezone

        return datetime.fromtimestamp(ms, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        text = str(created_at).strip()
        return text[:10] if text else None
