#!/usr/bin/env python3
"""Generate before/after resume writing examples across professions.

Runs the Human Resume Writer stage (deterministic + optional LLM) on
representative validated resumes and writes comparison artifacts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from intelligent_tailoring.writing.fact_lock import compare_facts
from intelligent_tailoring.writing.style_validator import evaluate_writing_quality
from intelligent_tailoring.writing.ai_detector import detect_ai_writing
from intelligent_tailoring.writing.writing_pipeline import run_human_writing_stage
from pdf_generator_service import ModernPdfRenderer, markdown_to_resume_html

OUT_DIR = Path("/tmp/cursor/artifacts/resume-examples")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLES = {
    "backend": {
        "professional_title": "Backend Engineer",
        "professional_summary": (
            "Results-driven professional with a proven track record and strong "
            "understanding of Python services. Passionate about delivering "
            "exceptional results in fast-paced environments using cutting-edge tools."
        ),
        "skills": [
            "Languages: Python, SQL",
            "Backend & Frameworks: FastAPI, SQLAlchemy",
            "Cloud & DevOps: Docker, AWS",
        ],
        "experience": [
            {
                "company": "Acme Corp",
                "title": "Software Engineer",
                "dates": "2021 – Present",
                "bullets": [
                    "Responsible for implementing REST APIs with FastAPI and PostgreSQL",
                    "Worked on monitoring dashboards for production services",
                    "Implemented CRUD endpoints for internal tools",
                    "Implemented automated tests for billing workflows",
                ],
            }
        ],
        "projects": [
            {
                "name": "Ops Monitor",
                "description": "Created monitoring system.",
                "bullets": [
                    "Utilized Python and Docker to collect service metrics",
                    "Leveraged PostgreSQL for historical metric storage",
                ],
            }
        ],
        "education": [{"school": "State University", "degree": "B.Sc. Computer Science"}],
        "certifications": ["AWS Cloud Practitioner"],
    },
    "frontend": {
        "professional_title": "Frontend Engineer",
        "professional_summary": (
            "Highly motivated frontend developer with extensive experience in React. "
            "Results-driven team player passionate about cutting-edge interfaces."
        ),
        "skills": ["Languages: TypeScript, JavaScript", "Frameworks: React, Next.js"],
        "experience": [
            {
                "company": "Pixel Labs",
                "title": "Frontend Developer",
                "dates": "2020 – Present",
                "bullets": [
                    "Responsible for building React dashboards for operations teams",
                    "Worked on accessibility improvements across core pages",
                    "Implemented design-system components used by product squads",
                ],
            }
        ],
        "projects": [
            {
                "name": "Design System",
                "description": "Internal component library.",
                "bullets": ["Developed reusable React components used by three product teams"],
            }
        ],
        "education": [],
        "certifications": [],
    },
    "devops": {
        "professional_title": "DevOps Engineer",
        "professional_summary": (
            "Seasoned professional with knowledge of CI/CD and cloud infrastructure. "
            "Passionate about reliable deployments and world-class automation."
        ),
        "skills": ["Cloud & DevOps: Kubernetes, Terraform, AWS", "Languages: Python, Bash"],
        "experience": [
            {
                "company": "CloudNine",
                "title": "DevOps Engineer",
                "dates": "2019 – Present",
                "bullets": [
                    "Responsible for maintaining Kubernetes clusters and CI pipelines",
                    "Worked on Terraform modules for staging environments",
                    "Implemented monitoring alerts for production services",
                ],
            }
        ],
        "projects": [],
        "education": [],
        "certifications": [],
    },
    "qa": {
        "professional_title": "QA Engineer",
        "professional_summary": (
            "Detail-oriented professional with a proven track record in test automation "
            "and a strong understanding of quality processes."
        ),
        "skills": ["Tools: Playwright, pytest", "Languages: Python"],
        "experience": [
            {
                "company": "Quality First",
                "title": "QA Engineer",
                "dates": "2021 – Present",
                "bullets": [
                    "Responsible for writing end-to-end tests with Playwright",
                    "Worked on regression suites for release validation",
                ],
            }
        ],
        "projects": [],
        "education": [],
        "certifications": [],
    },
    "customer_service": {
        "professional_title": "Customer Support Specialist",
        "professional_summary": (
            "Highly motivated customer service professional passionate about helping clients "
            "and delivering exceptional results."
        ),
        "skills": ["Tools: Zendesk, Salesforce", "Languages: English, Hebrew"],
        "experience": [
            {
                "company": "HelpDesk Co",
                "title": "Support Specialist",
                "dates": "2022 – Present",
                "bullets": [
                    "Responsible for resolving customer tickets in Zendesk",
                    "Worked on onboarding guides for new accounts",
                    "Handled escalation calls for billing issues",
                ],
            }
        ],
        "projects": [],
        "education": [{"school": "City College", "degree": "BA Communications"}],
        "certifications": [],
    },
    "teacher": {
        "professional_title": "Mathematics Teacher",
        "professional_summary": (
            "Dedicated professional with a passion for education and proven track record "
            "of student success in fast-paced environments."
        ),
        "skills": ["Classroom instruction", "Curriculum planning", "Parent communication"],
        "experience": [
            {
                "company": "Lincoln High School",
                "title": "Math Teacher",
                "dates": "2018 – Present",
                "bullets": [
                    "Responsible for teaching algebra and geometry to grades 9-11",
                    "Worked on after-school tutoring for struggling learners",
                    "Prepared curriculum materials aligned to state standards",
                ],
            }
        ],
        "projects": [],
        "education": [{"school": "Teachers College", "degree": "B.Ed. Mathematics"}],
        "certifications": ["Teaching License"],
    },
    "sales": {
        "professional_title": "Account Executive",
        "professional_summary": (
            "Results-driven sales professional with a proven track record of hitting quotas "
            "and leveraging CRM tools for success."
        ),
        "skills": ["Salesforce", "Negotiation", "Pipeline management"],
        "experience": [
            {
                "company": "Northwind Sales",
                "title": "Account Executive",
                "dates": "2020 – Present",
                "bullets": [
                    "Responsible for managing a portfolio of mid-market accounts",
                    "Worked on closing renewal deals worth $1.2M annually",
                    "Utilized Salesforce to track pipeline health",
                ],
            }
        ],
        "projects": [],
        "education": [],
        "certifications": [],
    },
    "healthcare": {
        "professional_title": "Registered Nurse",
        "professional_summary": (
            "Compassionate healthcare professional passionate about patient outcomes "
            "and delivering exceptional care."
        ),
        "skills": ["Patient care", "Electronic health records", "Triage"],
        "experience": [
            {
                "company": "City General Hospital",
                "title": "Registered Nurse",
                "dates": "2019 – Present",
                "bullets": [
                    "Responsible for coordinating care for medical-surgical patients",
                    "Worked on triage workflows during peak admission periods",
                    "Documented care plans in the electronic health record",
                ],
            }
        ],
        "projects": [],
        "education": [],
        "certifications": ["RN License"],
    },
    "administration": {
        "professional_title": "Office Administrator",
        "professional_summary": (
            "Organized administrative professional with extensive experience in office "
            "operations and a strong understanding of scheduling processes."
        ),
        "skills": ["Microsoft Office", "Scheduling", "Vendor coordination"],
        "experience": [
            {
                "company": "Harbor Logistics",
                "title": "Office Administrator",
                "dates": "2017 – Present",
                "bullets": [
                    "Responsible for scheduling meetings and maintaining records",
                    "Worked on vendor invoice tracking and office supply orders",
                ],
            }
        ],
        "projects": [],
        "education": [],
        "certifications": [],
    },
}


def _to_markdown(resume: dict, name: str = "Alex Candidate") -> str:
    lines = [
        f"# {name}",
        "",
        "alex@example.com | +1-555-0100",
        "",
        f"**Target Role: {resume.get('professional_title') or 'Professional'}**",
        "",
        "## Summary",
        "",
        str(resume.get("professional_summary") or resume.get("summary") or ""),
        "",
        "## Experience",
        "",
    ]
    for entry in resume.get("experience") or []:
        lines.append(f"### {entry.get('title') or ''}")
        lines.append(f"{entry.get('company') or ''} | {entry.get('dates') or ''}")
        lines.append("")
        for bullet in entry.get("bullets") or []:
            lines.append(f"- {bullet}")
        lines.append("")
    if resume.get("projects"):
        lines.append("## Projects")
        lines.append("")
        for entry in resume.get("projects") or []:
            lines.append(f"### {entry.get('name') or ''}")
            if entry.get("description"):
                lines.append(str(entry["description"]))
            lines.append("")
            for bullet in entry.get("bullets") or []:
                lines.append(f"- {bullet}")
            lines.append("")
    lines.append("## Skills")
    lines.append("")
    for skill in resume.get("skills") or []:
        lines.append(str(skill))
    return "\n".join(lines).strip() + "\n"


def _metrics(resume: dict) -> dict:
    style = evaluate_writing_quality(resume)
    ai = detect_ai_writing(resume)
    return {
        "overall_writing_score": style["overall_score"],
        "dimensions": style["dimensions"],
        "ai_risk": ai["ai_risk"],
        "human_score": ai["human_score"],
        "ai_signals": ai["signals"],
    }


def main() -> int:
    summary_rows = []
    renderer = ModernPdfRenderer(theme="modern_ats")
    for profession, resume in SAMPLES.items():
        before = dict(resume)
        before["summary"] = before.get("professional_summary") or ""
        stage = run_human_writing_stage(
            validated_resume=before,
            strategy={
                "candidate_value_proposition": f"strong {profession} contributor",
                "tone": "professional",
            },
            knowledge_base={"facts": []},
            output_language="en",
            allow_llm=False,
            max_review_cycles=2,
        )
        after = stage["tailored_resume"]
        facts_ok = compare_facts(before, after)["passed"]
        before_m = _metrics(before)
        after_m = _metrics(after)

        payload = {
            "profession": profession,
            "facts_unchanged": facts_ok,
            "before": before,
            "after": after,
            "before_metrics": before_m,
            "after_metrics": after_m,
            "writing_stage": {
                "passed": stage.get("passed"),
                "review_cycles": stage.get("review_cycles"),
                "writer_mode": (stage.get("writer") or {}).get("mode"),
                "quality_gate_failures": stage.get("quality_gate_failures"),
            },
        }
        (OUT_DIR / f"{profession}_before_after.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        before_md = _to_markdown(before, name=f"{profession.title()} Candidate")
        after_md = _to_markdown(after, name=f"{profession.title()} Candidate")
        (OUT_DIR / f"{profession}_before.md").write_text(before_md, encoding="utf-8")
        (OUT_DIR / f"{profession}_after.md").write_text(after_md, encoding="utf-8")

        for theme in ("modern_ats", "professional", "executive", "minimal", "classic"):
            html = markdown_to_resume_html(after_md, theme=theme)
            (OUT_DIR / f"{profession}_{theme}.html").write_text(html, encoding="utf-8")

        try:
            pdf_bytes, _ = renderer.render(after_md, theme="modern_ats")
            (OUT_DIR / f"{profession}_modern_ats.pdf").write_bytes(pdf_bytes)
        except Exception as exc:  # noqa: BLE001
            (OUT_DIR / f"{profession}_pdf_error.txt").write_text(str(exc), encoding="utf-8")

        summary_rows.append(
            {
                "profession": profession,
                "facts_unchanged": facts_ok,
                "before_score": before_m["overall_writing_score"],
                "after_score": after_m["overall_writing_score"],
                "before_ai_risk": before_m["ai_risk"],
                "after_ai_risk": after_m["ai_risk"],
                "before_human": before_m["human_score"],
                "after_human": after_m["human_score"],
            }
        )

    comparison = {
        "summary": summary_rows,
        "notes": (
            "Before = claim-validated but AI-sounding draft. "
            "After = Human Resume Writer + Senior Recruiter Review polish "
            "(deterministic path in this script; production also uses LLM polish)."
        ),
    }
    (OUT_DIR / "comparison_summary.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Human-readable markdown report
    lines = [
        "# Resume Writing Before / After",
        "",
        "| Profession | Facts OK | Writing score before → after | AI risk before → after | Human score before → after |",
        "|---|---|---|---|---|",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['profession']} | {row['facts_unchanged']} | "
            f"{row['before_score']} → {row['after_score']} | "
            f"{row['before_ai_risk']} → {row['after_ai_risk']} | "
            f"{row['before_human']} → {row['after_human']} |"
        )
    lines.append("")
    lines.append("Artifacts written to `/tmp/cursor/artifacts/resume-examples/`.")
    report = "\n".join(lines) + "\n"
    (OUT_DIR / "COMPARISON.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
