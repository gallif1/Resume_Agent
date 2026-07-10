"""Detect the candidate's professional domain and refine parsed CV profiles.

Rule-based skill matching can misclassify medical resumes (e.g. "Network" in
"Global Health Network", "/UI" in hospital abbreviations). This module
corrects those cases before role analysis and job collection run.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_MEDICAL_KEYWORDS = (
    "obstetrician",
    "gynaecologist",
    "gynecologist",
    "physician",
    "surgeon",
    "medical officer",
    "hospital consultant",
    "registrar",
    "house officer",
    "m.b.,b.s",
    "mbbs",
    "m.d.",
    "fwacs",
    "fellowship in surgery",
    "clinical",
    "patient care",
    "gynaecology",
    "gynecology",
    "obstetrics",
    "urogynaecology",
    "hysterectomy",
    "laparotomy",
    "medical practice",
    "רופא",
    "רופאה",
    "רפואה",
    "מנתח",
    "סטאז'",
    "סטאז",
    "רישום",
    "אחות",
    "אח",
    "סיעוד",
)

_TECH_ROLE_MARKERS = (
    "developer",
    "engineer",
    "programmer",
    "soc analyst",
    "it support",
    "devops",
    "full stack",
    "backend",
    "frontend",
    "data scientist",
    "cyber",
    "ux/ui",
    "software",
)

_MEDICAL_ROLES = (
    "Obstetrician and Gynaecologist",
    "Consultant Obstetrician",
    "Gynaecologist",
    "Medical Consultant",
    "Physician",
    "Surgeon",
    "Hospital Consultant",
    "Clinical Specialist",
    "Public Health Specialist",
    "Medical Officer",
)

_FALSE_TECH_SKILLS = {
    "cyber_security": {"Networking", "TCP/IP", "SOC", "Cybersecurity"},
    "design_creative": {"UI", "UX"},
}


def detect_domain(profile: dict[str, Any]) -> str:
    """Return ``medical``, ``tech``, or ``general`` for the parsed profile."""
    raw = str(profile.get("raw_text") or "").lower()
    summary = str((profile.get("sections") or {}).get("summary") or "").lower()
    blob = f"{raw}\n{summary}"

    medical_hits = sum(1 for kw in _MEDICAL_KEYWORDS if kw in blob)
    tech_skills = profile.get("skills") or {}
    if not isinstance(tech_skills, dict):
        tech_skills = {}
    tech_score = (
        len(tech_skills.get("programming_languages") or [])
        + len(tech_skills.get("frameworks_libraries") or [])
        + len(tech_skills.get("databases") or [])
    )

    if medical_hits >= 3:
        return "medical"
    if tech_score >= 2:
        return "tech"
    if medical_hits >= 1 and tech_score == 0:
        return "medical"
    return "general"


def _parse_embedded_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    try:
        parsed = ast.literal_eval(text)
        return parsed if isinstance(parsed, dict) else None
    except (SyntaxError, ValueError):
        return None


def _extract_name_from_raw(raw_text: str) -> str:
    patterns = [
        r"(?:curriculum vitae of|cv of|resume of)\s+(Dr\.?\s+[\w\s.'-]{3,60})",
        r"(Dr\.?\s+[A-Z][\w\s.'-]{3,60}?)(?:\n|\.|,|\s+M\.B)",
        r"^([A-Z][\w\s.'-]{4,50})\n",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE | re.MULTILINE)
        if match:
            name = re.sub(r"\s+", " ", match.group(1)).strip(" .,\n")
            name = re.sub(r"\s+M\.B\.?,?B\.?S\.?.*$", "", name, flags=re.IGNORECASE).strip(" .,")
            if name.lower() not in {"personal information", "curriculum vitae"}:
                return name
    return ""


def _looks_like_bad_job_titles(titles: list[str]) -> bool:
    if not titles:
        return True
    bad = 0
    for title in titles:
        t = str(title).strip().lower()
        if not t or t in {"february", "may", "august", "january"}:
            bad += 1
            continue
        if re.match(r"^[\W\d\s]+$", t):
            bad += 1
        if len(t) < 4:
            bad += 1
    return bad >= max(1, len(titles) // 2)


def _medical_roles_from_text(raw_text: str) -> list[str]:
    roles: list[str] = []
    patterns = [
        r"consultant\s+obstetrician(?:\s+and\s+gyna?ecologist)?",
        r"obstetrician(?:\s+and\s+gyna?ecologist)?",
        r"gyna?ecologist",
        r"hospital\s+consultant",
        r"senior\s+registrar",
        r"medical\s+officer",
        r"house\s+officer",
        r"physician",
        r"surgeon",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw_text, re.IGNORECASE):
            role = re.sub(r"\s+", " ", match.group(0)).strip().title()
            if role not in roles:
                roles.append(role)
    for role in _MEDICAL_ROLES:
        if role not in roles:
            roles.append(role)
        if len(roles) >= 8:
            break
    return roles[:8]


def _medical_strengths(profile: dict[str, Any]) -> list[str]:
    strengths: list[str] = []
    summary = str((profile.get("sections") or {}).get("summary") or "")
    raw = str(profile.get("raw_text") or "")
    blob = f"{summary}\n{raw}".lower()

    if any(k in blob for k in ("obstetric", "gynaecolog", "gynecolog")):
        strengths.append("Obstetrics and gynaecology")
    if "surgery" in blob or "surgical" in blob:
        strengths.append("Surgical procedures")
    if "urogynaecology" in blob or "urogynecology" in blob:
        strengths.append("Urogynaecology")
    if "teaching" in blob or "training" in blob:
        strengths.append("Medical teaching and training")
    if "research" in blob or "publication" in blob:
        strengths.append("Clinical research and publications")
    if "fistula" in blob:
        strengths.append("Obstetric fistula care")
    if "maternal" in blob:
        strengths.append("Maternal health")

    healthcare = (profile.get("skills") or {}).get("healthcare") or []
    for skill in healthcare[:4]:
        text = str(skill).strip()
        if text and text not in strengths:
            strengths.append(text)

    return strengths[:8]


def _strip_false_tech_skills(skills: dict[str, list[str]]) -> dict[str, list[str]]:
    cleaned = {key: list(values) for key, values in skills.items()}
    for category, remove in _FALSE_TECH_SKILLS.items():
        items = cleaned.get(category) or []
        cleaned[category] = [item for item in items if item not in remove]
    return cleaned


def _merge_healthcare_from_sections(profile: dict[str, Any]) -> dict[str, list[str]]:
    skills = profile.get("skills") or {}
    if not isinstance(skills, dict):
        skills = {}
    skills = {key: list(values or []) for key, values in skills.items()}

    embedded = _parse_embedded_dict((profile.get("sections") or {}).get("skills"))
    if embedded:
        for key, values in embedded.items():
            if key not in skills:
                skills[key] = []
            if isinstance(values, list):
                for item in values:
                    text = str(item).strip()
                    if text and text not in skills[key]:
                        skills[key].append(text)
    return skills


def refine_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Fix domain-specific parsing mistakes before saving or matching."""
    domain = detect_domain(profile)
    if domain != "medical":
        return profile

    refined = dict(profile)
    raw_text = str(profile.get("raw_text") or "")

    skills = _merge_healthcare_from_sections(refined)
    skills = _strip_false_tech_skills(skills)
    refined["skills"] = skills

    contact = dict(refined.get("contact") or {})
    name = str(contact.get("name") or "").strip()
    if not name or name.lower().startswith("personal information"):
        extracted = _extract_name_from_raw(raw_text)
        if extracted:
            contact["name"] = extracted
    refined["contact"] = contact

    experience = dict(refined.get("experience") or {})
    embedded_exp = _parse_embedded_dict((profile.get("sections") or {}).get("experience"))
    titles = list(experience.get("job_titles") or [])
    if embedded_exp and _looks_like_bad_job_titles(titles):
        experience.update(
            {
                "job_titles": embedded_exp.get("job_titles") or titles,
                "companies": embedded_exp.get("companies") or experience.get("companies") or [],
                "years_of_experience_estimate": embedded_exp.get("years_of_experience_estimate")
                or experience.get("years_of_experience_estimate"),
                "seniority_level": embedded_exp.get("seniority_level")
                or experience.get("seniority_level")
                or "senior",
                "management_experience": bool(
                    embedded_exp.get("management_experience")
                    or experience.get("management_experience")
                ),
                "internship_or_student_experience": bool(
                    embedded_exp.get("internship_or_student_experience")
                    or experience.get("internship_or_student_experience")
                ),
            }
        )
    elif experience.get("seniority_level") in {"", "unknown", None}:
        experience["seniority_level"] = "senior"
    refined["experience"] = experience

    education = dict(refined.get("education") or {})
    embedded_edu = _parse_embedded_dict((profile.get("sections") or {}).get("education"))
    if embedded_edu and not education.get("degrees"):
        education.update(
            {
                "degrees": embedded_edu.get("degrees") or [],
                "institutions": embedded_edu.get("institutions") or [],
                "fields_of_study": embedded_edu.get("fields_of_study") or [],
            }
        )
    refined["education"] = education

    medical_roles = _medical_roles_from_text(raw_text)
    insights = dict(refined.get("ai_insights") or {})
    recommended = insights.get("recommended_job_types") or []
    if isinstance(recommended, list):
        for role in recommended:
            text = str(role).strip()
            if text and text not in medical_roles:
                medical_roles.append(text)

    refined["best_fit_roles"] = medical_roles[:10]
    refined["strengths"] = _medical_strengths(refined)

    if not insights.get("recommended_job_types"):
        insights["recommended_job_types"] = medical_roles[:6]
    if not insights.get("professional_summary"):
        summary = str((profile.get("sections") or {}).get("summary") or "").strip()
        if summary:
            insights["professional_summary"] = summary[:600]
    refined["ai_insights"] = insights
    refined["primary_domain"] = "medical"
    return refined
