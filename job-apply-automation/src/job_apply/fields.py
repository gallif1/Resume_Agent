"""Field label matching for job application forms (EN + HE)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "first_name": (
        "first name",
        "firstname",
        "first-name",
        "given name",
        "fname",
        "שם פרטי",
    ),
    "last_name": (
        "last name",
        "lastname",
        "last-name",
        "surname",
        "family name",
        "lname",
        "שם משפחה",
    ),
    "full_name": (
        "full name",
        "fullname",
        "your name",
        "שם מלא",
        "שם",
    ),
    "email": (
        "email",
        "email address",
        "e-mail",
        "e mail",
        "mail",
        'דוא"ל',
        "דואל",
        "אימייל",
        "מייל",
    ),
    "phone": (
        "phone",
        "mobile",
        "telephone",
        "tel",
        "cell",
        "phone number",
        "mobile number",
        "טלפון",
        "נייד",
        "פלאפון",
        "מספר טלפון",
    ),
    "cv_file": (
        "resume",
        "cv",
        "curriculum vitae",
        "upload resume",
        "upload cv",
        "attach resume",
        "קורות חיים",
        "העלאת קורות חיים",
    ),
}


def normalize_label(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\"']+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def match_field_key(blob: str) -> str | None:
    normalized = normalize_label(blob)
    if not normalized:
        return None
    # Prefer more specific keys before generic "name" / full_name.
    preferred_order = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "cv_file",
        "full_name",
    )
    for key in preferred_order:
        for synonym in FIELD_SYNONYMS[key]:
            syn = normalize_label(synonym)
            if not syn:
                continue
            if syn == normalized or syn in normalized or normalized in syn:
                return key
    return None


def field_blob_from_element(attrs: dict[str, str | None], label_text: str = "") -> str:
    parts = [
        attrs.get("name") or "",
        attrs.get("id") or "",
        attrs.get("placeholder") or "",
        attrs.get("aria-label") or "",
        attrs.get("autocomplete") or "",
        label_text,
    ]
    return " ".join(p for p in parts if p)


def build_profile_values(profile: dict[str, Any]) -> dict[str, str]:
    first = str(profile.get("first_name") or "").strip()
    last = str(profile.get("last_name") or "").strip()
    full = str(profile.get("full_name") or "").strip()
    if not full and (first or last):
        full = f"{first} {last}".strip()
    if full and (not first or not last):
        parts = full.split(None, 1)
        first = first or (parts[0] if parts else "")
        last = last or (parts[1] if len(parts) > 1 else "")

    values = {
        "first_name": first,
        "last_name": last,
        "full_name": full,
        "email": str(profile.get("email") or "").strip(),
        "phone": str(profile.get("phone") or "").strip(),
    }
    return {k: v for k, v in values.items() if v}
