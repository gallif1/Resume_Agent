"""Render tailored CV Markdown to a professional A4 PDF via Playwright."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

# Margins come from @page CSS; Playwright gets zero so CSS owns the page box.
PDF_MARGIN = {"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
DEFAULT_PDF_FILENAME = "Gal_Lifshitz_CV_Tailored.pdf"

NAME_FROM_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
CONTACT_HINT_RE = re.compile(
    r"(@|\||linkedin\.com|github\.com|mailto:|\+?\d[\d\s().-]{6,}\d)",
    re.IGNORECASE,
)
DATE_HINT_RE = re.compile(
    r"(?:"
    r"\b(?:19|20)\d{2}\b"
    r"|present|current|ongoing|לפני|היום|עד כה"
    r"|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?"
    r"|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r"|ינואר|פברואר|מרץ|אפריל|מאי|יוני|יולי|אוגוסט|ספטמבר|אוקטובר|נובמבר|דצמבר"
    r")",
    re.IGNORECASE,
)
TARGET_ROLE_RE = re.compile(
    r"^\s*(?:\*\*)?\s*target\s*role\s*:\s*(.+?)(?:\*\*)?\s*$",
    re.IGNORECASE,
)
SKILLS_HEADING_RE = re.compile(
    r"skills|כישורים|טכנולוגיות|technologies|technical\s+skills",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+)$")
MD_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")

RESUME_CSS = """
@page {
    size: A4;
    margin: 15mm 15mm 15mm 15mm;
}
* {
    box-sizing: border-box;
}
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #1e293b;
    line-height: 1.5;
    font-size: 10pt;
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}
.header {
    text-align: center;
    margin-bottom: 4mm;
}
.header h1 {
    font-size: 24pt;
    font-weight: 700;
    color: #0f172a;
    margin: 0 0 1mm 0;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.contact-info {
    font-size: 9.5pt;
    color: #475569;
    margin-bottom: 1mm;
}
.contact-info a {
    color: #475569;
    text-decoration: none;
}
.target-role {
    font-size: 11pt;
    font-weight: 600;
    color: #1d4ed8;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin: 0;
}
h2.section-title {
    font-size: 11pt;
    font-weight: 700;
    color: #0f172a;
    border-bottom: 1.5px solid #cbd5e1;
    text-transform: uppercase;
    margin: 4mm 0 2mm 0;
    padding-bottom: 0.5mm;
    letter-spacing: 0.5px;
    page-break-after: avoid;
    break-after: avoid;
}
.resume-entry {
    margin: 0 0 2.5mm 0;
    page-break-inside: avoid;
    break-inside: avoid;
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
    color: #0f172a;
    font-size: 10.5pt;
}
.title-sub {
    font-weight: 600;
    color: #475569;
    font-style: italic;
}
.meta-right {
    font-size: 9.5pt;
    color: #64748b;
    font-weight: 500;
    text-align: right;
    white-space: nowrap;
    flex-shrink: 0;
}
ul {
    margin: 0.5mm 0 2.5mm 0;
    padding-left: 4.5mm;
}
li {
    margin-bottom: 0.8mm;
    color: #334155;
    text-align: left;
}
li::marker {
    color: #64748b;
}
.skills-line {
    margin-bottom: 1mm;
    font-size: 10pt;
    color: #334155;
}
.skills-category {
    font-weight: 700;
    color: #0f172a;
}
.summary-text {
    margin: 0 0 2mm 0;
    color: #334155;
}
"""


class PdfGeneratorError(RuntimeError):
    """Raised when PDF generation fails."""

    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class ResumeEntry:
    title: str
    dates: str = ""
    subtitle: str = ""
    location: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class ResumeSection:
    title: str
    kind: str  # experience | projects | skills | summary | other
    entries: list[ResumeEntry] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    skill_lines: list[tuple[str, str]] = field(default_factory=list)
    flat_skills: str = ""


@dataclass
class ParsedResume:
    name: str = ""
    contact: str = ""
    target_role: str = ""
    sections: list[ResumeSection] = field(default_factory=list)


def extract_candidate_name(markdown: str) -> str | None:
    """Return the first Markdown H1 as the candidate name, if present."""
    match = NAME_FROM_H1_RE.search(markdown or "")
    if not match:
        return None
    name = match.group(1).strip()
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


def _strip_md_inline(text: str) -> str:
    value = (text or "").strip()
    value = MD_LINK_RE.sub(r"\1", value)
    value = MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", value)
    value = MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", value)
    value = value.replace("`", "")
    return value.strip()


def _inline_html(text: str) -> str:
    """Escape text but keep simple bold markers as <strong>."""
    value = (text or "").strip()
    value = MD_LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">'
        f"{html.escape(m.group(1))}</a>",
        value,
    )
    parts: list[str] = []
    cursor = 0
    for match in MD_BOLD_RE.finditer(value):
        parts.append(html.escape(value[cursor: match.start()]))
        inner = match.group(1) or match.group(2) or ""
        parts.append(f"<strong>{html.escape(inner)}</strong>")
        cursor = match.end()
    parts.append(html.escape(value[cursor:]))
    return "".join(parts)


def _looks_like_contact_line(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 280:
        return False
    return bool(CONTACT_HINT_RE.search(cleaned))


def _looks_like_date_token(text: str) -> bool:
    return bool(DATE_HINT_RE.search(text or ""))


def _split_company_and_dates(text: str) -> tuple[str, str]:
    """Split 'Company | 2020 – Present' into (company, dates)."""
    raw = _strip_md_inline(text)
    if not raw:
        return "", ""

    for sep in ("|", "•", "·"):
        if sep in raw:
            left, right = [p.strip() for p in raw.split(sep, 1)]
            if _looks_like_date_token(right) and not _looks_like_date_token(left):
                return left, right
            if _looks_like_date_token(left) and not _looks_like_date_token(right):
                return right, left
            # "City | Country" without dates — treat whole as subtitle.
            if not _looks_like_date_token(left) and not _looks_like_date_token(right):
                return raw, ""
            return left, right

    paren = re.match(r"^(.*?)\s*[(（]([^)）]+)[)）]\s*$", raw)
    if paren and _looks_like_date_token(paren.group(2)):
        return paren.group(1).strip(), paren.group(2).strip()

    date_match = re.search(
        r"((?:(?:19|20)\d{2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec).+)$",
        raw,
        re.IGNORECASE,
    )
    if date_match and date_match.start() > 2:
        return raw[: date_match.start()].strip(" -–—,"), date_match.group(1).strip()

    if _looks_like_date_token(raw) and len(raw) < 48:
        return "", raw
    return raw, ""


def _section_kind(title: str) -> str:
    t = (title or "").lower()
    if SKILLS_HEADING_RE.search(t):
        return "skills"
    if any(k in t for k in ("experience", "employment", "work history", "ניסיון", "תעסוק")):
        return "experience"
    if any(k in t for k in ("project", "פרויקט")):
        return "projects"
    if any(k in t for k in ("summary", "profile", "objective", "תקציר", "אודות")):
        return "summary"
    if any(k in t for k in ("education", "השכלה", "academic")):
        return "education"
    return "other"


def _looks_like_skill_category_line(text: str) -> bool:
    if ":" not in text:
        return False
    left, right = text.split(":", 1)
    return bool(left.strip()) and bool(right.strip()) and len(left.strip()) < 40


def parse_resume_markdown(markdown: str) -> ParsedResume:
    """Parse tailored CV markdown into a structured resume model."""
    resume = ParsedResume()
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    current_section: ResumeSection | None = None
    current_entry: ResumeEntry | None = None
    header_done = False

    def ensure_section(title: str) -> ResumeSection:
        nonlocal current_section, current_entry, header_done
        header_done = True
        current_entry = None
        current_section = ResumeSection(title=title, kind=_section_kind(title))
        resume.sections.append(current_section)
        return current_section

    def close_entry() -> None:
        nonlocal current_entry
        current_entry = None

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        i += 1

        if not line or line == "---":
            continue

        heading = MD_HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            text = _strip_md_inline(heading.group(2))
            if level == 1 and not resume.name:
                resume.name = text
                continue
            if level == 2:
                ensure_section(text)
                continue
            if level == 3:
                if current_section is None:
                    ensure_section("Experience")
                assert current_section is not None
                title, embedded_dates = _split_company_and_dates(text)
                current_entry = ResumeEntry(
                    title=title or text,
                    dates=embedded_dates,
                )
                current_section.entries.append(current_entry)
                continue

        # Header contact / target role before first section.
        if not header_done and current_section is None:
            target = TARGET_ROLE_RE.match(_strip_md_inline(line))
            if target:
                resume.target_role = target.group(1).strip()
                continue
            if _looks_like_contact_line(line) and not resume.contact:
                resume.contact = _strip_md_inline(line)
                continue
            # Ignore stray preamble lines in header zone.
            if not resume.name:
                resume.name = _strip_md_inline(line)
            continue

        if current_section is None:
            ensure_section("Summary")

        assert current_section is not None

        bullet = BULLET_RE.match(raw_line)
        if bullet:
            text = _strip_md_inline(bullet.group(1))
            if current_entry is None:
                current_entry = ResumeEntry(title="")
                current_section.entries.append(current_entry)
            current_entry.bullets.append(text)
            continue

        plain = _strip_md_inline(line)
        target = TARGET_ROLE_RE.match(plain)
        if target and not resume.target_role:
            resume.target_role = target.group(1).strip()
            continue

        if current_section.kind == "skills":
            if _looks_like_skill_category_line(plain):
                left, right = plain.split(":", 1)
                current_section.skill_lines.append(
                    (left.strip().rstrip(":"), right.strip())
                )
            else:
                if current_section.flat_skills:
                    current_section.flat_skills += " · " + plain
                else:
                    current_section.flat_skills = plain
            continue

        # Meta line under an entry title (company | dates).
        if (
            current_entry is not None
            and not current_entry.bullets
            and len(plain) < 160
            and (
                "|" in plain
                or _looks_like_date_token(plain)
                or (not current_entry.subtitle and not current_entry.dates)
            )
        ):
            company, dates = _split_company_and_dates(plain)
            # Second meta line may be location.
            if current_entry.subtitle and not current_entry.location and not dates:
                current_entry.location = company or plain
            else:
                if company and not current_entry.subtitle:
                    current_entry.subtitle = company
                if dates and not current_entry.dates:
                    current_entry.dates = dates
                if company and current_entry.subtitle and dates and not current_entry.location:
                    # Prefer dates on first row; keep company as subtitle.
                    pass
            continue

        if current_section.kind in {"summary", "other"} and current_entry is None:
            current_section.paragraphs.append(plain)
            continue

        # Free text under an entry → treat as a soft bullet.
        if current_entry is not None:
            current_entry.bullets.append(plain)
        else:
            current_section.paragraphs.append(plain)

    return resume


def _render_resume_row(left_class: str, left_text: str, right_text: str = "") -> str:
    left = f'<span class="{left_class}">{html.escape(left_text)}</span>' if left_text else "<span></span>"
    right = (
        f'<span class="meta-right">{html.escape(right_text)}</span>'
        if right_text
        else ""
    )
    return f'<div class="resume-row">{left}{right}</div>'


def _render_entry(entry: ResumeEntry) -> str:
    parts: list[str] = ['<div class="resume-entry">']
    if entry.title or entry.dates:
        parts.append(_render_resume_row("title-main", entry.title, entry.dates))
    if entry.subtitle or entry.location:
        parts.append(_render_resume_row("title-sub", entry.subtitle, entry.location))
    if entry.bullets:
        parts.append("<ul>")
        for bullet in entry.bullets:
            parts.append(f"<li>{_inline_html(bullet)}</li>")
        parts.append("</ul>")
    parts.append("</div>")
    return "\n".join(parts)


def _render_section(section: ResumeSection) -> str:
    chunks = [f'<h2 class="section-title">{html.escape(section.title)}</h2>']

    if section.kind == "skills":
        if section.skill_lines:
            for category, values in section.skill_lines:
                chunks.append(
                    '<div class="skills-line">'
                    f'<span class="skills-category">{html.escape(category)}:</span> '
                    f"{html.escape(values)}"
                    "</div>"
                )
        elif section.flat_skills:
            chunks.append(
                f'<div class="skills-line">{html.escape(section.flat_skills)}</div>'
            )
        return "\n".join(chunks)

    for paragraph in section.paragraphs:
        chunks.append(f'<p class="summary-text">{_inline_html(paragraph)}</p>')

    for entry in section.entries:
        # Skip empty placeholder entries.
        if not (entry.title or entry.subtitle or entry.bullets):
            continue
        chunks.append(_render_entry(entry))

    return "\n".join(chunks)


def parsed_resume_to_html(resume: ParsedResume) -> str:
    """Render a ParsedResume into the Playwright print HTML document."""
    body_parts: list[str] = ['<div class="resume">']

    header_parts = ['<div class="header">']
    if resume.name:
        header_parts.append(f"<h1>{html.escape(resume.name)}</h1>")
    if resume.contact:
        header_parts.append(
            f'<div class="contact-info">{html.escape(resume.contact)}</div>'
        )
    if resume.target_role:
        role = resume.target_role
        if not role.lower().startswith("target role"):
            role_display = role
        else:
            role_display = role
        header_parts.append(
            f'<p class="target-role">{html.escape(role_display)}</p>'
        )
    header_parts.append("</div>")
    body_parts.append("\n".join(header_parts))

    for section in resume.sections:
        body_parts.append(_render_section(section))

    body_parts.append("</div>")
    body = "\n".join(body_parts)
    title = resume.name or "CV"

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8"/>\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{RESUME_CSS}</style>\n"
        "</head>\n"
        f"<body>\n{body}\n</body>\n"
        "</html>\n"
    )


def markdown_to_resume_html(markdown: str) -> str:
    """Convert CV Markdown into a structured HTML document with print CSS."""
    raw = (markdown or "").strip()
    if not raw:
        raise PdfGeneratorError("קורות החיים ריקים — אין מה להמיר ל-PDF", status_code=400)

    parsed = parse_resume_markdown(raw)
    if not parsed.name and not parsed.sections:
        raise PdfGeneratorError("קורות החיים ריקים — אין מה להמיר ל-PDF", status_code=400)
    return parsed_resume_to_html(parsed)


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
                    prefer_css_page_size=True,
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
