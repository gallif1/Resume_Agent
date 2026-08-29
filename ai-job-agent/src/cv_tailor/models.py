"""Structured schemas for the CV Tailor MVP."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
            contact=str(data.get("contact") or data.get("contact_line") or "").strip(),
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
