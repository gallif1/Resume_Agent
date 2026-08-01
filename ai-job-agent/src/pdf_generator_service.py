"""Render tailored CV Markdown to a professional A4 PDF via Playwright."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

from intelligent_tailoring.themes.modern_template_manager import (
    DEFAULT_THEME,
    ModernTemplateManager,
    resolve_theme,
)

# Margins come from @page CSS; Playwright gets zero so CSS owns the page box.
PDF_MARGIN = {"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
DEFAULT_PDF_FILENAME = "Gal_Lifshitz_CV_Tailored.pdf"
DEFAULT_RESUME_THEME = DEFAULT_THEME
_TEMPLATE_MANAGER = ModernTemplateManager()

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
# Plain / bold section titles the LLM sometimes emits without ## markers.
SECTION_TITLE_RE = re.compile(
    r"^\s*(?:\*\*|__)?\s*("
    r"experience|work\s+experience|employment|work\s+history|professional\s+experience|"
    r"projects?|personal\s+projects?|selected\s+projects?|"
    r"skills|technical\s+skills|core\s+skills|technologies|tech\s+stack|"
    r"summary|professional\s+summary|profile|objective|about(?:\s+me)?|"
    r"education|academic\s+background|"
    r"certifications?|certificates?|licenses?|"
    r"languages?|interests?|awards?|"
    r"military(?:\s+service)?|volunteering|volunteer(?:ing)?|other|"
    r"ניסיון(?:\s+תעסוקתי|\s+מקצועי|\s+עבודה)?|פרויקטים|כישורים(?:\s+טכניים)?|"
    r"מיומנויות|תקציר|פרופיל|השכלה|הסמכות|תעודות|שפות|"
    r"שירות\s+צבאי|התנדבות|פרסים|אחר"
    r")\s*(?:\*\*|__)?\s*:?\s*$",
    re.IGNORECASE,
)
MAX_BULLETS_PER_ENTRY = 3
MAX_SUMMARY_SENTENCES = 3

# Known tool → preferred skill category (for taxonomy cleanup).
SKILL_CATEGORY_HINTS: dict[str, str] = {
    "sqlalchemy": "Backend & Frameworks",
    "fastapi": "Backend & Frameworks",
    "django": "Backend & Frameworks",
    "flask": "Backend & Frameworks",
    "node.js": "Backend & Frameworks",
    "nodejs": "Backend & Frameworks",
    "express": "Backend & Frameworks",
    "expo": "Frontend",
    "react native": "Frontend",
    "react": "Frontend",
    "angular": "Frontend",
    "vue": "Frontend",
    "vue.js": "Frontend",
    "html": "Frontend",
    "css": "Frontend",
    "next.js": "Frontend",
    "nextjs": "Frontend",
    "postgresql": "Databases & Caching",
    "postgres": "Databases & Caching",
    "mysql": "Databases & Caching",
    "sqlite": "Databases & Caching",
    "mongodb": "Databases & Caching",
    "redis": "Databases & Caching",
    "docker": "Cloud & DevOps",
    "kubernetes": "Cloud & DevOps",
    "aws": "Cloud & DevOps",
    "gcp": "Cloud & DevOps",
    "azure": "Cloud & DevOps",
    "ci/cd": "Cloud & DevOps",
    "github actions": "Cloud & DevOps",
    "python": "Languages",
    "javascript": "Languages",
    "typescript": "Languages",
    "sql": "Languages",
    "c++": "Languages",
}
CLOUD_CATEGORY_RE = re.compile(
    r"cloud|devops|infrastructure|ops\b",
    re.IGNORECASE,
)
SKILL_SPLIT_RE = re.compile(r"\s*[,•]\s*|\s+/\s+|\s*\|\s*")
BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+)$")
MD_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")

def resume_css_for_theme(theme: str | None = None) -> str:
    """Return ATS-safe print CSS for the selected theme."""
    return resolve_theme(theme).css


# Backward-compatible alias — classic theme matches the historical stylesheet.
RESUME_CSS = resume_css_for_theme("classic")


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


def _looks_like_section_title(text: str) -> str | None:
    """Return a cleaned section title if ``text`` is a known section heading."""
    raw = _strip_md_inline(text)
    if not raw or len(raw) > 60:
        return None
    match = SECTION_TITLE_RE.match(raw)
    if not match:
        return None
    return match.group(1).strip()


def _looks_like_skill_category_line(text: str) -> bool:
    if ":" not in text:
        return False
    left, right = text.split(":", 1)
    return bool(left.strip()) and bool(right.strip()) and len(left.strip()) < 40


def _add_skill_content(
    section: ResumeSection, text: str, *, category: str = ""
) -> None:
    """Record one line of a Skills section, whatever shape it arrived in.

    Skills reach us as "Category: a, b" rows, as bare comma lists, or as Markdown
    bullets under an optional ``### Category`` heading. All three end up in
    ``skill_lines`` / ``flat_skills``, which are the only fields the Skills
    renderer reads — anything else would be dropped and leave an empty section.
    """
    cleaned = _strip_md_inline(text)
    if not cleaned:
        return

    if _looks_like_skill_category_line(cleaned):
        left, right = cleaned.split(":", 1)
        section.skill_lines.append((left.strip().rstrip(":"), right.strip()))
        return

    if category:
        for index, (existing, values) in enumerate(section.skill_lines):
            if existing.lower() == category.lower():
                section.skill_lines[index] = (existing, f"{values}, {cleaned}")
                return
        section.skill_lines.append((category, cleaned))
        return

    section.flat_skills = (
        f"{section.flat_skills} · {cleaned}" if section.flat_skills else cleaned
    )


def parse_resume_markdown(markdown: str) -> ParsedResume:
    """Parse tailored CV markdown into a structured resume model."""
    resume = ParsedResume()
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    current_section: ResumeSection | None = None
    current_entry: ResumeEntry | None = None
    skills_category = ""
    header_done = False
    sections_by_kind: dict[str, ResumeSection] = {}

    def ensure_section(title: str) -> ResumeSection:
        """Create or reuse a section so headings like SUMMARY are never duplicated."""
        nonlocal current_section, current_entry, header_done, skills_category
        header_done = True
        current_entry = None
        skills_category = ""
        kind = _section_kind(title)
        existing = sections_by_kind.get(kind)
        if existing is not None and kind != "other":
            current_section = existing
            return existing
        current_section = ResumeSection(title=title, kind=kind)
        resume.sections.append(current_section)
        sections_by_kind[kind] = current_section
        return current_section

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
            # H1 after the name (or any H2) is a section title — LLMs often use # Skills.
            if level <= 2:
                section_title = _looks_like_section_title(text) or text
                ensure_section(section_title)
                continue
            if level == 3:
                if current_section is None:
                    ensure_section("Experience")
                assert current_section is not None
                if current_section.kind == "skills":
                    # "### Databases" is a skills category, not a resume entry.
                    skills_category = text
                    continue
                title, embedded_dates = _split_company_and_dates(text)
                current_entry = ResumeEntry(
                    title=title or text,
                    dates=embedded_dates,
                )
                current_section.entries.append(current_entry)
                continue

        plain_for_title = _strip_md_inline(line)
        plain_section = _looks_like_section_title(plain_for_title)
        if plain_section:
            ensure_section(plain_section)
            continue

        # Header contact / target role before first section.
        if not header_done and current_section is None:
            target = TARGET_ROLE_RE.match(plain_for_title)
            if target:
                resume.target_role = target.group(1).strip()
                continue
            if _looks_like_contact_line(line) and not resume.contact:
                resume.contact = _strip_md_inline(line)
                continue
            # First non-meta line becomes the name when H1 was missing.
            if not resume.name:
                resume.name = plain_for_title
                continue
            # Name already set — remaining body starts a summary section rather
            # than being silently dropped (this caused blank PDFs).
            ensure_section("Summary")
            # Fall through to process this line as summary content.

        if current_section is None:
            ensure_section("Summary")

        assert current_section is not None

        bullet = BULLET_RE.match(raw_line)

        # Skills content is collected before the generic bullet handling: a
        # bulleted skills list would otherwise become entry bullets, which the
        # Skills renderer ignores — the section rendered as a bare header.
        if current_section.kind == "skills":
            _add_skill_content(
                current_section,
                (bullet.group(1) if bullet else plain_for_title) or "",
                category=skills_category,
            )
            continue

        if bullet:
            # Preserve **bold** markers so PDF/HTML can render <strong>.
            text = (bullet.group(1) or "").strip()
            if current_entry is None:
                current_entry = ResumeEntry(title="")
                current_section.entries.append(current_entry)
            current_entry.bullets.append(text)
            continue

        plain = plain_for_title
        target = TARGET_ROLE_RE.match(plain)
        if target and not resume.target_role:
            resume.target_role = target.group(1).strip()
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

        # Keep inline Markdown (bold) for summary/body rendering.
        rich_line = line
        if current_section.kind in {"summary", "other"} and current_entry is None:
            current_section.paragraphs.append(rich_line)
            continue

        # Free text under an entry → treat as a soft bullet.
        if current_entry is not None:
            current_entry.bullets.append(rich_line)
        else:
            current_section.paragraphs.append(rich_line)

    return _normalize_parsed_resume(resume)


def _entry_identity(entry: ResumeEntry) -> str:
    return re.sub(
        r"\s+",
        " ",
        f"{entry.title} {entry.subtitle}".strip().lower(),
    )


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = re.sub(r"\s+", " ", item.strip().lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def _rebalance_skill_lines(
    skill_lines: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Move mis-categorized tools (e.g. SQLAlchemy under Cloud) to better buckets."""
    buckets: dict[str, list[str]] = {}
    order: list[str] = []

    def add(category: str, skill: str) -> None:
        cat = category.strip() or "Tools"
        skill = skill.strip()
        if not skill:
            return
        if cat not in buckets:
            buckets[cat] = []
            order.append(cat)
        if skill not in buckets[cat]:
            buckets[cat].append(skill)

    for category, values in skill_lines:
        # Bare "/" is part of skill names ("CI/CD", "TCP/IP"), so only a spaced
        # slash counts as a separator.
        for raw in SKILL_SPLIT_RE.split(values):
            skill = raw.strip()
            if not skill:
                continue
            hint = SKILL_CATEGORY_HINTS.get(skill.lower())
            if hint:
                add(hint, skill)
            elif CLOUD_CATEGORY_RE.search(category) and skill.lower() in {
                "sqlalchemy",
                "fastapi",
                "django",
                "flask",
            }:
                add(
                    SKILL_CATEGORY_HINTS.get(skill.lower(), "Backend & Frameworks"),
                    skill,
                )
            else:
                add(category, skill)

    return [(cat, ", ".join(buckets[cat])) for cat in order if buckets[cat]]


def _normalize_parsed_resume(resume: ParsedResume) -> ParsedResume:
    """Deduplicate sections/entries, cap density, drop ghost sections."""
    # Cap summary length.
    for section in resume.sections:
        if section.kind == "summary" and section.paragraphs:
            text = " ".join(section.paragraphs)
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            sentences = [s for s in sentences if s]
            section.paragraphs = [
                " ".join(sentences[:MAX_SUMMARY_SENTENCES]).strip()
            ] if sentences else []

        if section.kind == "skills" and section.skill_lines:
            section.skill_lines = _rebalance_skill_lines(section.skill_lines)

        for entry in section.entries:
            entry.bullets = _dedupe_strings(entry.bullets)[:MAX_BULLETS_PER_ENTRY]

        section.paragraphs = _dedupe_strings(section.paragraphs)

    # If the same titled entry appears in both Experience and Projects, keep it
    # in Experience (real employment) and remove the Projects duplicate.
    experience = next((s for s in resume.sections if s.kind == "experience"), None)
    projects = next((s for s in resume.sections if s.kind == "projects"), None)
    if experience and projects:
        exp_ids = {_entry_identity(e) for e in experience.entries if _entry_identity(e)}
        projects.entries = [
            e for e in projects.entries if _entry_identity(e) not in exp_ids
        ]

    # Drop empty / ghost sections (Military, Awards, empty Other, etc.).
    resume.sections = [s for s in resume.sections if _section_has_content(s)]
    return resume


def _section_has_content(section: ResumeSection) -> bool:
    if section.paragraphs or section.skill_lines or section.flat_skills:
        return True
    return any(
        entry.title or entry.subtitle or entry.bullets for entry in section.entries
    )


def _resume_has_body(resume: ParsedResume) -> bool:
    return any(_section_has_content(section) for section in resume.sections)


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
    bullets = entry.bullets[:MAX_BULLETS_PER_ENTRY]
    if bullets:
        parts.append("<ul>")
        for bullet in bullets:
            parts.append(f"<li>{_inline_html(bullet)}</li>")
        parts.append("</ul>")
    parts.append("</div>")
    return "\n".join(parts)


def _render_section(section: ResumeSection) -> str:
    if not _section_has_content(section):
        return ""

    chunks = [f'<h2 class="section-title">{html.escape(section.title)}</h2>']

    if section.kind == "skills":
        chunks.append('<div class="skills-container">')
        for category, values in section.skill_lines:
            chunks.append(
                '<div class="skills-line">'
                f'<span class="skills-category">{html.escape(category)}:</span> '
                f"{html.escape(values)}"
                "</div>"
            )
        # Ungrouped skills render alongside grouped rows — a resume that mixes
        # both shapes must not lose half of its skills.
        if section.flat_skills:
            chunks.append(
                f'<div class="skills-line">{html.escape(section.flat_skills)}</div>'
            )
        chunks.append("</div>")
        return "\n".join(chunks)

    for paragraph in section.paragraphs:
        chunks.append(f'<p class="summary-text">{_inline_html(paragraph)}</p>')

    for entry in section.entries:
        # Skip empty placeholder entries.
        if not (entry.title or entry.subtitle or entry.bullets):
            continue
        chunks.append(_render_entry(entry))

    return "\n".join(chunks)


def parsed_resume_to_html(
    resume: ParsedResume,
    *,
    theme: str | None = None,
) -> str:
    """Render a ParsedResume into the Playwright print HTML document."""
    css = resume_css_for_theme(theme)
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
        role_display = role
        header_parts.append(
            f'<p class="target-role">{html.escape(role_display)}</p>'
        )
    header_parts.append("</div>")
    body_parts.append("\n".join(header_parts))

    for section in resume.sections:
        rendered = _render_section(section)
        if rendered:
            body_parts.append(rendered)

    body_parts.append("</div>")
    body = "\n".join(body_parts)
    title = resume.name or "CV"
    theme_id = resolve_theme(theme).id

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8"/>\n'
        f'<meta name="resume-theme" content="{html.escape(theme_id)}"/>\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{css}</style>\n"
        "</head>\n"
        f"<body>\n{body}\n</body>\n"
        "</html>\n"
    )


def _generic_markdown_html(markdown: str, *, theme: str | None = None) -> str:
    """Fallback HTML when structured parsing cannot find resume sections."""
    import markdown as md_lib

    css = resume_css_for_theme(theme)
    body = md_lib.markdown(
        markdown,
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8"/>\n'
        "<title>CV</title>\n"
        f"<style>{css}"
        "h1{font-size:22pt;font-weight:700;margin:0 0 1mm 0;letter-spacing:0.2px;}"
        "h2{font-size:11pt;font-weight:700;margin:3mm 0 1.5mm 0;"
        "letter-spacing:0.5px;text-transform:uppercase;}"
        "h3{font-size:10pt;font-weight:700;margin:2mm 0 0.5mm 0;}"
        "p{margin:0 0 1.5mm 0;}"
        "ul{margin:0.5mm 0 2mm 0;padding-left:4mm;}"
        "li{margin-bottom:0.5mm;}"
        "</style>\n"
        "</head>\n"
        f'<body>\n<div class="resume">\n{body}\n</div>\n</body>\n'
        "</html>\n"
    )


def markdown_to_resume_html(markdown: str, *, theme: str | None = None) -> str:
    """Convert CV Markdown into a structured HTML document with print CSS."""
    raw = (markdown or "").strip()
    if not raw:
        raise PdfGeneratorError("קורות החיים ריקים — אין מה להמיר ל-PDF", status_code=400)

    parsed = parse_resume_markdown(raw)
    if not parsed.name and not parsed.sections:
        raise PdfGeneratorError("קורות החיים ריקים — אין מה להמיר ל-PDF", status_code=400)

    # Structured parse sometimes keeps only the header when the LLM omitted ##
    # headings — fall back to generic Markdown rendering so the PDF is not blank.
    if parsed.name and not _resume_has_body(parsed):
        return _generic_markdown_html(raw, theme=theme)

    return parsed_resume_to_html(parsed, theme=theme)


def render_markdown_to_pdf(markdown: str, *, theme: str | None = None) -> bytes:
    """Render Markdown to an A4 PDF buffer using headless Chromium."""
    document = markdown_to_resume_html(markdown, theme=theme)
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


def generate_tailored_cv_pdf(
    markdown: str,
    *,
    theme: str | None = None,
) -> tuple[bytes, str]:
    """Return (pdf_bytes, download_filename) for a tailored CV body."""
    pdf_bytes = render_markdown_to_pdf(markdown, theme=theme)
    return pdf_bytes, pdf_filename_for_markdown(markdown)


class ModernPdfRenderer:
    """Modern multi-theme PDF renderer (ATS-safe, single-column)."""

    def __init__(self, theme: str | None = None):
        self.theme = theme or DEFAULT_RESUME_THEME
        self.templates = _TEMPLATE_MANAGER

    def render_html(self, markdown: str, *, theme: str | None = None) -> str:
        return markdown_to_resume_html(markdown, theme=theme or self.theme)

    def render_pdf(self, markdown: str, *, theme: str | None = None) -> bytes:
        return render_markdown_to_pdf(markdown, theme=theme or self.theme)

    def render(self, markdown: str, *, theme: str | None = None) -> tuple[bytes, str]:
        return generate_tailored_cv_pdf(markdown, theme=theme or self.theme)

    def list_themes(self) -> list[dict[str, str]]:
        return self.templates.list()
