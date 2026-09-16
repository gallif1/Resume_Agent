"""Workday CXS jobs API collector."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json


class WorkdayCollector(BaseCollector):
    """Collect via Workday Candidate Experience Service (CXS) JSON API.

    board_identifier formats accepted:
    - ``{tenant}.{host}/{site}`` e.g. ``nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite``
    - full careers URL (parsed)
    - ``tenant|host|site`` pipe-separated
    """

    provider = "workday"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        parsed = self._parse(board_identifier, careers_url)
        if not parsed:
            return (
                "Workday config requires tenant.host/site "
                "(e.g. nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite)"
            )
        return None

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        parsed = self._parse(board_identifier, careers_url)
        err = self.validate_config(board_identifier=board_identifier, careers_url=careers_url)
        if err or not parsed:
            return CollectResult(status="error", error=err or "Invalid Workday config")

        tenant, host, site = parsed
        api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        jobs: list[CollectedJob] = []
        offset = 0
        limit = 20
        last_status = None
        company = tenant.replace("_", " ").title()

        while True:
            body = {
                "appliedFacets": {},
                "limit": limit,
                "offset": offset,
                "searchText": "",
            }
            try:
                data, status = http_json(api, method="POST", body=body)
                last_status = status
            except Exception as exc:  # noqa: BLE001
                if jobs:
                    # Partial success — return what we have
                    break
                return CollectResult(status="error", error=str(exc))

            if not isinstance(data, dict):
                return CollectResult(
                    status="error",
                    error="Unexpected Workday response shape",
                    http_status=status,
                )

            postings = data.get("jobPostings") or []
            if not isinstance(postings, list) or not postings:
                break

            for item in postings:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                external_path = str(item.get("externalPath") or "").strip()
                if not title or not external_path:
                    continue
                # Stable id from path segment (often contains job requisition id)
                ext_id = _workday_external_id(external_path, item)
                job_url = f"https://{host}/{site}{external_path}"
                location = str(item.get("locationsText") or item.get("bulletFields") or "").strip()
                if isinstance(item.get("bulletFields"), list):
                    location = ", ".join(str(x) for x in item["bulletFields"] if x)
                jobs.append(
                    CollectedJob(
                        external_job_id=ext_id,
                        title=title,
                        company=company,
                        job_url=job_url,
                        location=location,
                        description="",
                        posted_date=_wd_date(item.get("postedOn") or item.get("postedOnText")),
                        raw=item,
                    )
                )

            total = data.get("total")
            offset += limit
            if total is not None:
                try:
                    if offset >= int(total):
                        break
                except (TypeError, ValueError):
                    pass
            if len(postings) < limit:
                break
            # Safety cap for very large tenants during live scanning
            if offset >= 500:
                break

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=last_status)
        return CollectResult(jobs=jobs, status="ok", http_status=last_status)

    @staticmethod
    def _parse(
        board_identifier: str, careers_url: str | None
    ) -> tuple[str, str, str] | None:
        raw = (board_identifier or "").strip()
        url = (careers_url or "").strip()

        if "|" in raw:
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) == 3 and all(parts):
                tenant, host, site = parts
                return tenant, host, site.strip("/")

        candidate = raw if "://" in raw or ".myworkday" in raw else url or raw
        if not candidate:
            return None

        if "://" not in candidate:
            # tenant.host/site
            if "/" not in candidate:
                return None
            host_part, site = candidate.split("/", 1)
            site = site.strip("/")
            host = host_part.strip()
            tenant = host.split(".")[0] if host else ""
            if tenant and host and site:
                return tenant, host, site
            return None

        parsed = urlparse(candidate)
        host = (parsed.netloc or "").lower()
        if "myworkdayjobs.com" not in host and "myworkdaysite.com" not in host:
            return None
        tenant = host.split(".")[0]
        parts = [p for p in (parsed.path or "").split("/") if p]
        # Drop common CXS prefixes if present
        while parts and parts[0] in {"wday", "cxs", tenant}:
            if parts[0] == "cxs" and len(parts) > 1:
                parts = parts[1:]
                if parts and parts[0] == tenant:
                    parts = parts[1:]
                break
            parts = parts[1:]
        site = parts[0] if parts else ""
        if tenant and host and site:
            return tenant, host, site
        return None


def _workday_external_id(external_path: str, item: dict) -> str:
    for key in ("bulletFields",):
        pass
    # Prefer trailing path segment as id
    seg = external_path.rstrip("/").split("/")[-1]
    if seg:
        # Often like "Job-Name_R12345"
        m = re.search(r"(_R\d+|_\d{4,}|\d{5,})", seg)
        if m:
            return seg
        return seg
    return external_path


def _wd_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    # "Posted 3 Days Ago" is not a date — skip
    if "ago" in text.lower() or "posted" in text.lower():
        return None
    return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else None
