"""Load and build per-CV search/match preferences.

All candidate data (name, contact, roles, location) is derived from the parsed
resume (``cv_profile.json``). Per-CV ``profile.json`` files are a cached view of
those preferences. The legacy global ``data/profile.json`` is only a last-resort
fallback when no parsed CV exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import (
    AGENT_CV_ID,
    AGENT_USER_ID,
    CV_PROFILE_PATH,
    LEGACY_CV_PROFILE_PATH,
    LEGACY_PROFILE_PATH,
    cv_data_dir,
    cv_profile_prefs_path,
    user_cv_profile_path,
    user_profile_prefs_path,
)
from cv_domain import detect_domain

_SENIOR_ROLE_KEYWORDS = (
    "senior",
    "lead",
    "manager",
    "architect",
    "principal",
    "head of",
    "director",
)


def profile_path_for_cv(cv_id: str | None = None) -> Path:
    uid = (AGENT_USER_ID or "").strip()
    if uid:
        return user_profile_prefs_path(uid)
    cid = (cv_id or AGENT_CV_ID or "").strip()
    if cid:
        return cv_profile_prefs_path(cid)
    return LEGACY_PROFILE_PATH


def save_profile_for_user(user_id: str, cv_profile: dict[str, Any]) -> Path:
    """Write user-level profile.json from aggregated resume data."""
    path = user_profile_prefs_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = build_profile_from_cv(cv_profile)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_profile_from_cv(cv_profile: dict[str, Any]) -> dict[str, Any]:
    """Derive search preferences solely from a parsed resume."""
    contact = cv_profile.get("contact") if isinstance(cv_profile.get("contact"), dict) else {}
    experience = (
        cv_profile.get("experience") if isinstance(cv_profile.get("experience"), dict) else {}
    )
    insights = cv_profile.get("ai_insights") if isinstance(cv_profile.get("ai_insights"), dict) else {}
    domain = str(cv_profile.get("primary_domain") or detect_domain(cv_profile))

    seniority = str(experience.get("seniority_level") or "").strip()
    is_student = bool(
        experience.get("internship_or_student_experience")
        or seniority.lower() in {"student", "intern", "graduate", "entry"}
        or "student" in seniority.lower()
        or "סטודנט" in seniority
    )

    roles: list[str] = []
    for source in (
        cv_profile.get("best_fit_roles"),
        insights.get("recommended_job_types"),
    ):
        if not isinstance(source, list):
            continue
        for role in source:
            text = str(role).strip()
            if text and text not in roles:
                roles.append(text)

    if domain == "medical":
        roles = [
            role
            for role in roles
            if not any(
                marker in role.lower()
                for marker in (
                    "developer",
                    "engineer",
                    "soc",
                    "it support",
                    "devops",
                    "ux",
                    "ui",
                    "cyber",
                    "software",
                    "programmer",
                )
            )
        ]
        if not roles:
            roles = [
                "Obstetrician and Gynaecologist",
                "Medical Consultant",
                "Physician",
                "Surgeon",
            ]

    if is_student:
        roles = [
            role
            for role in roles
            if not any(kw in role.lower() for kw in _SENIOR_ROLE_KEYWORDS)
        ]

    location = str(contact.get("location") or "").strip()
    if not location and domain == "medical":
        location = "Israel"
    elif not location:
        location = "Israel"

    min_score = 55 if is_student else (58 if domain == "medical" else 60)

    return {
        "full_name": str(contact.get("name") or "").strip(),
        "target_roles": roles[:6],
        "location": location,
        "remote": domain != "medical",
        "min_match_score": min_score,
        "seniority_level": seniority,
        "is_student": is_student,
        "primary_domain": domain,
    }


def save_profile_for_cv(cv_id: str, cv_profile: dict[str, Any]) -> Path:
    """Write per-CV profile.json from parsed resume data."""
    path = cv_profile_prefs_path(cv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = build_profile_from_cv(cv_profile)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _empty_profile_defaults() -> dict[str, Any]:
    return {
        "full_name": "",
        "target_roles": [],
        "location": "Israel",
        "remote": True,
        "min_match_score": 60,
    }


def _read_cv_profile(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def cv_profile_path_for(cv_id: str | None = None) -> Path:
    """Resolved cv_profile.json for the active user, CV, or legacy context."""
    uid = (AGENT_USER_ID or "").strip()
    if uid:
        return user_cv_profile_path(uid)
    cid = (cv_id or AGENT_CV_ID or "").strip()
    if cid:
        return cv_data_dir(cid) / "cv_profile.json"
    return LEGACY_CV_PROFILE_PATH


def load_cv_profile(cv_id: str | None = None) -> dict[str, Any]:
    """Load the parsed resume profile for the active CV context."""
    path = cv_profile_path_for(cv_id)
    if path == LEGACY_CV_PROFILE_PATH and CV_PROFILE_PATH != LEGACY_CV_PROFILE_PATH:
        path = CV_PROFILE_PATH
    return _read_cv_profile(path) or {}


def load_cv_contact(cv_id: str | None = None) -> dict[str, str]:
    """Contact details extracted from the resume under review."""
    contact = load_cv_profile(cv_id).get("contact")
    if not isinstance(contact, dict):
        return {}
    return {
        key: str(contact.get(key) or "").strip()
        for key in ("name", "email", "phone", "location", "linkedin", "github", "portfolio")
    }


def load_profile() -> dict[str, Any]:
    """Load search/match preferences derived from the resume under review."""
    uid = (AGENT_USER_ID or "").strip()
    if uid:
        prefs_path = user_profile_prefs_path(uid)
        if prefs_path.exists():
            try:
                with open(prefs_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        cv_profile = _read_cv_profile(user_cv_profile_path(uid))
        if cv_profile:
            return build_profile_from_cv(cv_profile)
        return _empty_profile_defaults()

    cid = (AGENT_CV_ID or "").strip()

    if cid:
        prefs_path = cv_profile_prefs_path(cid)
        if prefs_path.exists():
            try:
                with open(prefs_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        cv_profile = _read_cv_profile(cv_data_dir(cid) / "cv_profile.json")
        if cv_profile:
            return build_profile_from_cv(cv_profile)
        return _empty_profile_defaults()

    prefs_path = profile_path_for_cv()
    if prefs_path.exists() and prefs_path != LEGACY_PROFILE_PATH:
        try:
            with open(prefs_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    cv_profile = load_cv_profile()
    if cv_profile:
        return build_profile_from_cv(cv_profile)

    if LEGACY_PROFILE_PATH.exists():
        try:
            with open(LEGACY_PROFILE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    return _empty_profile_defaults()
