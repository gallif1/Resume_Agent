"""Comeet (Spark Hire Recruit) public careers collector.

Like Lever/Greenhouse/Workday: configure with a **public careers board URL**.
No private API key is required.

The public Comeet careers page embeds ``company_uid`` + public careers ``token``
in page HTML. We extract those and call the documented public Careers API.

Accepted ``board_identifier`` / ``careers_url`` forms:
- ``https://www.comeet.com/jobs/{slug}/{company_uid}``
- ``{slug}/{company_uid}``  e.g. ``weski/F8.00C``
- ``{company_uid}:{token}``  (explicit; still supported)
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from live_scanner.collectors.base import BaseCollector, CollectedJob, CollectResult
from live_scanner.collectors.http_util import http_json, http_text


class ComeetCollector(BaseCollector):
    provider = "comeet"

    def validate_config(
        self, *, board_identifier: str, careers_url: str | None = None
    ) -> str | None:
        if self._explicit_creds(board_identifier, careers_url):
            return None
        if self._board_page_ref(board_identifier, careers_url):
            return None
        return (
            "Comeet board_identifier must be a public careers URL "
            "(e.g. https://www.comeet.com/jobs/weski/F8.00C) "
            "or slug/uid (weski/F8.00C)"
        )

    def collect(self, *, board_identifier: str, careers_url: str | None = None) -> CollectResult:
        err = self.validate_config(
            board_identifier=board_identifier, careers_url=careers_url
        )
        if err:
            return CollectResult(status="error", error=err)

        try:
            company_uid, token = self._resolve_creds(board_identifier, careers_url)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(status="error", error=str(exc))

        if not company_uid or not token:
            return CollectResult(
                status="error",
                error=(
                    "Could not resolve Comeet public careers credentials from "
                    "the board URL (expected company_uid + token in page HTML)"
                ),
            )

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
                    loc.get("country_code") or loc.get("countryCode") or country
                ).strip()
                location = str(
                    loc.get("displayName") or loc.get("name") or ""
                ).strip()
                if not location:
                    location = ", ".join(p for p in (city, country or country_code) if p)
                if loc.get("is_remote") is not None:
                    is_remote = bool(loc.get("is_remote"))
                elif loc.get("remote") is not None:
                    is_remote = bool(loc.get("remote"))
            elif isinstance(loc, str):
                location = loc.strip()
            workplace = str(item.get("workplace_type") or "").lower()
            if "remote" in workplace:
                is_remote = True if is_remote is None else is_remote
            company = str(
                item.get("company_name")
                or (
                    (item.get("company") or {}).get("name")
                    if isinstance(item.get("company"), dict)
                    else None
                )
                or item.get("department")
                or company_uid
            ).strip()
            desc = str(item.get("description") or item.get("details") or "").strip()
            if not desc and isinstance(item.get("details"), list):
                parts = []
                for block in item["details"]:
                    if isinstance(block, dict) and block.get("value"):
                        parts.append(str(block["value"]))
                desc = "\n".join(parts)
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
                    posted_date=_comeet_date(
                        item.get("time_updated") or item.get("time_created")
                    ),
                    raw=item,
                )
            )

        if not jobs:
            return CollectResult(jobs=[], status="empty", http_status=status)
        return CollectResult(jobs=jobs, status="ok", http_status=status)

    def _resolve_creds(
        self, board_identifier: str, careers_url: str | None
    ) -> tuple[str, str]:
        explicit = self._explicit_creds(board_identifier, careers_url)
        if explicit:
            return explicit

        page_url = self._board_page_url(board_identifier, careers_url)
        if not page_url:
            return "", ""
        html, _status = http_text(page_url)
        uid = _match_group(r'"company_uid"\s*:\s*"([^"]+)"', html)
        token = _match_group(r'"token"\s*:\s*"([^"]+)"', html)
        if not uid or not token:
            raise RuntimeError(
                f"Comeet careers page did not expose public credentials: {page_url}"
            )
        return uid, token

    @staticmethod
    def _explicit_creds(
        board_identifier: str, careers_url: str | None
    ) -> tuple[str, str] | None:
        raw = (board_identifier or "").strip()
        # uid:token — but not URLs and not slug/uid paths with a single slash
        if ":" in raw and "://" not in raw and "/" not in raw:
            uid, token = raw.split(":", 1)
            uid, token = uid.strip(), token.strip()
            if uid and token and re.fullmatch(r"[A-Za-z0-9._-]+", uid):
                return uid, token

        for candidate in (raw, careers_url or ""):
            if not candidate or "://" not in candidate:
                continue
            parsed = urlparse(candidate)
            qs = parse_qs(parsed.query)
            token = (qs.get("token") or [""])[0].strip()
            m = re.search(r"/company/([^/]+)/", parsed.path or "")
            uid = m.group(1) if m else ""
            if uid and token:
                return uid, token
        return None

    @staticmethod
    def _board_page_ref(board_identifier: str, careers_url: str | None) -> bool:
        return bool(ComeetCollector._board_page_url(board_identifier, careers_url))

    @staticmethod
    def _board_page_url(board_identifier: str, careers_url: str | None) -> str | None:
        for candidate in ((careers_url or "").strip(), (board_identifier or "").strip()):
            if not candidate:
                continue
            if "://" in candidate:
                host = (urlparse(candidate).netloc or "").lower()
                path = urlparse(candidate).path or ""
                m = re.search(r"/jobs/([^/]+)/([A-Za-z0-9._-]+)", path)
                if m and "comeet." in host:
                    slug, uid = m.group(1), m.group(2)
                    return f"https://www.comeet.com/jobs/{slug}/{uid}"
                continue
            # slug/uid
            m = re.fullmatch(r"([A-Za-z0-9_-]+)/([A-Za-z0-9._-]+)", candidate.strip("/"))
            if m:
                return f"https://www.comeet.com/jobs/{m.group(1)}/{m.group(2)}"
        return None


def _match_group(pattern: str, text: str) -> str:
    m = re.search(pattern, text or "")
    return m.group(1).strip() if m else ""


def _comeet_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None
