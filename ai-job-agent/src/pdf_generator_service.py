"""Render tailored CV Markdown to a professional A4 PDF via Playwright."""

from __future__ import annotations

import html
import re

import markdown as md_lib
from bs4 import BeautifulSoup, NavigableString, Tag
from playwright.sync_api import sync_playwright

PDF_MARGIN = {"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"}
DEFAULT_PDF_FILENAME = "Gal_Lifshitz_CV_Tailored.pdf"

CONTACT_HINT_RE = re.compile(
    r"(@|\||linkedin\.com|github\.com|mailto:|\+?\d[\d\s().-]{6,}\d)",
    re.IGNORECASE,
)
NAME_FROM_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

RESUME_CSS = """
@page {
  size: A4;
  margin: 0;
}

* {
  box-sizing: border-box;
}

html, body {
  margin: 0;
  padding: 0;
  background: #ffffff;
  color: #222222;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.45;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

.resume {
  max-width: 100%;
  color: #222222;
}

/* ---- Header / name ---- */
.resume > h1:first-child,
.resume-header h1 {
  margin: 0 0 6pt;
  padding: 0;
  font-size: 21pt;
  font-weight: 700;
  letter-spacing: 0.01em;
  text-align: center;
  color: #1a1a1a;
  line-height: 1.2;
}

.contact-bar {
  margin: 0 0 14pt;
  padding: 0 0 10pt;
  text-align: center;
  font-size: 9pt;
  line-height: 1.4;
  color: #4a5568;
  border-bottom: 1.5px solid #e2e8f0;
}

.contact-bar a {
  color: #4a5568;
  text-decoration: none;
}

.contact-sep {
  margin: 0 0.35em;
  color: #a0aec0;
}

/* ---- Section headers ---- */
.resume h2 {
  margin: 12pt 0 5pt;
  padding: 0 0 3pt;
  font-size: 12.5pt;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #1a365d;
  border-bottom: 1px solid #e2e8f0;
  line-height: 1.3;
  page-break-after: avoid;
  break-after: avoid;
}

.resume h3 {
  margin: 8pt 0 2pt;
  padding: 0;
  font-size: 11pt;
  font-weight: 650;
  color: #222222;
  line-height: 1.35;
  page-break-after: avoid;
  break-after: avoid;
}

.resume h4 {
  margin: 6pt 0 2pt;
  font-size: 10.5pt;
  font-weight: 600;
  color: #2d3748;
  page-break-after: avoid;
  break-after: avoid;
}

/* ---- Body text ---- */
.resume p {
  margin: 0 0 5pt;
  color: #222222;
}

.resume a {
  color: #1a365d;
  text-decoration: none;
}

.resume strong, .resume b {
  font-weight: 650;
  color: #1a1a1a;
}

.resume em, .resume i {
  font-style: italic;
}

.resume hr {
  border: 0;
  border-top: 1px solid #e2e8f0;
  margin: 10pt 0;
}

/* ---- Lists ---- */
.resume ul,
.resume ol {
  margin: 2pt 0 6pt;
  padding-left: 1.15em;
  padding-inline-start: 1.15em;
}

.resume li {
  margin: 0 0 2.5pt;
  padding: 0;
  color: #222222;
}

.resume li::marker {
  color: #4a5568;
}

.resume ul ul,
.resume ol ol,
.resume ul ol,
.resume ol ul {
  margin-top: 2pt;
  margin-bottom: 2pt;
}

/* ---- Meta / secondary text (dates, company lines) ---- */
.resume .meta,
.resume p.role-meta {
  color: #718096;
  font-size: 9.5pt;
  margin: 0 0 3pt;
}

/* ---- Experience / project blocks: never split awkwardly ---- */
.experience-item,
.project-item,
.resume li {
  page-break-inside: avoid;
  break-inside: avoid;
}

.experience-item,
.project-item {
  margin: 0 0 6pt;
}

.experience-item > h3:first-child,
.project-item > h3:first-child {
  margin-top: 6pt;
}

/* Keep heading with following content */
.resume h2 + *,
.resume h3 + * {
  page-break-before: avoid;
  break-before: avoid;
}

/* ---- Target role line ---- */
.resume p:has(strong:first-child),
.resume .target-role {
  margin: 0 0 8pt;
  font-size: 10pt;
  color: #2d3748;
}

/* ---- Compact tables (skills matrices) ---- */
.resume table {
  width: 100%;
  border-collapse: collapse;
  margin: 4pt 0 8pt;
  font-size: 9.5pt;
}

.resume th,
.resume td {
  text-align: left;
  padding: 2pt 6pt 2pt 0;
  vertical-align: top;
  border: 0;
}

.resume th {
  color: #1a365d;
  font-weight: 650;
}

/* ---- Code / inline tech tokens ---- */
.resume code {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 9pt;
  background: transparent;
  color: #222222;
}
"""


class PdfGeneratorError(RuntimeError):
    """Raised when PDF generation fails."""

    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def extract_candidate_name(markdown: str) -> str | None:
    """Return the first Markdown H1 as the candidate name, if present."""
    match = NAME_FROM_H1_RE.search(markdown or "")
    if not match:
        return None
    name = match.group(1).strip()
    # Strip inline markdown markers
    name = re.sub(r"[*_`]+", "", name).strip()
    return name or None


def pdf_filename_for_markdown(markdown: str) -> str:
    """Build a Content-Disposition filename from the CV name."""
    name = extract_candidate_name(markdown)
    if not name:
        return DEFAULT_PDF_FILENAME
    safe = re.sub(r"[^\w\u0590-\u05FF]+", "_", name, flags=re.UNICODE).strip("_")
    if not safe:
        return DEFAULT_PDF_FILENAME
    return f"{safe}_CV_Tailored.pdf"


def _looks_like_contact_line(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 280:
        return False
    return bool(CONTACT_HINT_RE.search(cleaned))


def _decorate_contact_bar(paragraph: Tag) -> None:
    """Turn a contact paragraph into a centered bar with | separators."""
    paragraph["class"] = list(paragraph.get("class") or []) + ["contact-bar"]
    # Soften bare links already present; leave structure intact.
    for anchor in paragraph.find_all("a"):
        if not (anchor.get_text(strip=True)):
            continue
        href = (anchor.get("href") or "").lower()
        if "mailto:" in href or "linkedin" in href or "github" in href or "tel:" in href:
            continue


def _wrap_break_avoid_blocks(soup: BeautifulSoup) -> None:
    """Wrap each h3 (+ following siblings until next h2/h3) to avoid page splits."""
    resume = soup.find(class_="resume")
    if not isinstance(resume, Tag):
        return

    headings = [
        child
        for child in list(resume.children)
        if isinstance(child, Tag) and child.name == "h3"
    ]
    for h3 in headings:
        parent = h3.parent
        if not isinstance(parent, Tag):
            continue
        # Already wrapped
        if parent.name == "div" and "experience-item" in (parent.get("class") or []):
            continue

        wrapper = soup.new_tag("div", attrs={"class": "experience-item"})
        h3.insert_before(wrapper)
        wrapper.append(h3)

        sibling = wrapper.next_sibling
        while sibling is not None:
            nxt = sibling.next_sibling
            if isinstance(sibling, Tag) and sibling.name in ("h2", "h3"):
                break
            if isinstance(sibling, NavigableString) and not str(sibling).strip():
                sibling = nxt
                continue
            wrapper.append(sibling)
            sibling = nxt


def _postprocess_html(fragment: str) -> str:
    """Add resume semantics: contact bar, page-break wrappers."""
    soup = BeautifulSoup(f'<div class="resume">{fragment}</div>', "html.parser")
    resume = soup.find(class_="resume")
    if not isinstance(resume, Tag):
        return fragment

    h1 = resume.find("h1")
    if isinstance(h1, Tag):
        for sib in h1.next_siblings:
            if isinstance(sib, NavigableString) and not str(sib).strip():
                continue
            if isinstance(sib, Tag) and sib.name == "p" and _looks_like_contact_line(sib.get_text(" ", strip=True)):
                _decorate_contact_bar(sib)
            break

    _wrap_break_avoid_blocks(soup)
    return str(resume)


def markdown_to_resume_html(markdown: str) -> str:
    """Convert CV Markdown into a full HTML document with print CSS."""
    raw = (markdown or "").strip()
    if not raw:
        raise PdfGeneratorError("קורות החיים ריקים — אין מה להמיר ל-PDF", status_code=400)

    body_fragment = md_lib.markdown(
        raw,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )
    resume_html = _postprocess_html(body_fragment)
    title = extract_candidate_name(raw) or "CV"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{RESUME_CSS}</style>\n"
        "</head>\n"
        f"<body>{resume_html}</body>\n"
        "</html>\n"
    )


def render_markdown_to_pdf(markdown: str) -> bytes:
    """Render Markdown to an A4 PDF buffer using headless Chromium."""
    document = markdown_to_resume_html(markdown)
    try:
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
                    margin=PDF_MARGIN,
                    print_background=True,
                )
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 — surface as a clean API error
        message = str(exc)
        if "Executable doesn't exist" in message or "browserType.launch" in message:
            raise PdfGeneratorError(
                "דפדפן השרת (Playwright Chromium) לא מותקן. "
                "יש להריץ `python -m playwright install chromium`.",
                status_code=503,
            ) from exc
        raise PdfGeneratorError(
            f"שגיאה ביצירת PDF: {message[:240]}",
            status_code=500,
        ) from exc

    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise PdfGeneratorError("יצירת ה-PDF נכשלה — פלט לא תקין", status_code=500)
    return pdf_bytes


def generate_tailored_cv_pdf(markdown: str) -> tuple[bytes, str]:
    """Return (pdf_bytes, download_filename) for a tailored CV body."""
    pdf_bytes = render_markdown_to_pdf(markdown)
    return pdf_bytes, pdf_filename_for_markdown(markdown)
