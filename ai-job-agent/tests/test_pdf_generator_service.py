"""Tests for Playwright-based tailored CV PDF generation."""

from __future__ import annotations

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
- Designed a React dashboard with REST APIs

## Skills
Python | SQL | Linux | Git
"""


def test_markdown_to_html_has_resume_structure():
    html_doc = pdf.markdown_to_resume_html(SAMPLE_CV)
    assert "<!DOCTYPE html>" in html_doc
    assert 'class="resume"' in html_doc
    assert "Gal Lifshitz" in html_doc
    assert "contact-bar" in html_doc
    assert "experience-item" in html_doc
    assert "#1a365d" in html_doc  # section accent in CSS
    assert "page-break-inside: avoid" in html_doc


def test_pdf_filename_from_name():
    assert pdf.pdf_filename_for_markdown(SAMPLE_CV) == "Gal_Lifshitz_CV_Tailored.pdf"
    assert pdf.pdf_filename_for_markdown("# No Name?") == "No_Name_CV_Tailored.pdf"
    assert pdf.pdf_filename_for_markdown("no heading") == pdf.DEFAULT_PDF_FILENAME


def test_empty_markdown_raises():
    with pytest.raises(pdf.PdfGeneratorError) as exc:
        pdf.markdown_to_resume_html("   ")
    assert exc.value.status_code == 400


def test_render_markdown_to_pdf_bytes():
    pdf_bytes = pdf.render_markdown_to_pdf(SAMPLE_CV)
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

    pdf_bytes, filename = pdf.generate_tailored_cv_pdf(body)
    assert pdf_bytes.startswith(b"%PDF")
    assert filename == "Gal_Lifshitz_CV_Tailored.pdf"
