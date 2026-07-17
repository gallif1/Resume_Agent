"""Tests for Playwright-based tailored CV PDF generation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import config
import pdf_generator_service as pdf
import tailor_cv_service as tailor


SAMPLE_CV = """# Gal Lifshitz

email@example.com | +972-50-000-0000 | linkedin.com/in/gal | github.com/gal

**Target Role: Backend Engineer**

## Experience

### Technical Support Engineer
Acme Corp | 2020 – Present

- Troubleshooting production systems with SQL and Python
- Automated recurring support workflows

### Intern
StartupX | 2019

- Built internal tooling for data intake

## Projects

### Personal Dashboard
Personal | 2021

- Designed a React dashboard with REST APIs

## Skills
Languages: Python, SQL
Tools: Git, Linux
"""


def test_markdown_to_html_has_resume_structure():
    html_doc = pdf.markdown_to_resume_html(SAMPLE_CV)
    assert "<!DOCTYPE html>" in html_doc
    assert 'class="header"' in html_doc
    assert "Gal Lifshitz" in html_doc
    assert "contact-info" in html_doc
    assert "target-role" in html_doc
    assert "section-title" in html_doc
    assert "resume-row" in html_doc
    assert "title-main" in html_doc
    assert "title-sub" in html_doc
    assert "meta-right" in html_doc
    assert "Technical Support Engineer" in html_doc
    assert "Acme Corp" in html_doc
    assert "2020" in html_doc
    assert "#1d4ed8" in html_doc
    assert "margin: 10mm 12mm 10mm 12mm" in html_doc
    assert "background-color: #f1f5f9" in html_doc
    assert "border-left: 3px solid #1d4ed8" in html_doc
    # Dates are on the same flex row as titles — not dump-style separate blocks only.
    assert 'class="meta-right"' in html_doc
    assert "<ul>" in html_doc and "<li>" in html_doc


def test_skills_lines_bold_category():
    md = """# Name

## Skills
Languages: Python, SQL
Tools: Git, Linux
"""
    html_doc = pdf.markdown_to_resume_html(md)
    assert "skills-line" in html_doc
    assert "skills-category" in html_doc
    assert "Python, SQL" in html_doc
    assert "Git, Linux" in html_doc


def test_parse_puts_dates_in_meta_right():
    parsed = pdf.parse_resume_markdown(SAMPLE_CV)
    exp = next(s for s in parsed.sections if s.kind == "experience")
    first = exp.entries[0]
    assert first.title == "Technical Support Engineer"
    assert first.subtitle == "Acme Corp"
    assert "2020" in first.dates
    assert "Present" in first.dates


def test_pdf_filename_from_name():
    assert pdf.pdf_filename_for_markdown(SAMPLE_CV) == "Gal_Lifshitz_CV_Tailored.pdf"
    assert pdf.pdf_filename_for_markdown("# No Name?") == "No_Name_CV_Tailored.pdf"
    assert pdf.pdf_filename_for_markdown("no heading") == pdf.DEFAULT_PDF_FILENAME


def test_plain_section_titles_are_not_dropped():
    """LLM often emits 'Experience' / 'Skills' without ## — PDF must still show body."""
    md = """# GAL LIFSHITZ

gal8054@gmail.com

**Target Role: Backend Developer**

Experience

### Backend Developer
Acme | 2021 – Present
- Built APIs with FastAPI and PostgreSQL

Skills
Python, SQL, Docker, AWS
"""
    parsed = pdf.parse_resume_markdown(md)
    assert parsed.name == "GAL LIFSHITZ"
    assert parsed.target_role == "Backend Developer"
    kinds = {s.kind for s in parsed.sections}
    assert "experience" in kinds
    assert "skills" in kinds
    html_doc = pdf.markdown_to_resume_html(md)
    assert "Built APIs with FastAPI" in html_doc
    assert "PostgreSQL" in html_doc
    assert "Python" in html_doc
    assert "section-title" in html_doc


def test_bold_section_titles_parsed():
    md = """# Gal Lifshitz
gal@example.com
**Target Role: Backend Engineer**

**Experience**
### Support Engineer
Acme | 2020 – Present
- SQL and Python troubleshooting

**Skills**
Languages: Python, SQL
"""
    html_doc = pdf.markdown_to_resume_html(md)
    assert "SQL and Python troubleshooting" in html_doc
    assert "Languages" in html_doc


def test_header_only_with_body_text_uses_fallback_or_summary():
    """Content without recognizable headings must not produce a blank PDF body."""
    md = """# GAL LIFSHITZ
gal8054@gmail.com
**Target Role: Backend Developer**

Backend Developer at Acme (2021-Present).
Built REST APIs with FastAPI, SQLAlchemy and PostgreSQL.
Skills include Python, Docker and AWS.
"""
    html_doc = pdf.markdown_to_resume_html(md)
    assert "GAL LIFSHITZ" in html_doc
    assert "FastAPI" in html_doc
    assert "PostgreSQL" in html_doc


def test_duplicate_summary_heading_collapsed():
    md = """# Gal Lifshitz
gal@example.com
**Target Role: Backend Engineer**

## Summary
Backend-leaning engineer with FastAPI experience.

## Summary
Backend-leaning engineer with FastAPI experience. Extra fluff sentence two. Extra fluff sentence three. Extra fluff sentence four should be trimmed.

## Experience
### Support Specialist
Acme Corp | 2021 – Present
- Supported ERP customers
- Wrote SQL reports
- Automated triage with Python
- Extra bullet four
- Extra bullet five that should be capped

## Projects
### Tribe Platform
Personal | 2023
- Built a React Native app with Expo

## Military Service

## Awards

## Skills
Cloud/DevOps: Docker, AWS, SQLAlchemy, Expo
Languages: Python, SQL
"""
    parsed = pdf.parse_resume_markdown(md)
    summary_sections = [s for s in parsed.sections if s.kind == "summary"]
    assert len(summary_sections) == 1
    # Ghost sections omitted.
    titles = [s.title.lower() for s in parsed.sections]
    assert not any("military" in t for t in titles)
    assert not any("award" in t for t in titles)
    # Real employment kept; project not duplicated into experience.
    exp = next(s for s in parsed.sections if s.kind == "experience")
    assert any("Acme" in (e.subtitle or e.title) for e in exp.entries)
    assert all("Tribe" not in (e.title or "") for e in exp.entries)
    assert len(exp.entries[0].bullets) <= 4
    # Summary capped roughly to 3 sentences.
    summary_text = " ".join(summary_sections[0].paragraphs)
    assert summary_text.count(".") <= 3

    html_doc = pdf.markdown_to_resume_html(md)
    assert html_doc.lower().count(">summary<") == 1
    assert "Military" not in html_doc
    assert "Awards" not in html_doc
    assert "Acme Corp" in html_doc
    assert "SQLAlchemy" in html_doc
    # SQLAlchemy should not remain under a Cloud/DevOps label.
    assert not re.search(
        r"Cloud\s*/\s*DevOps:</span>\s*[^<]*SQLAlchemy",
        html_doc,
        flags=re.IGNORECASE,
    )
    assert "Frameworks / Libraries" in html_doc
    assert "Mobile" in html_doc


def test_one_page_css_contract():
    html_doc = pdf.markdown_to_resume_html(SAMPLE_CV)
    assert "margin: 10mm 12mm 10mm 12mm" in html_doc
    assert "line-height: 1.35" in html_doc
    assert "font-size: 9.5pt" in html_doc
    assert "background-color: #f1f5f9" in html_doc
    assert "border-left: 3px solid #1d4ed8" in html_doc
    assert 'class="skills-container"' in html_doc


def test_render_markdown_to_pdf_bytes():
    pytest.importorskip("playwright")
    try:
        pdf_bytes = pdf.render_markdown_to_pdf(SAMPLE_CV)
    except pdf.PdfGeneratorError as exc:
        if "Playwright" in exc.message or "chromium" in exc.message.lower():
            pytest.skip(exc.message)
        raise
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_generate_uses_cv_body_only(
    cvs_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Saved tailor docs include analysis preamble; PDF must use resume body."""
    monkeypatch.setattr(config, "CVS_DIR", cvs_dir)
    full = """## פירוט שינויים
- change

## ציון התאמה למשרה
**ציון משוער: 70/100**

---

## קורות החיים המעודכנים

""" + SAMPLE_CV
    tailor.save_tailored_cv("cv_pdf", 11, full)
    saved = tailor.load_saved_tailored_cv("cv_pdf", 11)
    assert saved is not None
    body = tailor.extract_cv_markdown_for_copy(saved)
    assert body.startswith("# Gal")
    assert "פירוט שינויים" not in body

    try:
        pdf_bytes, filename = pdf.generate_tailored_cv_pdf(body)
    except pdf.PdfGeneratorError as exc:
        if "Playwright" in exc.message or "chromium" in exc.message.lower():
            pytest.skip(exc.message)
        raise
    assert pdf_bytes.startswith(b"%PDF")
    assert filename == "Gal_Lifshitz_CV_Tailored.pdf"
