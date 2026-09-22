"""SmartRecruiters public postings API collector."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json, path_of


class SmartRecruitersCollector(BaseCollector):
    """GET https://api.smartrecruiters.com/v1/companies/{company}/postings"""

    provider = "smartrecruiters"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        slug = self._resolve_slug(board_identifier, careers_url)
        if not slug:
            return "SmartRecruiters board_identifier (company slug) is required"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
            return f"Invalid SmartRecruiters company slug: {slug!r}"
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        slug = self._resolve_slug(board_identifier, careers_url)
        err = self.validate_config(board_identifier=slug or board_identifier, careers_url=careers_url)
        if err:
            return CollectResult(status="error", error=err)

        jobs: list[CollectedJob] = []
        offset = 0
        limit = 100
        last_status = None
        company_name = slug

        while offset < 1000:
            url = (
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                f"?limit={limit}&offset={offset}"
            )
            try:
                data, status = http_json(url)
                last_status = status
            except Exception as exc:  # noqa: BLE001
                if jobs:
                    break
                return CollectResult(status="error", error=str(exc))

            if not isinstance(data, dict):
                return CollectResult(
                    status="error",
                    error="Unexpected SmartRecruiters response shape",
                    http_status=status,
                )

            content = data.get("content") or []
            if not isinstance(content, list) or not content:
                break

            for item in content:
                if not isinstance(item, dict):
                    continue
                ext_id = str(item.get("id") or item.get("uuid") or "").strip()
                title = str(item.get("name") or item.get("title") or "").strip()
                if not ext_id or not title:
                    continue
                loc = item.get("location") if isinstance(item.get("location"), dict) else {}
                city = str(loc.get("city") or "").strip()
                full = str(loc.get("fullLocation") or "").strip()
                country = str(loc.get("country") or "").strip()
                country_code = country
                location = full or ", ".join(p for p in (city, country) if p)
                company_obj = item.get("company") if isinstance(item.get("company"), dict) else {}
                company_name = str(company_obj.get("name") or slug).strip() or slug
                ref = str(item.get("refNumber") or ext_id)
                job_url = (
                    f"https://jobs.smartrecruiters.com/{slug}/{ref}"
                    if slug and ref
                    else f"https://jobs.smartrecruiters.com/{ext_id}"
                )
                # Prefer apply URL when present
                apply_url = ""
                actions = item.get("actions")
                if isinstance(actions, dict):
                    details = actions.get("details")
                    if isinstance(details, str):
                        apply_url = details
                jobs.append(
                    CollectedJob(
                        external_job_id=ext_id,
                        title=title,
                        company=company_name,
                        job_url=apply_url or job_url,
                        location=location,
                        country=country,
                        country_code=country_code,
                        is_remote=bool(loc.get("remote")) if "remote" in loc else None,
                        posted_date=_sr_date(item.get("releasedDate")),
                        raw=item,
                    )
                )

            total = data.get("totalFound")
            offset += limit
            if total is not None:
                try:
                    if offset >= int(total):
                        break
                except (TypeError, ValueError):
                    pass
            if len(content) < limit:
                break

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=last_status)
        return CollectResult(jobs=jobs, status="ok", http_status=last_status)

    @staticmethod
    def _resolve_slug(board_identifier: str, careers_url: str | None) -> str:
        slug = (board_identifier or "").strip().strip("/")
        if slug and "://" not in slug and "/" not in slug:
            return slug
        url = careers_url or board_identifier or ""
        host = (urlparse(url).netloc or "").lower()
        path = path_of(url)
        parts = [p for p in path.split("/") if p]
        # jobs.smartrecruiters.com/{company}/...
        if "smartrecruiters.com" in host and parts:
            return parts[0]
        return slug.split("/")[0] if slug else ""


def _sr_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None
