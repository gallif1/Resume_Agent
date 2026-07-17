"""Aggregate multiple parsed CV texts into a unified Master Candidate Profile."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ai_client import (
    OpenAIAPIError,
    call_openai_json,
    is_ai_available,
    normalize_string_list,
    truncate_text,
)
from config import OPENAI_CV_MAX_CHARS, OPENAI_MODEL
from parse_cv import empty_profile, save_json
from skills import SKILL_CATEGORIES

AGGREGATION_SYSTEM_PROMPT = """You are an expert resume analyst. You receive multiple CV/resume
versions for the SAME candidate. Merge them into ONE comprehensive Master Candidate Profile.

Return ONE JSON object with exactly these keys:

{
  "personal_info": {
    "name": "",
    "email": "",
    "phone": "",
    "location": "",
    "linkedin": "",
    "github": "",
    "portfolio": ""
  },
  "unified_summary": "2-4 sentence comprehensive summary of strengths across all CVs",
  "master_skills": {
    "<skill_category>": ["skill1", "skill2"]
  },
  "work_experience": [
    {
      "title": "Job Title",
      "company": "Company",
      "start_date": "YYYY-MM or text",
      "end_date": "YYYY-MM, Present, or text",
      "description": "Brief role summary",
      "bullet_points": ["achievement or responsibility"]
    }
  ],
  "projects": [
    {
      "name": "Project name",
      "description": "What was built/done",
      "technologies": ["tech1", "tech2"]
    }
  ],
  "education": [
    {
      "degree": "Degree name",
      "institution": "School/University",
      "field": "Field of study",
      "year": "Graduation year or range"
    }
  ],
  "languages": ["Hebrew", "English"]
}

Aggregation rules:
1. DEDUPLICATION: Merge duplicate job experiences and education entries. Same role at same company
   = one entry. If different CVs highlight different bullet points for the same role, merge ALL
   unique bullet points logically (no duplicates).
2. SKILLS: Collect ALL technical and soft skills from every CV into master_skills, categorized.
   Use categories: programming_languages, frameworks_libraries, databases, cloud_devops_tools,
   data_ai, cyber_security, design_creative, marketing_sales, finance_accounting,
   operations_logistics, hr_admin, healthcare, languages, soft_skills, general_tools, other.
3. PROJECTS: Collect ALL personal, academic, and professional projects from any CV. Deduplicate
   by name/similarity; merge technologies and descriptions.
4. PERSONAL INFO: Prefer the most complete contact details across CVs.
5. Base analysis ONLY on provided CV texts. Do not invent employers, degrees, or skills.
6. Handle Hebrew and English content naturally.
7. Return valid JSON only, no markdown."""


class PersonalInfo(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""


class WorkExperienceEntry(BaseModel):
    title: str = ""
    company: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""
    bullet_points: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    name: str = ""
    description: str = ""
    technologies: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    degree: str = ""
    institution: str = ""
    field: str = ""
    year: str = ""


class MasterCandidateProfile(BaseModel):
    personal_info: PersonalInfo = Field(default_factory=PersonalInfo)
    unified_summary: str = ""
    master_skills: dict[str, list[str]] = Field(default_factory=dict)
    work_experience: list[WorkExperienceEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    source_cv_count: int = 0
    aggregated_at: str | None = None
    aggregated_with: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def _empty_skill_categories() -> dict[str, list[str]]:
    return {category: [] for category in SKILL_CATEGORIES}


def _parse_master_skills(raw: Any) -> dict[str, list[str]]:
    skills = _empty_skill_categories()
    if not isinstance(raw, dict):
        return skills
    for category in SKILL_CATEGORIES:
        values = raw.get(category)
        if isinstance(values, list):
            skills[category] = normalize_string_list(values, max_items=50)
    for category, values in raw.items():
        if category in skills or not isinstance(values, list):
            continue
        skills.setdefault("other", []).extend(normalize_string_list(values, max_items=50))
    return skills


def _parse_work_experience(raw: Any) -> list[WorkExperienceEntry]:
    if not isinstance(raw, list):
        return []
    entries: list[WorkExperienceEntry] = []
    for item in raw[:30]:
        if not isinstance(item, dict):
            continue
        entries.append(
            WorkExperienceEntry(
                title=str(item.get("title") or "").strip(),
                company=str(item.get("company") or "").strip(),
                start_date=str(item.get("start_date") or "").strip(),
                end_date=str(item.get("end_date") or "").strip(),
                description=str(item.get("description") or "").strip(),
                bullet_points=normalize_string_list(item.get("bullet_points"), max_items=20),
            )
        )
    return [e for e in entries if e.title or e.company]


def _parse_projects(raw: Any) -> list[ProjectEntry]:
    if not isinstance(raw, list):
        return []
    projects: list[ProjectEntry] = []
    for item in raw[:40]:
        if isinstance(item, str):
            text = item.strip()
            if text:
                projects.append(ProjectEntry(name=text))
            continue
        if not isinstance(item, dict):
            continue
        projects.append(
            ProjectEntry(
                name=str(item.get("name") or "").strip(),
                description=str(item.get("description") or "").strip(),
                technologies=normalize_string_list(item.get("technologies"), max_items=20),
            )
        )
    return [p for p in projects if p.name or p.description]


def _parse_education(raw: Any) -> list[EducationEntry]:
    if not isinstance(raw, list):
        return []
    entries: list[EducationEntry] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        entries.append(
            EducationEntry(
                degree=str(item.get("degree") or "").strip(),
                institution=str(item.get("institution") or "").strip(),
                field=str(item.get("field") or "").strip(),
                year=str(item.get("year") or "").strip(),
            )
        )
    return [e for e in entries if e.degree or e.institution]


def _parse_llm_result(data: dict[str, Any], *, source_cv_count: int) -> MasterCandidateProfile:
    personal_raw = data.get("personal_info") if isinstance(data.get("personal_info"), dict) else {}
    return MasterCandidateProfile(
        personal_info=PersonalInfo(
            name=str(personal_raw.get("name") or "").strip(),
            email=str(personal_raw.get("email") or "").strip(),
            phone=str(personal_raw.get("phone") or "").strip(),
            location=str(personal_raw.get("location") or "").strip(),
            linkedin=str(personal_raw.get("linkedin") or "").strip(),
            github=str(personal_raw.get("github") or "").strip(),
            portfolio=str(personal_raw.get("portfolio") or "").strip(),
        ),
        unified_summary=str(data.get("unified_summary") or "").strip(),
        master_skills=_parse_master_skills(data.get("master_skills")),
        work_experience=_parse_work_experience(data.get("work_experience")),
        projects=_parse_projects(data.get("projects")),
        education=_parse_education(data.get("education")),
        languages=normalize_string_list(data.get("languages"), max_items=20),
        source_cv_count=source_cv_count,
        aggregated_at=datetime.now(timezone.utc).isoformat(),
        aggregated_with="ai",
    )


def _rule_based_aggregate(cv_texts: list[str]) -> MasterCandidateProfile:
    """Fallback merge when AI is unavailable or only one CV is provided."""
    combined_text = "\n\n---\n\n".join(t.strip() for t in cv_texts if t and t.strip())
    profile = MasterCandidateProfile(
        unified_summary=combined_text[:2000],
        master_skills=_empty_skill_categories(),
        source_cv_count=len(cv_texts),
        aggregated_at=datetime.now(timezone.utc).isoformat(),
        aggregated_with="rules",
    )
    if combined_text:
        from skills import detect_skills_by_category

        detected = detect_skills_by_category(combined_text)
        for category, items in detected.items():
            profile.master_skills[category] = list(items)
    return profile


def aggregate_cv_texts(
    cv_texts: list[str],
    *,
    use_ai: bool = True,
) -> MasterCandidateProfile:
    """Merge parsed CV text contents into a Master Candidate Profile."""
    texts = [t.strip() for t in cv_texts if t and t.strip()]
    if not texts:
        return MasterCandidateProfile(source_cv_count=0)

    if len(texts) == 1:
        return _rule_based_aggregate(texts)

    if not use_ai or not is_ai_available():
        return _rule_based_aggregate(texts)

    numbered = []
    for index, text in enumerate(texts, start=1):
        numbered.append(f"=== CV VERSION {index} ===\n{truncate_text(text, OPENAI_CV_MAX_CHARS // max(len(texts), 1))}")
    user_prompt = (
        f"Merge these {len(texts)} CV versions for the same candidate:\n\n"
        + "\n\n".join(numbered)
    )

    try:
        data = call_openai_json(
            AGGREGATION_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.1,
            cache_namespace="cv_aggregate",
            cache_payload=user_prompt,
        )
        return _parse_llm_result(data, source_cv_count=len(texts))
    except OpenAIAPIError:
        return _rule_based_aggregate(texts)


def master_profile_to_cv_profile(master: MasterCandidateProfile) -> dict[str, Any]:
    """Convert a Master Candidate Profile into the legacy cv_profile.json schema."""
    profile = empty_profile()
    contact = profile["contact"]
    pi = master.personal_info
    contact.update({
        "name": pi.name,
        "email": pi.email,
        "phone": pi.phone,
        "location": pi.location,
        "linkedin": pi.linkedin,
        "github": pi.github,
        "portfolio": pi.portfolio,
    })

    profile["skills"] = master.master_skills or _empty_skill_categories()
    profile["sections"]["summary"] = master.unified_summary
    profile["sections"]["languages"] = ", ".join(master.languages)

    job_titles: list[str] = []
    companies: list[str] = []
    experience_lines: list[str] = []
    for entry in master.work_experience:
        if entry.title and entry.title not in job_titles:
            job_titles.append(entry.title)
        if entry.company and entry.company not in companies:
            companies.append(entry.company)
        block = f"{entry.title} @ {entry.company}".strip(" @")
        if entry.start_date or entry.end_date:
            block += f" ({entry.start_date} – {entry.end_date})".strip()
        if entry.description:
            block += f"\n{entry.description}"
        for bullet in entry.bullet_points:
            block += f"\n• {bullet}"
        if block.strip():
            experience_lines.append(block.strip())

    profile["experience"]["job_titles"] = job_titles
    profile["experience"]["companies"] = companies
    profile["sections"]["experience"] = "\n\n".join(experience_lines)

    degrees: list[str] = []
    institutions: list[str] = []
    fields: list[str] = []
    education_lines: list[str] = []
    for entry in master.education:
        if entry.degree:
            degrees.append(entry.degree)
        if entry.institution:
            institutions.append(entry.institution)
        if entry.field:
            fields.append(entry.field)
        line = ", ".join(p for p in [entry.degree, entry.field, entry.institution, entry.year] if p)
        if line:
            education_lines.append(line)
    profile["education"]["degrees"] = degrees
    profile["education"]["institutions"] = institutions
    profile["education"]["fields_of_study"] = fields
    profile["sections"]["education"] = "\n".join(education_lines)

    project_strings: list[str] = []
    for proj in master.projects:
        line = proj.name
        if proj.description:
            line = f"{line}: {proj.description}" if line else proj.description
        if proj.technologies:
            line += f" [{', '.join(proj.technologies)}]"
        if line.strip():
            project_strings.append(line.strip())
    profile["projects"] = project_strings
    profile["sections"]["projects"] = "\n".join(project_strings)

    profile["best_fit_roles"] = job_titles[:10]
    profile["ai_insights"]["professional_summary"] = master.unified_summary
    profile["parsed_with"] = master.aggregated_with
    profile["char_count"] = len(master.unified_summary)
    profile["master_profile"] = master.to_dict()
    profile["raw_text"] = master.unified_summary
    return profile


def save_master_profile(master: MasterCandidateProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(master.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_master_profile(path: Path) -> MasterCandidateProfile | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return MasterCandidateProfile.model_validate(data)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def aggregate_and_save(
    cv_texts: list[str],
    *,
    master_path: Path,
    cv_profile_path: Path,
    use_ai: bool = True,
) -> MasterCandidateProfile:
    """Aggregate CV texts and persist both master and legacy cv_profile formats."""
    master = aggregate_cv_texts(cv_texts, use_ai=use_ai)
    cv_profile = master_profile_to_cv_profile(master)
    save_master_profile(master, master_path)
    save_json(cv_profile, cv_profile_path)
    return master
