"""OpenAI-powered resume analysis.

Extracts structured profile data and career insights from resume text.
Requires OPENAI_API_KEY in .env — otherwise parse_cv.py uses rule-based parsing only.
"""

from __future__ import annotations

import json
import re
from typing import Any

from config import OPENAI_API_KEY, OPENAI_CV_MAX_CHARS, OPENAI_MODEL
from skills import SKILL_CATEGORIES

SENIORITY_LEVELS = {
    "intern", "student", "junior", "mid", "senior", "lead", "manager", "unknown",
}

SECTION_KEYS = [
    "summary", "experience", "education", "skills", "projects",
    "certifications", "languages", "military_service", "volunteering",
    "awards", "other",
]

SKILL_CATEGORY_KEYS = list(SKILL_CATEGORIES.keys())

SYSTEM_PROMPT = """You are an expert resume analyst for job seekers in Israel and abroad.
You receive raw text extracted from a PDF resume (Hebrew and/or English).

Return ONE JSON object with exactly these top-level keys:

1. "sections" — object with keys: summary, experience, education, skills, projects,
   certifications, languages, military_service, volunteering, awards, other.
   Each value is the relevant section text copied or lightly cleaned from the resume.

2. "contact" — object: name, email, phone, location, linkedin, github, portfolio
   (empty string if not found).

3. "skills" — object with these category keys, each an array of skill strings:
   programming_languages, frameworks_libraries, databases, cloud_devops_tools,
   data_ai, cyber_security, design_creative, marketing_sales, finance_accounting,
   operations_logistics, hr_admin, healthcare, languages, soft_skills,
   general_tools, other.
   Include skills mentioned anywhere in the resume, not only in a skills section.
   Use canonical names (e.g. "Python", "React", "AWS").

4. "experience" — object:
   - job_titles: array of strings (most recent first)
   - companies: array of strings
   - years_of_experience_estimate: integer or null
   - seniority_level: one of intern, student, junior, mid, senior, lead, manager, unknown
   - management_experience: boolean
   - internship_or_student_experience: boolean

5. "education" — object: degrees, institutions, fields_of_study (arrays of strings)

6. "projects" — array of short project titles/descriptions

7. "certifications" — array of certification names

8. "best_fit_roles" — up to 10 job titles that fit this candidate

9. "strengths" — up to 8 short strength phrases

10. "ai_insights" — object with thoughtful analysis:
    - professional_summary: 2-4 sentences in the resume's primary language
    - key_achievements: array of notable accomplishments (metrics if present)
    - career_trajectory: 1-2 sentences on career direction
    - recommended_job_types: array of specific role types to target
    - skills_to_highlight: top skills to emphasize in applications
    - potential_gaps: weaknesses or missing items for target roles (be constructive)
    - improvement_suggestions: actionable resume tips (2-5 items)

Rules:
- Base everything ONLY on the resume text provided. Do not invent employers or degrees.
- If information is missing, use empty strings, empty arrays, null, or false.
- Handle Hebrew and English resumes naturally.
- Return valid JSON only, no markdown."""


def is_ai_available() -> bool:
    """True when an OpenAI API key is configured."""
    return bool(OPENAI_API_KEY)


def _truncate_text(text: str, max_chars: int = OPENAI_CV_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... truncated for API limit ...]"


def _empty_ai_insights() -> dict[str, Any]:
    return {
        "professional_summary": "",
        "key_achievements": [],
        "career_trajectory": "",
        "recommended_job_types": [],
        "skills_to_highlight": [],
        "potential_gaps": [],
        "improvement_suggestions": [],
    }


def _normalize_string_list(value: Any, max_items: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _normalize_skills(value: Any) -> dict[str, list[str]]:
    result = {key: [] for key in SKILL_CATEGORY_KEYS}
    if not isinstance(value, dict):
        return result
    for key in SKILL_CATEGORY_KEYS:
        result[key] = _normalize_string_list(value.get(key, []), max_items=50)
    return result


def _normalize_sections(value: Any) -> dict[str, str]:
    sections = {key: "" for key in SECTION_KEYS}
    if not isinstance(value, dict):
        return sections
    for key in SECTION_KEYS:
        raw = value.get(key, "")
        sections[key] = str(raw).strip() if raw is not None else ""
    return sections


def _normalize_contact(value: Any) -> dict[str, str]:
    fields = ["name", "email", "phone", "location", "linkedin", "github", "portfolio"]
    contact = {field: "" for field in fields}
    if not isinstance(value, dict):
        return contact
    for field in fields:
        contact[field] = str(value.get(field, "") or "").strip()
    return contact


def _normalize_experience(value: Any) -> dict[str, Any]:
    default: dict[str, Any] = {
        "job_titles": [],
        "companies": [],
        "years_of_experience_estimate": None,
        "seniority_level": "unknown",
        "management_experience": False,
        "internship_or_student_experience": False,
    }
    if not isinstance(value, dict):
        return default

    seniority = str(value.get("seniority_level", "unknown") or "unknown").lower()
    if seniority not in SENIORITY_LEVELS:
        seniority = "unknown"

    years = value.get("years_of_experience_estimate")
    if years is not None:
        try:
            years = max(0, int(years))
        except (TypeError, ValueError):
            years = None

    return {
        "job_titles": _normalize_string_list(value.get("job_titles", [])),
        "companies": _normalize_string_list(value.get("companies", [])),
        "years_of_experience_estimate": years,
        "seniority_level": seniority,
        "management_experience": bool(value.get("management_experience", False)),
        "internship_or_student_experience": bool(
            value.get("internship_or_student_experience", False)
        ),
    }


def _normalize_education(value: Any) -> dict[str, list[str]]:
    return {
        "degrees": _normalize_string_list(
            value.get("degrees", []) if isinstance(value, dict) else []
        ),
        "institutions": _normalize_string_list(
            value.get("institutions", []) if isinstance(value, dict) else []
        ),
        "fields_of_study": _normalize_string_list(
            value.get("fields_of_study", []) if isinstance(value, dict) else []
        ),
    }


def _normalize_ai_insights(value: Any) -> dict[str, Any]:
    insights = _empty_ai_insights()
    if not isinstance(value, dict):
        return insights

    insights["professional_summary"] = str(
        value.get("professional_summary", "") or ""
    ).strip()
    insights["career_trajectory"] = str(value.get("career_trajectory", "") or "").strip()
    insights["key_achievements"] = _normalize_string_list(
        value.get("key_achievements", []), max_items=10
    )
    insights["recommended_job_types"] = _normalize_string_list(
        value.get("recommended_job_types", []), max_items=10
    )
    insights["skills_to_highlight"] = _normalize_string_list(
        value.get("skills_to_highlight", []), max_items=15
    )
    insights["potential_gaps"] = _normalize_string_list(
        value.get("potential_gaps", []), max_items=8
    )
    insights["improvement_suggestions"] = _normalize_string_list(
        value.get("improvement_suggestions", []), max_items=8
    )
    return insights


def normalize_ai_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the model response to the expected schema."""
    return {
        "sections": _normalize_sections(data.get("sections")),
        "contact": _normalize_contact(data.get("contact")),
        "skills": _normalize_skills(data.get("skills")),
        "experience": _normalize_experience(data.get("experience")),
        "education": _normalize_education(data.get("education")),
        "projects": _normalize_string_list(data.get("projects", [])),
        "certifications": _normalize_string_list(data.get("certifications", [])),
        "best_fit_roles": _normalize_string_list(data.get("best_fit_roles", []), max_items=10),
        "strengths": _normalize_string_list(data.get("strengths", []), max_items=8),
        "ai_insights": _normalize_ai_insights(data.get("ai_insights")),
    }


def _parse_json_response(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError("Empty response from OpenAI")

    # Strip optional markdown code fences if the model adds them anyway.
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fenced:
        text = fenced.group(1).strip()

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("OpenAI response is not a JSON object")
    return data


def analyze_cv_with_openai(raw_text: str) -> dict[str, Any]:
    """Send resume text to OpenAI and return a normalized structured profile."""
    if not is_ai_available():
        raise RuntimeError("OPENAI_API_KEY is not set in .env")

    if not (raw_text or "").strip():
        raise ValueError("No resume text to analyze")

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    resume_text = _truncate_text(raw_text)

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Analyze this resume and return the JSON object described.\n\n"
                    f"--- RESUME TEXT ---\n{resume_text}"
                ),
            },
        ],
    )

    content = response.choices[0].message.content or ""
    return normalize_ai_profile(_parse_json_response(content))


VISION_SYSTEM_PROMPT = SYSTEM_PROMPT + """

You are reading resume PAGE IMAGES (photo/scan). OCR the visible text carefully,
including Hebrew. Then fill the same JSON schema."""


def analyze_cv_with_vision(image_pages: list[bytes]) -> dict[str, Any]:
    """Analyze resume from page images via OpenAI Vision (scanned PDFs / photos)."""
    if not is_ai_available():
        raise RuntimeError("OPENAI_API_KEY is not set in .env")
    if not image_pages:
        raise ValueError("No resume images to analyze")

    import base64

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Read every page of this resume image and return the JSON object "
                "described in the system prompt. Include all Hebrew/English text."
            ),
        }
    ]
    for index, image_bytes in enumerate(image_pages[:3], start=1):
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"},
            }
        )

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )

    result = normalize_ai_profile(_parse_json_response(response.choices[0].message.content or ""))
    # Build searchable raw text from AI sections for downstream matching.
    sections = result.get("sections", {})
    if isinstance(sections, dict):
        result["_vision_raw_text"] = "\n\n".join(
            str(text).strip() for text in sections.values() if str(text).strip()
        )
    return result


def merge_skill_lists(
    base: dict[str, list[str]], extra: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Union of skill lists per category (case-insensitive dedup)."""
    merged = {key: list(base.get(key, [])) for key in SKILL_CATEGORY_KEYS}
    for key in SKILL_CATEGORY_KEYS:
        seen = {item.lower() for item in merged[key]}
        for skill in extra.get(key, []):
            if skill.lower() not in seen:
                merged[key].append(skill)
                seen.add(skill.lower())
    return merged


def merge_profiles(rule_based: dict[str, Any], ai_based: dict[str, Any]) -> dict[str, Any]:
    """Combine rule-based and AI profiles — AI primary, rule-based fills gaps."""
    merged = dict(ai_based)

    # Keep locally extracted raw text, or text reconstructed from vision OCR.
    raw_text = rule_based.get("raw_text", "")
    if not raw_text.strip():
        raw_text = str(ai_based.pop("_vision_raw_text", "") or "")
    else:
        ai_based.pop("_vision_raw_text", None)

    merged["raw_text"] = raw_text
    merged["char_count"] = len(raw_text)

    # Union skills from both parsers.
    merged["skills"] = merge_skill_lists(
        rule_based.get("skills", {}),
        ai_based.get("skills", {}),
    )

    # Prefer AI contact but keep rule-based values when AI missed a field.
    contact = dict(ai_based.get("contact", {}))
    for field, value in rule_based.get("contact", {}).items():
        if not contact.get(field) and value:
            contact[field] = value
    merged["contact"] = contact

    # Union list fields.
    for field in ("projects", "certifications", "best_fit_roles", "strengths"):
        combined: list[str] = []
        seen: set[str] = set()
        for source in (ai_based, rule_based):
            for item in source.get(field, []):
                key = str(item).lower()
                if key not in seen:
                    combined.append(str(item))
                    seen.add(key)
        merged[field] = combined[:10] if field == "best_fit_roles" else combined[:8 if field == "strengths" else 20]

    # Experience: prefer AI but keep rule-based titles/companies if AI returned fewer.
    exp = dict(ai_based.get("experience", {}))
    rb_exp = rule_based.get("experience", {})
    for list_field in ("job_titles", "companies"):
        items = list(exp.get(list_field, []))
        seen = {item.lower() for item in items}
        for item in rb_exp.get(list_field, []):
            if item.lower() not in seen:
                items.append(item)
                seen.add(item.lower())
        exp[list_field] = items[:10]
    if exp.get("years_of_experience_estimate") is None:
        exp["years_of_experience_estimate"] = rb_exp.get("years_of_experience_estimate")
    merged["experience"] = exp

    return merged
