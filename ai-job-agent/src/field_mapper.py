"""Normalized field mapping for job application forms."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Canonical field keys and their label/name/placeholder synonyms.
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "first_name": (
        "first name",
        "firstname",
        "first-name",
        "given name",
        "givenname",
        "fname",
        "שם פרטי",
    ),
    "last_name": (
        "last name",
        "lastname",
        "last-name",
        "surname",
        "family name",
        "familyname",
        "lname",
        "שם משפחה",
    ),
    "full_name": (
        "full name",
        "fullname",
        "name",
        "your name",
        "שם מלא",
        "שם",
    ),
    "email": (
        "email",
        "email address",
        "e-mail",
        "mail",
        "דוא\"ל",
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
    "location": (
        "location",
        "city",
        "address",
        "current location",
        "עיר",
        "מיקום",
        "כתובת",
    ),
    "linkedin": (
        "linkedin",
        "linkedin profile",
        "linkedin url",
        "לינקדאין",
    ),
    "github": (
        "github",
        "github profile",
        "github url",
        "גיטהאב",
    ),
    "portfolio": (
        "portfolio",
        "website",
        "personal website",
        "personal site",
        "url",
        "אתר",
        "פורטפוליו",
    ),
    "current_title": (
        "current title",
        "current job title",
        "job title",
        "position",
        "current position",
        "תפקיד נוכחי",
        "תפקיד",
    ),
    "experience": (
        "experience",
        "work experience",
        "employment history",
        "professional experience",
        "ניסיון",
        "ניסיון תעסוקתי",
    ),
    "education": (
        "education",
        "academic background",
        "השכלה",
    ),
    "skills": (
        "skills",
        "technical skills",
        "כישורים",
        "מיומנויות",
    ),
    "salary": (
        "salary",
        "salary expectations",
        "expected salary",
        "desired salary",
        "compensation",
        "שכר",
        "ציפיות שכר",
    ),
    "work_authorization": (
        "work authorization",
        "work eligibility",
        "authorized to work",
        "legal right to work",
        "visa status",
        "היתר עבודה",
        "אזרחות",
    ),
    "cover_letter": (
        "cover letter",
        "motivation",
        "motivation letter",
        "letter",
        "מכתב מקדים",
        "מכתב נלווה",
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
    """Lowercase, strip accents, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\"']+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def match_field_key(blob: str) -> str | None:
    """Return the canonical field key if blob matches a known synonym."""
    normalized = normalize_label(blob)
    if not normalized:
        return None
    for key, synonyms in FIELD_SYNONYMS.items():
        for synonym in synonyms:
            syn = normalize_label(synonym)
            if not syn:
                continue
            if syn == normalized or syn in normalized or normalized in syn:
                return key
    return None


def build_profile_values(profile: dict[str, Any]) -> dict[str, str]:
    """Map a user/CV profile dict to canonical field values."""
    contact = profile.get("contact") if isinstance(profile.get("contact"), dict) else {}
    experience = (
        profile.get("experience") if isinstance(profile.get("experience"), dict) else {}
    )
    sections = profile.get("sections") if isinstance(profile.get("sections"), dict) else {}
    prefs = profile.get("preferences") if isinstance(profile.get("preferences"), dict) else {}

    full_name = str(contact.get("name") or profile.get("full_name") or "").strip()
    parts = full_name.split(None, 1) if full_name else []
    first_name = parts[0] if parts else ""
    last_name = parts[1] if len(parts) > 1 else ""

    job_titles = experience.get("job_titles")
    current_title = ""
    if isinstance(job_titles, list) and job_titles:
        current_title = str(job_titles[0]).strip()

    skills_blob = ""
    skills = profile.get("skills")
    if isinstance(skills, dict):
        flat: list[str] = []
        for values in skills.values():
            if isinstance(values, list):
                flat.extend(str(v) for v in values if v)
        skills_blob = ", ".join(flat[:30])
    elif isinstance(skills, list):
        skills_blob = ", ".join(str(s) for s in skills[:30])

    exp_text = ""
    if sections.get("experience"):
        exp_text = str(sections["experience"])[:4000]
    elif experience.get("companies"):
        companies = experience.get("companies")
        if isinstance(companies, list):
            exp_text = "; ".join(str(c) for c in companies[:10])

    edu_text = ""
    if sections.get("education"):
        edu_text = str(sections["education"])[:2000]
    else:
        education = profile.get("education")
        if isinstance(education, dict):
            degrees = education.get("degrees") or education.get("institutions")
            if isinstance(degrees, list):
                edu_text = "; ".join(str(d) for d in degrees[:10])

    values: dict[str, str] = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "email": str(contact.get("email") or "").strip(),
        "phone": str(contact.get("phone") or "").strip(),
        "location": str(contact.get("location") or prefs.get("location") or "").strip(),
        "linkedin": str(contact.get("linkedin") or "").strip(),
        "github": str(contact.get("github") or "").strip(),
        "portfolio": str(contact.get("portfolio") or "").strip(),
        "current_title": current_title,
        "experience": exp_text,
        "education": edu_text,
        "skills": skills_blob,
    }

    salary = str(prefs.get("salary_expectations") or "").strip()
    if salary:
        values["salary"] = salary

    work_auth = str(prefs.get("work_authorization") or "").strip()
    if work_auth:
        values["work_authorization"] = work_auth

    cover = str(profile.get("cover_letter") or prefs.get("cover_letter") or "").strip()
    if cover:
        values["cover_letter"] = cover

    return {k: v for k, v in values.items() if v}


def field_blob_from_element(attrs: dict[str, str | None], label_text: str = "") -> str:
    """Combine element attributes and nearby label text for matching."""
    parts = [
        attrs.get("name") or "",
        attrs.get("id") or "",
        attrs.get("placeholder") or "",
        attrs.get("aria-label") or "",
        attrs.get("autocomplete") or "",
        label_text,
    ]
    return " ".join(p for p in parts if p)
