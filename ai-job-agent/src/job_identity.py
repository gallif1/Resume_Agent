"""Stable job identity and content hashing for incremental pipeline processing."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

DRUSHIM_JOB_ID_RE = re.compile(r"/job/(\d+)(?:/|$)", re.IGNORECASE)

# LinkedIn job URLs: /jobs/view/{id} or /jobs/view/{slug}-{id}, plus ?currentJobId={id}.
LINKEDIN_JOB_ID_RE = re.compile(
    r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})(?:[/?#]|$)", re.IGNORECASE
)
LINKEDIN_CURRENT_JOB_ID_RE = re.compile(r"[?&]currentJobId=(\d{6,})", re.IGNORECASE)

# GotFriends job URLs: /jobslobby/{category}/{profession?}/{id}/ or .../{slug}-{id}/
GOTFRIENDS_JOB_ID_RE = re.compile(
    r"/jobslobby/[^/]+(?:/[^/]+)?/(?:[^/]*?-)?(\d+)(?:-\d+)?/?",
    re.IGNORECASE,
)

ALLJOBS_JOB_ID_RE = re.compile(r"(?:[?&]JobID=|/JobID/)(\d+)", re.IGNORECASE)
INDEED_JOB_KEY_RE = re.compile(r"(?:[?&]jk=|/viewjob\?jk=)([a-f0-9]+)", re.IGNORECASE)
SECRET_TEL_AVIV_JOB_SLUG_RE = re.compile(r"/job/([^/?#]+)/?", re.IGNORECASE)
GEEKTIME_JOB_ID_RE = re.compile(r"(?:jid|job[_-]?id|id_job)=(\d+)", re.IGNORECASE)

# Query params stripped from job URLs (tracking / session noise).
_TRACKING_QUERY_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "ref", "source",
})


def extract_drushim_job_id(url: str | None) -> str | None:
    """Return the numeric Drushim job id from a job page URL, if present."""
    if not url:
        return None
    match = DRUSHIM_JOB_ID_RE.search(url)
    return match.group(1) if match else None


def extract_linkedin_job_id(url: str | None) -> str | None:
    """Return the numeric LinkedIn job id from a job page URL, if present."""
    if not url:
        return None
    match = LINKEDIN_JOB_ID_RE.search(url)
    if match:
        return match.group(1)
    match = LINKEDIN_CURRENT_JOB_ID_RE.search(url)
    return match.group(1) if match else None


def extract_gotfriends_job_id(url: str | None) -> str | None:
    """Return the numeric GotFriends job id from a job page URL, if present."""
    if not url:
        return None
    match = GOTFRIENDS_JOB_ID_RE.search(url)
    return match.group(1) if match else None


def extract_alljobs_job_id(url: str | None) -> str | None:
    if not url:
        return None
    match = ALLJOBS_JOB_ID_RE.search(url)
    return match.group(1) if match else None


def extract_indeed_job_key(url: str | None) -> str | None:
    if not url:
        return None
    match = INDEED_JOB_KEY_RE.search(url)
    return match.group(1) if match else None


def extract_secret_tel_aviv_job_slug(url: str | None) -> str | None:
    if not url:
        return None
    match = SECRET_TEL_AVIV_JOB_SLUG_RE.search(url)
    return match.group(1) if match else None


def extract_geektime_job_id(url: str | None) -> str | None:
    if not url:
        return None
    match = GEEKTIME_JOB_ID_RE.search(url)
    return match.group(1) if match else None


def normalize_job_url(url: str | None) -> str:
    """Canonical job URL: stable host/path, no tracking params, no trailing slash."""
    if not url or not str(url).strip():
        return ""

    raw = str(url).strip()
    if raw.startswith("/"):
        raw = f"https://www.drushim.co.il{raw}"

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "www.drushim.co.il").lower()
    if netloc == "drushim.co.il":
        netloc = "www.drushim.co.il"

    path = parsed.path or ""
    path = re.sub(r"/+$", "", path) or "/"

    # LinkedIn job pages: canonical form is www.linkedin.com/jobs/view/{id}.
    if "linkedin.com" in netloc:
        linkedin_id = extract_linkedin_job_id(raw)
        if linkedin_id:
            return f"https://www.linkedin.com/jobs/view/{linkedin_id}"

    # GotFriends job pages: keep category/profession path, drop duplicate suffixes (-1).
    if "gotfriends.co.il" in netloc:
        gotfriends_id = extract_gotfriends_job_id(raw)
        if gotfriends_id:
            path_match = re.search(
                rf"(/jobslobby/[^/]+(?:/[^/]+)?)/(?:[^/]*?-)?{re.escape(gotfriends_id)}(?:-\d+)?/?",
                path,
                re.IGNORECASE,
            )
            if path_match:
                path = f"{path_match.group(1)}/{gotfriends_id}"
            else:
                path = re.sub(
                    rf"/(?:[^/]*?-)?{re.escape(gotfriends_id)}(?:-\d+)?/?$",
                    f"/{gotfriends_id}",
                    path,
                )

    # AllJobs detail pages: canonical UploadSingle?JobID=
    if "alljobs.co.il" in netloc:
        alljobs_id = extract_alljobs_job_id(raw)
        if alljobs_id:
            return f"https://www.alljobs.co.il/Search/UploadSingle.aspx?JobID={alljobs_id}"

    # Indeed Israel: canonical viewjob?jk=
    if "indeed.com" in netloc:
        indeed_jk = extract_indeed_job_key(raw)
        if indeed_jk:
            host = "il.indeed.com" if netloc.startswith("il.") else netloc
            return f"https://{host}/viewjob?jk={indeed_jk}"

    # Secret Tel Aviv job pages
    if "secrettelaviv.com" in netloc:
        slug = extract_secret_tel_aviv_job_slug(path)
        if slug:
            return f"https://jobs.secrettelaviv.com/job/{slug}"

    # Geektime Insider deep links
    if "geektime.co.il" in netloc:
        geektime_id = extract_geektime_job_id(raw)
        if geektime_id:
            return f"https://insider.geektime.co.il/jobs/#jid={geektime_id}"

    # Drushim job pages: keep only /job/{id}/{token} — drop extra path segments.
    drushim_id = extract_drushim_job_id(path)
    if drushim_id:
        token_match = re.search(
            rf"/job/{re.escape(drushim_id)}/([^/?#]+)",
            path,
            re.IGNORECASE,
        )
        token = token_match.group(1) if token_match else ""
        path = f"/job/{drushim_id}/{token}" if token else f"/job/{drushim_id}"
        path = re.sub(r"/+$", "", path)

    clean_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_PARAMS
    ]
    query = urlencode(clean_params, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))


def compute_job_identity_key(
    job_url: str | None,
    title: str = "",
    company: str = "",
    location: str = "",
) -> str:
    """Stable identity for deduplication across queries, categories, and URL variants."""
    canonical_url = normalize_job_url(job_url)
    drushim_id = extract_drushim_job_id(canonical_url)
    if drushim_id and "linkedin.com" not in canonical_url:
        return f"drushim:job:{drushim_id}"

    linkedin_id = extract_linkedin_job_id(canonical_url)
    if linkedin_id:
        return f"linkedin:job:{linkedin_id}"

    gotfriends_id = extract_gotfriends_job_id(canonical_url)
    if gotfriends_id:
        return f"gotfriends:job:{gotfriends_id}"

    alljobs_id = extract_alljobs_job_id(canonical_url)
    if alljobs_id:
        return f"alljobs:job:{alljobs_id}"

    indeed_jk = extract_indeed_job_key(canonical_url)
    if indeed_jk:
        return f"indeed:job:{indeed_jk}"

    sta_slug = extract_secret_tel_aviv_job_slug(canonical_url)
    if sta_slug and "secrettelaviv.com" in canonical_url:
        return f"secret_tel_aviv:job:{sta_slug}"

    geektime_id = extract_geektime_job_id(canonical_url)
    if geektime_id:
        return f"geektime:job:{geektime_id}"

    if canonical_url:
        return f"url:{hashlib.sha256(canonical_url.encode('utf-8')).hexdigest()[:32]}"

    key = "|".join(
        part.lower().strip()
        for part in (title or "", company or "", location or "")
        if part and str(part).strip()
    )
    if key:
        return f"meta:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}"
    return "unknown:empty"


def compute_job_hash(
    job_url: str | None,
    title: str = "",
    company: str = "",
    location: str = "",
) -> str:
    """Unique job identity stored in DB — same as compute_job_identity_key."""
    return compute_job_identity_key(job_url, title, company, location)


def compute_job_content_hash(
    title: str = "",
    company: str = "",
    location: str = "",
    description: str = "",
    full_description: str = "",
) -> str:
    """Hash of job text used to detect description changes."""
    content = "\n".join([
        (title or "").strip(),
        (company or "").strip(),
        (location or "").strip(),
        (description or "").strip(),
        (full_description or "").strip(),
    ])
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_candidate_input_hash(
    profile: dict[str, Any],
    cv_profile: dict[str, Any],
) -> str:
    """Hash of candidate inputs used to decide if role/strategy analysis should re-run."""
    universal = cv_profile.get("universal_profile")
    universal_fingerprint: dict[str, Any] | None = None
    if isinstance(universal, dict):
        # Include search-driving fields so a rebuilt universal profile invalidates
        # the matching strategy even when raw skill lists look similar.
        universal_fingerprint = {
            key: universal.get(key)
            for key in (
                "canonical_roles",
                "preferred_role_titles",
                "alternative_role_titles",
                "search_keywords_en",
                "search_keywords_he",
                "technologies_tools",
                "domain_keywords",
                "exclusion_keywords",
                "seniority_level",
                "collection_queries",
                "candidate_summary",
            )
        }

    payload = {
        "profile": {key: profile.get(key) for key in sorted(profile.keys())},
        "cv_skills": cv_profile.get("skills"),
        "cv_experience": cv_profile.get("experience"),
        "cv_education": cv_profile.get("education"),
        "cv_contact": cv_profile.get("contact"),
        "cv_projects": cv_profile.get("projects"),
        "cv_certifications": cv_profile.get("certifications"),
        "universal_profile": universal_fingerprint,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_candidate_strategy_hash(
    profile: dict[str, Any],
    strategy: dict[str, Any],
) -> str:
    """Stable hash when profile preferences, matching strategy, or algorithm change."""
    from ats_scorer import ATS_SCORER_VERSION

    payload = {
        "ats_scorer_version": ATS_SCORER_VERSION,
        "profile": {key: profile.get(key) for key in sorted(profile.keys())},
        "strategy_analyzed_at": strategy.get("analyzed_at"),
        "strategy_source": strategy.get("source"),
        "best_fit_roles": strategy.get("best_fit_roles"),
        "job_categories": strategy.get("job_categories"),
        "collection_queries": strategy.get("collection_queries"),
        "global_reject_rules": strategy.get("global_reject_rules"),
        "seniority_filters": strategy.get("seniority_filters"),
        "skill_weights": strategy.get("skill_weights"),
        "location_preferences": strategy.get("location_preferences"),
        "application_priority_rules": strategy.get("application_priority_rules"),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
