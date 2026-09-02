"""Structured schemas for the CV Tailor MVP."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

RequirementStatus = Literal["SUPPORTED", "USER_CONFIRMED", "UNSUPPORTED"]


class ExperienceEntry(BaseModel):
    company: str = ""
    role: str = ""
    dates: str = ""
    bullets: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    name: str = ""
    description: str = ""
    bullets: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: str = ""
    degree: str = ""
    dates: str = ""


class SkillGroup(BaseModel):
    category: str = ""
    skills: list[str] = Field(default_factory=list)


class CandidateFact(BaseModel):
    """A single candidate fact with provenance for future profile storage."""

    fact: str = ""
    normalized_fact: str = ""
    source: Literal["original_cv", "user_confirmed"] = "user_confirmed"
    gap_id: str = ""


class ResolvedRequirement(BaseModel):
    requirement: str = ""
    title: str = ""
    status: RequirementStatus = "SUPPORTED"
    note: str = ""


class RequirementGap(BaseModel):
    gap_id: str = ""
    title: str = ""
    requirement: str = ""
    job_requirement_text: str = ""
    cv_evidence: str = ""
    confirmation_text: str = ""
    status: RequirementStatus = "UNSUPPORTED"
    explanation: str = ""


class JobAnalysis(BaseModel):
    target_job_title: str = ""
    seniority_required: str = ""
    must_have_technologies: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    key_phrases: list[str] = Field(default_factory=list)
    strong_matches: list[str] = Field(default_factory=list)
    gaps: list[RequirementGap] = Field(default_factory=list)
    resolved_requirements: list[ResolvedRequirement] = Field(default_factory=list)

    @classmethod
    def from_llm_dict(cls, data: dict[str, Any]) -> JobAnalysis:
        def _str_list(key: str) -> list[str]:
            return [
                str(item).strip()
                for item in (data.get(key) or [])
                if str(item).strip()
            ]

        strong_matches = _str_list("strong_matches")
        gaps: list[RequirementGap] = []
        for index, item in enumerate(data.get("gaps") or []):
            if not isinstance(item, dict):
                continue
            requirement = str(item.get("requirement") or "").strip()
            if not requirement:
                continue
            raw_status = str(item.get("status") or "").strip().lower()
            status = _normalize_requirement_status(raw_status)
            title = str(item.get("title") or requirement).strip()
            gap_id = str(item.get("gap_id") or "").strip() or _slugify_gap_id(title or requirement, index)
            job_requirement_text = str(
                item.get("job_requirement_text") or item.get("job_requirement") or ""
            ).strip()
            cv_evidence = str(item.get("cv_evidence") or "").strip()
            confirmation_text = str(item.get("confirmation_text") or "").strip()
            explanation = str(item.get("explanation") or "").strip()
            if not job_requirement_text and explanation:
                job_requirement_text = explanation
            gaps.append(
                RequirementGap(
                    gap_id=gap_id,
                    title=title,
                    requirement=requirement,
                    job_requirement_text=job_requirement_text,
                    cv_evidence=cv_evidence,
                    confirmation_text=confirmation_text,
                    status=status if status != "SUPPORTED" else "UNSUPPORTED",
                    explanation=explanation,
                )
            )

        resolved: list[ResolvedRequirement] = []
        for item in data.get("resolved_requirements") or []:
            if not isinstance(item, dict):
                continue
            requirement = str(item.get("requirement") or "").strip()
            if not requirement:
                continue
            raw_status = str(item.get("status") or "SUPPORTED").strip().upper()
            status: RequirementStatus = (
                raw_status if raw_status in ("SUPPORTED", "USER_CONFIRMED") else "SUPPORTED"
            )
            resolved.append(
                ResolvedRequirement(
                    requirement=requirement,
                    title=str(item.get("title") or requirement).strip(),
                    status=status,
                    note=str(item.get("note") or "").strip(),
                )
            )

        return cls(
            target_job_title=str(data.get("target_job_title") or "").strip(),
            seniority_required=str(data.get("seniority_required") or "").strip(),
            must_have_technologies=_str_list("must_have_technologies"),
            nice_to_have=_str_list("nice_to_have"),
            key_phrases=_str_list("key_phrases"),
            strong_matches=strong_matches,
            gaps=gaps,
            resolved_requirements=resolved,
        )


def _slugify_gap_id(text: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or f"gap-{index}"


def _format_contact_field(value: Any) -> str:
    """Normalize contact info from strings, dicts, or lists into one display line."""
    if value is None:
        return ""
    if isinstance(value, dict):
        ordered_keys = (
            "location",
            "phone",
            "email",
            "github",
            "linkedin",
            "website",
            "url",
        )
        parts: list[str] = []
        seen: set[str] = set()
        for key in ordered_keys:
            raw = value.get(key)
            if raw is None:
                continue
            text = str(raw).strip()
            if text and text not in seen:
                parts.append(text)
                seen.add(text)
        for raw in value.values():
            text = str(raw).strip()
            if text and text not in seen:
                parts.append(text)
                seen.add(text)
        return " | ".join(parts)
    if isinstance(value, list):
        return " | ".join(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        return ""
    return text


def _normalize_requirement_status(raw: str) -> RequirementStatus:
    normalized = raw.replace("-", "_").upper()
    if normalized in ("SUPPORTED", "USER_CONFIRMED", "UNSUPPORTED"):
        return normalized  # type: ignore[return-value]
    if raw in ("insufficient_evidence", "not_found", "missing", "unsupported"):
        return "UNSUPPORTED"
    if raw in ("user_confirmed", "confirmed"):
        return "USER_CONFIRMED"
    if raw in ("supported", "matched"):
        return "SUPPORTED"
    return "UNSUPPORTED"


class GapConfirmationInput(BaseModel):
    gap_id: str = ""
    confirmed: bool = False
    details: str = ""


class RegenerateCvRequest(BaseModel):
    gap_confirmations: list[GapConfirmationInput] = Field(default_factory=list)
    general_additional_info: str = ""


class TailoredCvData(BaseModel):
    name: str = ""
    contact: str = ""
    professional_title: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    skill_groups: list[SkillGroup] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)

    @classmethod
    def from_llm_dict(cls, data: dict[str, Any]) -> TailoredCvData:
        """Normalize LLM JSON into a stable schema."""
        experience: list[ExperienceEntry] = []
        for item in data.get("experience") or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("title") or "").strip()
            company = str(item.get("company") or "").strip()
            dates = str(item.get("dates") or "").strip()
            bullets = [
                str(b).strip()
                for b in (item.get("bullets") or [])
                if str(b).strip()
            ]
            if role or company or bullets:
                experience.append(
                    ExperienceEntry(company=company, role=role, dates=dates, bullets=bullets)
                )

        projects: list[ProjectEntry] = []
        for item in data.get("projects") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            bullets = [
                str(b).strip()
                for b in (item.get("bullets") or [])
                if str(b).strip()
            ]
            if name or description or bullets:
                projects.append(
                    ProjectEntry(name=name, description=description, bullets=bullets)
                )

        education: list[EducationEntry] = []
        for item in data.get("education") or []:
            if not isinstance(item, dict):
                continue
            institution = str(item.get("institution") or item.get("school") or "").strip()
            degree = str(item.get("degree") or "").strip()
            dates = str(item.get("dates") or "").strip()
            if institution or degree:
                education.append(
                    EducationEntry(institution=institution, degree=degree, dates=dates)
                )

        skills = [
            str(s).strip() for s in (data.get("skills") or []) if str(s).strip()
        ]

        skill_groups: list[SkillGroup] = []
        raw_groups = data.get("skill_groups") or data.get("skills_by_category") or []
        if isinstance(raw_groups, dict):
            raw_groups = [{"category": k, "skills": v} for k, v in raw_groups.items()]
        for item in raw_groups:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or item.get("name") or "").strip()
            group_skills = [
                str(s).strip()
                for s in (item.get("skills") or [])
                if str(s).strip()
            ]
            if category and group_skills:
                skill_groups.append(SkillGroup(category=category, skills=group_skills))

        certs_raw = data.get("certifications") or []
        certifications: list[str] = []
        for cert in certs_raw:
            if isinstance(cert, dict):
                label = str(cert.get("name") or cert.get("title") or "").strip()
            else:
                label = str(cert).strip()
            if label:
                certifications.append(label)

        summary = str(
            data.get("summary") or data.get("professional_summary") or ""
        ).strip()

        return cls(
            name=str(data.get("name") or "").strip(),
            contact=_format_contact_field(data.get("contact") or data.get("contact_line")),
            professional_title=str(
                data.get("professional_title")
                or data.get("target_role")
                or data.get("title")
                or ""
            ).strip(),
            summary=summary,
            skills=skills,
            skill_groups=skill_groups,
            experience=experience,
            projects=projects,
            education=education,
            certifications=certifications,
        )

    def to_preview_text(self) -> str:
        """Plain-text preview for the UI."""
        lines: list[str] = []
        if self.name:
            lines.append(self.name)
        if self.professional_title:
            lines.append(self.professional_title)
        if self.contact:
            lines.append(self.contact)
        if self.summary:
            lines.extend(["", "Summary", self.summary])
        if self.skill_groups:
            lines.extend(["", "Technical Skills"])
            for group in self.skill_groups:
                lines.append(f"{group.category}: {', '.join(group.skills)}")
        elif self.skills:
            lines.extend(["", "Technical Skills", ", ".join(self.skills)])
        if self.experience:
            lines.append("")
            lines.append("Experience")
            for entry in self.experience:
                heading = " — ".join(
                    part for part in (entry.role, entry.company) if part
                )
                if heading:
                    lines.append(heading)
                if entry.dates:
                    lines.append(entry.dates)
                lines.extend(f"• {bullet}" for bullet in entry.bullets)
        if self.projects:
            lines.append("")
            lines.append("Projects")
            for project in self.projects:
                if project.name:
                    lines.append(project.name)
                if project.description:
                    lines.append(project.description)
                lines.extend(f"• {bullet}" for bullet in project.bullets)
        if self.education:
            lines.append("")
            lines.append("Education")
            for edu in self.education:
                line = " — ".join(part for part in (edu.degree, edu.institution) if part)
                if line:
                    lines.append(line)
                if edu.dates:
                    lines.append(edu.dates)
        if self.certifications:
            lines.append("")
            lines.append("Certifications")
            lines.extend(f"• {cert}" for cert in self.certifications)
        return "\n".join(lines).strip()


class TailoredCvResult(BaseModel):
    result_id: str
    tailored_cv: TailoredCvData
    preview_text: str
    model: str
    job_analysis: JobAnalysis = Field(default_factory=JobAnalysis)
    user_confirmed_facts: list[CandidateFact] = Field(default_factory=list)

    def gaps_preview_text(self) -> str:
        if not self.job_analysis.gaps:
            return ""
        lines = ["Important gaps (not added to CV):"]
        for gap in self.job_analysis.gaps:
            lines.append(f"• {gap.title or gap.requirement} — {gap.explanation or gap.status}")
        return "\n".join(lines)
