"""PDF rendering for tailored CV structured data (professional layout)."""

from __future__ import annotations

import html
import logging
import re

from cv_tailor.models import SkillGroup, TailoredCvData
from pdf_generator_service import PdfGeneratorError

logger = logging.getLogger("cv_tailor.renderer")

CV_TAILOR_THEME = "cv_tailor"

# Matches the reference CV: centered navy header, section rules, categorized skills.
CV_TAILOR_CSS = """
@page {
    size: A4;
    margin: 10mm 12mm 10mm 12mm;
}
* {
    box-sizing: border-box;
}
body {
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    color: #1a1a1a;
    line-height: 1.32;
    font-size: 9.5pt;
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}
.resume {
    width: 100%;
}
.header {
    text-align: center;
    margin-bottom: 3mm;
    padding-bottom: 1.5mm;
}
.header h1 {
    font-family: Georgia, "Times New Roman", Times, serif;
    font-size: 20pt;
    font-weight: 700;
    color: #2e4a7d;
    margin: 0 0 1mm 0;
    letter-spacing: 0.2px;
}
.professional-title {
    font-family: Georgia, "Times New Roman", Times, serif;
    font-size: 10.5pt;
    font-weight: 700;
    color: #2e4a7d;
    margin: 0 0 1mm 0;
}
.contact-info {
    font-size: 8.5pt;
    color: #4a5568;
    margin: 0;
    line-height: 1.35;
}
h2.section-title {
    font-family: Georgia, "Times New Roman", Times, serif;
    font-size: 10.5pt;
    font-weight: 700;
    color: #2e4a7d;
    text-transform: none;
    margin: 3mm 0 1.2mm 0;
    padding-bottom: 0.6mm;
    border-bottom: 1px solid #2e4a7d;
    page-break-after: avoid;
    break-after: avoid;
}
.summary-text {
    margin: 0 0 1mm 0;
    text-align: left;
    color: #1a1a1a;
}
.skills-container {
    margin-top: 0.2mm;
}
.skills-line {
    margin-bottom: 0.5mm;
    font-size: 9.5pt;
}
.skills-category {
    font-weight: 700;
    color: #1a1a1a;
}
.resume-entry {
    margin: 0 0 1.6mm 0;
    page-break-inside: auto;
    break-inside: auto;
}
.resume-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 4mm;
    margin-bottom: 0.3mm;
}
.title-main {
    font-weight: 700;
    color: #1a1a1a;
    font-size: 10.5pt;
}
.title-sub {
    font-weight: 400;
    color: #4a5568;
    font-style: italic;
    font-size: 10pt;
}
.meta-right {
    font-size: 9.5pt;
    color: #4a5568;
    text-align: right;
    white-space: nowrap;
    flex-shrink: 0;
}
.entry-description {
    font-style: italic;
    color: #4a5568;
    font-size: 9.5pt;
    margin: 0.3mm 0 0.8mm 0;
}
ul {
    margin: 0.3mm 0 0.6mm 0;
    padding-left: 4mm;
}
li {
    margin-bottom: 0.35mm;
    color: #1a1a1a;
}
li::marker {
    color: #4a5568;
}
""".strip()

_SKILL_CATEGORY_HINTS: dict[str, str] = {
    "python": "Backend",
    "fastapi": "Backend",
    "django": "Backend",
    "flask": "Backend",
    "node.js": "Backend",
    "nodejs": "Backend",
    "express": "Backend",
    "rest": "Backend",
    "graphql": "Backend",
    "react": "Frontend",
    "angular": "Frontend",
    "vue": "Frontend",
    "html": "Frontend",
    "css": "Frontend",
    "postgresql": "Databases",
    "postgres": "Databases",
    "mysql": "Databases",
    "mongodb": "Databases",
    "redis": "Databases",
    "docker": "Cloud & DevOps",
    "kubernetes": "Cloud & DevOps",
    "aws": "Cloud & DevOps",
    "gcp": "Cloud & DevOps",
    "azure": "Cloud & DevOps",
    "ci/cd": "Cloud & DevOps",
    "javascript": "Languages",
    "typescript": "Languages",
    "sql": "Languages",
    "c++": "Languages",
    "java": "Languages",
}


def _esc(text: str) -> str:
    return html.escape((text or "").strip())


def _group_skills(cv: TailoredCvData) -> list[tuple[str, list[str]]]:
    if cv.skill_groups:
        return [(g.category, list(g.skills)) for g in cv.skill_groups if g.category and g.skills]

    buckets: dict[str, list[str]] = {}
    order: list[str] = []

    def add(category: str, skill: str) -> None:
        skill = skill.strip()
        if not skill:
            return
        if category not in buckets:
            buckets[category] = []
            order.append(category)
        if skill not in buckets[category]:
            buckets[category].append(skill)

    for raw in cv.skills:
        if ":" in raw and len(raw.split(":", 1)[0]) < 30:
            category, values = raw.split(":", 1)
            for part in re.split(r"[,•|/]", values):
                add(category.strip(), part.strip())
            continue
        key = raw.lower().strip()
        category = _SKILL_CATEGORY_HINTS.get(key, "Tools & Technologies")
        add(category, raw)

    if not buckets and cv.skills:
        return [("Technical Skills", cv.skills)]
    return [(cat, buckets[cat]) for cat in order]


def structured_cv_to_html(cv: TailoredCvData) -> str:
    """Render structured CV data into print-ready HTML."""
    parts: list[str] = ['<div class="resume">', '<div class="header">']
    if cv.name:
        parts.append(f"<h1>{_esc(cv.name)}</h1>")
    if cv.professional_title:
        parts.append(f'<p class="professional-title">{_esc(cv.professional_title)}</p>')
    if cv.contact:
        parts.append(f'<div class="contact-info">{_esc(cv.contact)}</div>')
    parts.append("</div>")

    if cv.summary:
        parts.append('<h2 class="section-title">Summary</h2>')
        parts.append(f'<p class="summary-text">{_esc(cv.summary)}</p>')

    skill_groups = _group_skills(cv)
    if skill_groups:
        parts.append('<h2 class="section-title">Technical Skills</h2>')
        parts.append('<div class="skills-container">')
        for category, skills in skill_groups:
            joined = ", ".join(skills)
            parts.append(
                '<div class="skills-line">'
                f'<span class="skills-category">{_esc(category)}:</span> '
                f"{_esc(joined)}"
                "</div>"
            )
        parts.append("</div>")

    if cv.experience:
        parts.append('<h2 class="section-title">Experience</h2>')
        for entry in cv.experience:
            parts.append('<div class="resume-entry">')
            title = entry.role or entry.company or "Experience"
            parts.append(
                '<div class="resume-row">'
                f'<span class="title-main">{_esc(title)}</span>'
                f'<span class="meta-right">{_esc(entry.dates)}</span>'
                "</div>"
            )
            if entry.company and entry.role:
                parts.append(
                    '<div class="resume-row">'
                    f'<span class="title-sub">{_esc(entry.company)}</span>'
                    "</div>"
                )
            if entry.bullets:
                parts.append("<ul>")
                for bullet in entry.bullets:
                    parts.append(f"<li>{_esc(bullet)}</li>")
                parts.append("</ul>")
            parts.append("</div>")

    if cv.projects:
        parts.append('<h2 class="section-title">Projects</h2>')
        for project in cv.projects:
            parts.append('<div class="resume-entry">')
            if project.name:
                parts.append(
                    '<div class="resume-row">'
                    f'<span class="title-main">{_esc(project.name)}</span>'
                    "</div>"
                )
            if project.description:
                parts.append(f'<p class="entry-description">{_esc(project.description)}</p>')
            if project.bullets:
                parts.append("<ul>")
                for bullet in project.bullets:
                    parts.append(f"<li>{_esc(bullet)}</li>")
                parts.append("</ul>")
            parts.append("</div>")

    if cv.education:
        parts.append('<h2 class="section-title">Education</h2>')
        for edu in cv.education:
            parts.append('<div class="resume-entry">')
            heading = edu.degree or edu.institution
            parts.append(
                '<div class="resume-row">'
                f'<span class="title-main">{_esc(heading)}</span>'
                f'<span class="meta-right">{_esc(edu.dates)}</span>'
                "</div>"
            )
            if edu.institution and edu.degree:
                parts.append(
                    '<div class="resume-row">'
                    f'<span class="title-sub">{_esc(edu.institution)}</span>'
                    "</div>"
                )
            parts.append("</div>")

    if cv.certifications:
        parts.append('<h2 class="section-title">Certifications</h2>')
        parts.append("<ul>")
        for cert in cv.certifications:
            parts.append(f"<li>{_esc(cert)}</li>")
        parts.append("</ul>")

    parts.append("</div>")
    body = "\n".join(parts)
    title = cv.name or "CV"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8"/>\n'
        f'<meta name="resume-theme" content="{CV_TAILOR_THEME}"/>\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{CV_TAILOR_CSS}</style>\n"
        "</head>\n"
        f"<body>\n{body}\n</body>\n"
        "</html>\n"
    )


def render_tailored_cv_pdf(cv: TailoredCvData) -> bytes:
    """Render structured tailored CV to a styled PDF byte string."""
    document = structured_cv_to_html(cv)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            try:
                page = browser.new_page()
                page.set_content(document, wait_until="load")
                pdf_bytes = page.pdf(
                    format="A4",
                    margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"},
                    print_background=True,
                    prefer_css_page_size=True,
                )
            finally:
                browser.close()
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "browserType.launch" in message:
            raise PdfGeneratorError(
                "Playwright Chromium is not installed. Run: python -m playwright install chromium",
                status_code=503,
            ) from exc
        logger.exception("PDF generation failed")
        raise PdfGeneratorError(f"PDF generation failed: {message[:240]}", status_code=500) from exc

    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise PdfGeneratorError("PDF generation returned invalid output", status_code=500)

    logger.info("PDF generated (%d bytes)", len(pdf_bytes))
    return pdf_bytes


def pdf_filename_for_cv(cv: TailoredCvData) -> str:
    name = (cv.name or "tailored-cv").strip()
    safe = re.sub(r"[^\w\u0590-\u05FF]+", "_", name, flags=re.UNICODE).strip("_")
    return f"{safe or 'tailored-cv'}_CV_Tailored.pdf"
