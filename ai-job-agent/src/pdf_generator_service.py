"""Render tailored CV Markdown to a professional A4 PDF via Playwright."""

from __future__ import annotations

import html
import re

import markdown as md_lib
from bs4 import BeautifulSoup, NavigableString, Tag
from playwright.sync_api import sync_playwright

# Margins come from @page CSS; Playwright gets zero so CSS owns the page box.
PDF_MARGIN = {"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
DEFAULT_PDF_FILENAME = "Gal_Lifshitz_CV_Tailored.pdf"

CONTACT_HINT_RE = re.compile(
    r"(@|\||linkedin\.com|github\.com|mailto:|\+?\d[\d\s().-]{6,}\d)",
    re.IGNORECASE,
)
NAME_FROM_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
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
META_SPLIT_RE = re.compile(r"\s*[|–—•·]\s*")

RESUME_CSS = """
@page {
    size: A4;
    margin: 16mm 16mm 16mm 16mm;
}
* {
    box-sizing: border-box;
}
body {
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1e293b;
    line-height: 1.4;
    font-size: 10.5pt;
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}
.resume {
    max-width: 100%;
}
/* Header Section */
.header {
    text-align: center;
    margin-bottom: 5mm;
}
.header h1 {
    font-size: 22pt;
    font-weight: 700;
    color: #0f172a;
    margin: 0 0 1.5mm 0;
    line-height: 1.15;
}
.contact-info {
    font-size: 9.5pt;
    color: #475569;
    margin: 0;
}
.contact-info a {
    color: #475569;
    text-decoration: none;
}
.target-role {
    font-size: 11pt;
    font-weight: 600;
    color: #2563eb;
    margin-top: 1mm;
    margin-bottom: 0;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
/* Section Layouts */
h2.section-title {
    font-size: 12pt;
    font-weight: 700;
    color: #0f172a;
    border-bottom: 1.5px solid #cbd5e1;
    text-transform: uppercase;
    margin: 5mm 0 2.5mm 0;
    padding-bottom: 0.5mm;
    letter-spacing: 0.3px;
    page-break-after: avoid;
    break-after: avoid;
}
/* Flex/Grid Rows for Work Experience & Projects */
.experience-item,
.project-item {
    margin: 0 0 3.5mm 0;
    page-break-inside: avoid;
    break-inside: avoid;
}
.entry-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 4mm;
    margin-bottom: 0.5mm;
}
.entry-title {
    font-weight: 700;
    color: #0f172a;
    font-size: 10.5pt;
}
.entry-subtitle {
    font-weight: 600;
    color: #475569;
    font-style: italic;
    font-size: 10pt;
}
.entry-meta {
    font-size: 9.5pt;
    color: #64748b;
    text-align: right;
    font-weight: 500;
    white-space: nowrap;
    flex-shrink: 0;
}
/* Bullet Points */
ul {
    margin: 1mm 0 3mm 0;
    padding-left: 5mm;
}
li {
    margin-bottom: 1mm;
    color: #334155;
}
li::marker {
    color: #64748b;
}
/* Skills Grid */
.skills-container {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 1.5mm 4mm;
    margin-top: 1mm;
    align-items: baseline;
}
.skills-category {
    font-weight: 700;
    color: #0f172a;
}
.skills-values {
    color: #334155;
}
.skills-flat {
    margin: 1mm 0 0 0;
    color: #334155;
}
/* Misc body */
.resume p {
    margin: 0 0 2mm 0;
    color: #334155;
}
.resume a {
    color: #2563eb;
    text-decoration: none;
}
.resume strong, .resume b {
    font-weight: 700;
    color: #0f172a;
}
.resume hr {
    border: 0;
    border-top: 1px solid #e2e8f0;
    margin: 3mm 0;
}
.resume h3 {
    margin: 0;
    font-size: 10.5pt;
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


def _looks_like_date_token(text: str) -> bool:
    return bool(DATE_HINT_RE.search(text or ""))


def _split_company_and_dates(text: str) -> tuple[str, str]:
    """Split 'Company | 2020 – Present' into (company, dates)."""
    raw = (text or "").strip()
    if not raw:
        return "", ""

    # Prefer explicit pipe / en-dash style separators.
    for sep in ("|", "•", "·"):
        if sep in raw:
            left, right = [p.strip() for p in raw.split(sep, 1)]
            if _looks_like_date_token(right) and not _looks_like_date_token(left):
                return left, right
            if _looks_like_date_token(left) and not _looks_like_date_token(right):
                return right, left
            return left, right

    # Trailing date range in parentheses: Company (2020-2022)
    paren = re.match(r"^(.*?)\s*[(（]([^)）]+)[)）]\s*$", raw)
    if paren and _looks_like_date_token(paren.group(2)):
        return paren.group(1).strip(), paren.group(2).strip()

    # "Company 2020 – Present"
    date_match = re.search(
        r"((?:(?:19|20)\d{2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r".+)$",
        raw,
        re.IGNORECASE,
    )
    if date_match and date_match.start() > 2:
        return raw[: date_match.start()].strip(" -–—,"), date_match.group(1).strip()

    if _looks_like_date_token(raw) and len(raw) < 40:
        return "", raw
    return raw, ""


def _is_target_role_node(node: Tag) -> bool:
    text = node.get_text(" ", strip=True)
    return bool(TARGET_ROLE_RE.match(text))


def _decorate_target_role(node: Tag) -> None:
    text = node.get_text(" ", strip=True)
    match = TARGET_ROLE_RE.match(text)
    role = match.group(1).strip() if match else text
    node.clear()
    node["class"] = list(dict.fromkeys((node.get("class") or []) + ["target-role"]))
    node.append(role)


def _make_entry_row(soup: BeautifulSoup, left_html: str, right_text: str = "") -> Tag:
    row = soup.new_tag("div", attrs={"class": "entry-row"})
    left = soup.new_tag("div")
    left.append(BeautifulSoup(left_html, "html.parser"))
    row.append(left)
    if right_text:
        meta = soup.new_tag("div", attrs={"class": "entry-meta"})
        meta.string = right_text
        row.append(meta)
    return row


def _wrap_header(soup: BeautifulSoup, resume: Tag) -> None:
    h1 = resume.find("h1")
    if not isinstance(h1, Tag):
        return

    header = soup.new_tag("div", attrs={"class": "header"})
    h1.insert_before(header)
    header.append(h1)

    sibling = header.next_sibling
    while sibling is not None:
        nxt = sibling.next_sibling
        if isinstance(sibling, NavigableString) and not str(sibling).strip():
            sibling = nxt
            continue
        if not isinstance(sibling, Tag):
            break
        if sibling.name == "p" and _looks_like_contact_line(sibling.get_text(" ", strip=True)):
            sibling["class"] = list(
                dict.fromkeys((sibling.get("class") or []) + ["contact-info"])
            )
            header.append(sibling)
            sibling = nxt
            continue
        if sibling.name in ("p", "h3") and _is_target_role_node(sibling):
            _decorate_target_role(sibling)
            header.append(sibling)
            sibling = nxt
            continue
        # Also catch plain strong target-role inside a short paragraph
        if sibling.name == "p":
            text = sibling.get_text(" ", strip=True)
            if TARGET_ROLE_RE.match(text):
                _decorate_target_role(sibling)
                header.append(sibling)
                sibling = nxt
                continue
        break


def _upgrade_section_titles(resume: Tag) -> None:
    for h2 in resume.find_all("h2"):
        classes = list(h2.get("class") or [])
        if "section-title" not in classes:
            classes.append("section-title")
            h2["class"] = classes


def _structure_experience_item(soup: BeautifulSoup, wrapper: Tag) -> None:
    h3 = wrapper.find("h3")
    if not isinstance(h3, Tag):
        return

    title_text = h3.get_text(" ", strip=True)
    company = ""
    dates = ""

    # Pull company/date from the first short paragraph after the heading.
    meta_p = None
    for child in list(wrapper.children):
        if child is h3:
            continue
        if isinstance(child, NavigableString) and not str(child).strip():
            continue
        if isinstance(child, Tag) and child.name == "p":
            text = child.get_text(" ", strip=True)
            if text and len(text) < 160 and not text.startswith(("•", "-", "*")):
                company, dates = _split_company_and_dates(text)
                meta_p = child
            break
        break

    # Title may itself contain dates: "Role (2020-2022)"
    if not dates:
        title_company, title_dates = _split_company_and_dates(title_text)
        if title_dates:
            title_text = title_company or title_text
            dates = title_dates

    title_html = f'<span class="entry-title">{html.escape(title_text)}</span>'
    title_row = _make_entry_row(soup, title_html, dates)
    h3.replace_with(title_row)

    if meta_p is not None:
        if company:
            sub_html = f'<span class="entry-subtitle">{html.escape(company)}</span>'
            sub_row = _make_entry_row(soup, sub_html, "")
            title_row.insert_after(sub_row)
        meta_p.decompose()


def _wrap_entry_blocks(soup: BeautifulSoup, resume: Tag) -> None:
    """Wrap each h3 block and upgrade to entry-row layout."""
    headings = [
        child
        for child in list(resume.children)
        if isinstance(child, Tag) and child.name == "h3"
    ]
    for h3 in headings:
        # Skip if already inside a structured item
        parent = h3.parent
        if isinstance(parent, Tag) and parent != resume:
            classes = parent.get("class") or []
            if "experience-item" in classes or "project-item" in classes:
                continue

        # Decide class based on nearest preceding h2
        block_class = "experience-item"
        prev = h3.find_previous_sibling("h2")
        if isinstance(prev, Tag):
            heading = prev.get_text(" ", strip=True).lower()
            if any(k in heading for k in ("project", "פרויקט")):
                block_class = "project-item"

        wrapper = soup.new_tag("div", attrs={"class": block_class})
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

        _structure_experience_item(soup, wrapper)


def _looks_like_skill_category_line(text: str) -> bool:
    if ":" not in text:
        return False
    left, right = text.split(":", 1)
    return bool(left.strip()) and bool(right.strip()) and len(left) < 40


def _structure_skills_section(soup: BeautifulSoup, resume: Tag) -> None:
    for h2 in resume.find_all("h2"):
        if not SKILLS_HEADING_RE.search(h2.get_text(" ", strip=True)):
            continue

        nodes: list[Tag] = []
        sibling = h2.next_sibling
        while sibling is not None:
            if isinstance(sibling, Tag) and sibling.name in ("h2", "h3"):
                break
            if isinstance(sibling, Tag):
                nodes.append(sibling)
            sibling = sibling.next_sibling

        category_pairs: list[tuple[str, str]] = []
        consumed: list[Tag] = []

        for node in nodes:
            if node.name == "p":
                # nl2br may collapse Categories into one <p> with <br> separators.
                for br in node.find_all("br"):
                    br.replace_with("\n")
                lines = [ln.strip() for ln in node.get_text("\n").split("\n") if ln.strip()]
                local_pairs: list[tuple[str, str]] = []
                for line in lines:
                    if _looks_like_skill_category_line(line):
                        left, right = line.split(":", 1)
                        local_pairs.append((left.strip().rstrip(":"), right.strip()))
                if local_pairs and len(local_pairs) == len(lines):
                    category_pairs.extend(local_pairs)
                    consumed.append(node)
                    continue
                if local_pairs and len(local_pairs) == 1 and len(lines) == 1:
                    category_pairs.extend(local_pairs)
                    consumed.append(node)
                    continue
            elif node.name in ("ul", "ol"):
                continue

        if category_pairs:
            container = soup.new_tag("div", attrs={"class": "skills-container"})
            h2.insert_after(container)
            for cat_name, values in category_pairs:
                cat = soup.new_tag("div", attrs={"class": "skills-category"})
                cat.string = cat_name
                vals = soup.new_tag("div", attrs={"class": "skills-values"})
                vals.string = values
                container.append(cat)
                container.append(vals)
            for node in consumed:
                node.decompose()
            continue

        # Flat skills paragraph / list → denser single line
        for node in nodes:
            if node.name == "p":
                classes = list(node.get("class") or [])
                classes.append("skills-flat")
                node["class"] = classes
            elif node.name in ("ul", "ol"):
                items = [li.get_text(" ", strip=True) for li in node.find_all("li")]
                if items:
                    flat = soup.new_tag("p", attrs={"class": "skills-flat"})
                    flat.string = " · ".join(items)
                    node.replace_with(flat)


def _postprocess_html(fragment: str) -> str:
    """Add resume semantics: header, entry rows, skills grid, section titles."""
    soup = BeautifulSoup(f'<div class="resume">{fragment}</div>', "html.parser")
    resume = soup.find(class_="resume")
    if not isinstance(resume, Tag):
        return fragment

    _wrap_header(soup, resume)
    _upgrade_section_titles(resume)
    _wrap_entry_blocks(soup, resume)
    _structure_skills_section(soup, resume)

    # Orphan standalone target-role paragraphs outside header
    for p in resume.find_all("p"):
        if _is_target_role_node(p) and "target-role" not in (p.get("class") or []):
            _decorate_target_role(p)

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
