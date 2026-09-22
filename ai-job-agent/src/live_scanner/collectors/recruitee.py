"""Recruitee public offers API collector."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json


class RecruiteeCollector(BaseCollector):
    """GET https://{company}.recruitee.com/api/offers"""

    provider = "recruitee"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        slug = self._resolve_slug(board_identifier, careers_url)
        if not slug:
            return "Recruitee board_identifier (company subdomain) is required"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
            return f"Invalid Recruitee company slug: {slug!r}"
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        slug = self._resolve_slug(board_identifier, careers_url)
        err = self.validate_config(board_identifier=slug or board_identifier, careers_url=careers_url)
        if err:
            return CollectResult(status="error", error=err)

        url = f"https://{slug}.recruitee.com/api/offers"
        try:
            data, status = http_json(url)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(status="error", error=str(exc))

        listings = data.get("offers") if isinstance(data, dict) else None
        if not isinstance(listings, list):
            return CollectResult(
                status="error",
                error="Unexpected Recruitee response shape",
                http_status=status,
            )

        jobs: list[CollectedJob] = []
        for item in listings:
            if not isinstance(item, dict):
                continue
            # status/open filters — skip closed when flagged
            state = str(item.get("status") or item.get("state_code") or "").lower()
            if state in {"closed", "archived", "draft"}:
                continue
            ext_id = str(item.get("id") or item.get("slug") or "").strip()
            title = str(item.get("title") or item.get("position") or "").strip()
            if not ext_id or not title:
                continue
            city = str(item.get("city") or "").strip()
            country = str(item.get("country") or "").strip()
            country_code = str(item.get("country_code") or "").strip()
            location = str(item.get("location") or "").strip()
            if not location:
                location = ", ".join(p for p in (city, country or country_code) if p)
            company = str(item.get("company_name") or slug).strip() or slug
            job_url = str(
                item.get("careers_url")
                or item.get("url")
                or item.get("apply_url")
                or ""
            ).strip()
            if not job_url:
                job_url = f"https://{slug}.recruitee.com/o/{ext_id}"
            remote = item.get("remote")
            hybrid = item.get("hybrid")
            is_remote = None
            if remote is True or hybrid is True:
                is_remote = True
            elif remote is False and hybrid is False:
                is_remote = False
            jobs.append(
                CollectedJob(
                    external_job_id=ext_id,
                    title=title,
                    company=company,
                    job_url=job_url,
                    location=location,
                    country=country,
                    country_code=country_code,
                    is_remote=is_remote,
                    description=str(item.get("description") or "")[:4000],
                    posted_date=_rt_date(item.get("published_at") or item.get("created_at")),
                    raw=item,
                )
            )

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=status)
        return CollectResult(jobs=jobs, status="ok", http_status=status)

    @staticmethod
    def _resolve_slug(board_identifier: str, careers_url: str | None) -> str:
        slug = (board_identifier or "").strip().strip("/")
        if slug and "://" not in slug and "/" not in slug and "." not in slug:
            return slug
        url = careers_url or board_identifier or ""
        if "://" not in url and slug.endswith(".recruitee.com"):
            return slug.split(".")[0]
        host = (urlparse(url if "://" in url else f"https://{url}").netloc or "").lower()
        if host.endswith(".recruitee.com"):
            return host.split(".")[0]
        return slug.split(".")[0] if slug else ""


def _rt_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None
