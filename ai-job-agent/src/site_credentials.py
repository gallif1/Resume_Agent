"""Per-CV site login credentials stored on disk (user-provided via profile page)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import (
    DRUSHIM_EMAIL,
    DRUSHIM_PASSWORD,
    LINKEDIN_EMAIL,
    LINKEDIN_PASSWORD,
    cv_data_dir,
)

SITE_CREDENTIALS_FILENAME = "site_credentials.json"
SUPPORTED_SITES = ("linkedin", "drushim")


def site_credentials_path(cv_id: str) -> Path:
    return cv_data_dir(cv_id) / SITE_CREDENTIALS_FILENAME


def _empty_record() -> dict[str, dict[str, str]]:
    return {site: {"email": "", "password": ""} for site in SUPPORTED_SITES}


def load_site_credentials(cv_id: str) -> dict[str, dict[str, str]]:
    path = site_credentials_path(cv_id)
    if not path.is_file():
        return _empty_record()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_record()
    result = _empty_record()
    for site in SUPPORTED_SITES:
        entry = raw.get(site) if isinstance(raw, dict) else None
        if not isinstance(entry, dict):
            continue
        result[site]["email"] = str(entry.get("email") or "").strip()
        result[site]["password"] = str(entry.get("password") or "")
    return result


def save_site_credentials(cv_id: str, data: dict[str, dict[str, str]]) -> None:
    path = site_credentials_path(cv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def public_site_credentials(cv_id: str) -> dict[str, dict[str, Any]]:
    """API-safe view — never exposes stored passwords."""
    stored = load_site_credentials(cv_id)
    public: dict[str, dict[str, Any]] = {}
    for site in SUPPORTED_SITES:
        email = stored[site]["email"]
        password = stored[site]["password"]
        public[site] = {
            "email": email,
            "password_set": bool(password),
            "configured": bool(email and password),
        }
    return public


def update_site_credentials(
    cv_id: str,
    *,
    linkedin: dict[str, str] | None = None,
    drushim: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge user updates. Empty password keeps the existing stored password."""
    current = load_site_credentials(cv_id)

    def _merge(site: str, patch: dict[str, str] | None) -> None:
        if patch is None:
            return
        email = str(patch.get("email") or "").strip()
        password = patch.get("password")
        current[site]["email"] = email
        if password is not None and str(password).strip():
            current[site]["password"] = str(password)

    _merge("linkedin", linkedin)
    _merge("drushim", drushim)
    save_site_credentials(cv_id, current)
    return public_site_credentials(cv_id)


def get_linkedin_credentials(cv_id: str) -> tuple[str, str]:
    stored = load_site_credentials(cv_id)["linkedin"]
    email = stored["email"] or LINKEDIN_EMAIL
    password = stored["password"] or LINKEDIN_PASSWORD
    return email.strip(), password


def get_drushim_credentials(cv_id: str) -> tuple[str, str]:
    stored = load_site_credentials(cv_id)["drushim"]
    email = stored["email"] or DRUSHIM_EMAIL
    password = stored["password"] or DRUSHIM_PASSWORD
    return email.strip(), password


def linkedin_credentials_configured(cv_id: str | None = None) -> bool:
    if cv_id:
        email, password = get_linkedin_credentials(cv_id)
        return bool(email and password)
    return bool(LINKEDIN_EMAIL and LINKEDIN_PASSWORD)


def drushim_credentials_configured(cv_id: str | None = None) -> bool:
    if cv_id:
        email, password = get_drushim_credentials(cv_id)
        return bool(email and password)
    return bool(DRUSHIM_EMAIL and DRUSHIM_PASSWORD)
