"""ModernTemplateManager — ATS-safe resume themes (CSS presentation only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_THEME = "modern_ats"

THEME_IDS = (
    "modern_ats",
    "professional",
    "executive",
    "minimal",
    "classic",
)


@dataclass(frozen=True)
class ResumeTheme:
    id: str
    label: str
    description: str
    css: str


def _theme(
    theme_id: str,
    *,
    label: str,
    description: str,
    font_stack: str,
    text: str,
    muted: str,
    heading: str,
    accent: str,
    rule: str,
    section_bg: str,
    section_style: str,
    name_size: str = "20pt",
    name_transform: str = "none",
    name_weight: str = "700",
    name_tracking: str = "0.2px",
    section_transform: str = "uppercase",
    section_tracking: str = "0.8px",
    margin: str = "14mm 16mm 14mm 16mm",
    body_size: str = "10pt",
    line_height: str = "1.42",
    header_align: str = "left",
) -> ResumeTheme:
    if section_style == "bar":
        section_css = f"""
h2.section-title {{
    font-size: 10.5pt;
    font-weight: 700;
    color: {heading};
    background-color: {section_bg};
    padding: 1.4mm 2.5mm;
    text-transform: {section_transform};
    margin: 4.5mm 0 2mm 0;
    letter-spacing: {section_tracking};
    border-left: 2.5px solid {accent};
    page-break-after: avoid;
    break-after: avoid;
}}
"""
    elif section_style == "rule":
        section_css = f"""
h2.section-title {{
    font-size: 10.5pt;
    font-weight: 700;
    color: {heading};
    background: transparent;
    padding: 0 0 1.2mm 0;
    text-transform: {section_transform};
    margin: 5mm 0 2.2mm 0;
    letter-spacing: {section_tracking};
    border-bottom: 1px solid {rule};
    border-left: none;
    page-break-after: avoid;
    break-after: avoid;
}}
"""
    else:  # underline accent
        section_css = f"""
h2.section-title {{
    font-size: 10pt;
    font-weight: 700;
    color: {heading};
    background: transparent;
    padding: 0 0 1mm 0;
    text-transform: {section_transform};
    margin: 5mm 0 2mm 0;
    letter-spacing: {section_tracking};
    border-bottom: 2px solid {accent};
    border-left: none;
    page-break-after: avoid;
    break-after: avoid;
}}
"""

    css = f"""
@page {{
    size: A4;
    margin: {margin};
}}
* {{
    box-sizing: border-box;
}}
body {{
    font-family: {font_stack};
    color: {text};
    line-height: {line_height};
    font-size: {body_size};
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}}
.header {{
    text-align: {header_align};
    margin-bottom: 4mm;
    border-bottom: 1px solid {rule};
    padding-bottom: 3mm;
}}
.header h1 {{
    font-size: {name_size};
    font-weight: {name_weight};
    color: {heading};
    margin: 0 0 1.5mm 0;
    text-transform: {name_transform};
    letter-spacing: {name_tracking};
}}
.contact-info {{
    font-size: 9pt;
    color: {muted};
    font-weight: 400;
    margin-bottom: 0;
    letter-spacing: 0.1px;
}}
.contact-info a {{
    color: {muted};
    text-decoration: none;
}}
.target-role {{
    font-size: 10.5pt;
    font-weight: 600;
    color: {accent};
    text-transform: none;
    letter-spacing: 0.2px;
    margin: 1.8mm 0 0 0;
}}
{section_css}
.resume-entry {{
    margin: 0 0 2.8mm 0;
    page-break-inside: avoid;
    break-inside: avoid;
}}
.resume-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 5mm;
    margin-bottom: 0.4mm;
}}
.title-main {{
    font-weight: 700;
    color: {heading};
    font-size: 10.2pt;
}}
.title-sub {{
    font-weight: 500;
    color: {muted};
    font-style: normal;
}}
.meta-right {{
    font-size: 9pt;
    color: {muted};
    font-weight: 400;
    text-align: right;
    white-space: nowrap;
    flex-shrink: 0;
}}
ul {{
    margin: 0.8mm 0 1.5mm 0;
    padding-left: 4.5mm;
}}
li {{
    margin-bottom: 1mm;
    color: {text};
    text-align: left;
}}
li strong {{
    color: {heading};
    font-weight: 700;
}}
li::marker {{
    color: {muted};
}}
.skills-container {{
    margin-top: 1mm;
    line-height: 1.45;
}}
.skills-line {{
    margin-bottom: 1mm;
    font-size: {body_size};
    color: {text};
}}
.skills-category {{
    font-weight: 700;
    color: {heading};
}}
.summary-text {{
    margin: 0 0 1.8mm 0;
    color: {text};
    max-width: 100%;
}}
"""
    return ResumeTheme(
        id=theme_id, label=label, description=description, css=css.strip()
    )


_THEMES: dict[str, ResumeTheme] = {
    "modern_ats": _theme(
        "modern_ats",
        label="Modern ATS",
        description="Clean modern layout optimized for ATS and human scanning.",
        font_stack='"Source Sans 3", "Source Sans Pro", "IBM Plex Sans", "Segoe UI", Helvetica, Arial, sans-serif',
        text="#1f2937",
        muted="#4b5563",
        heading="#111827",
        accent="#0f766e",
        rule="#d1d5db",
        section_bg="#f3f4f6",
        section_style="bar",
        name_size="21pt",
        name_weight="700",
        margin="13mm 15mm 13mm 15mm",
        header_align="left",
    ),
    "professional": _theme(
        "professional",
        label="Professional",
        description="Balanced corporate presentation with clear hierarchy.",
        font_stack='"IBM Plex Sans", "Source Sans 3", "Segoe UI", Helvetica, Arial, sans-serif',
        text="#1e293b",
        muted="#475569",
        heading="#0f172a",
        accent="#1e40af",
        rule="#cbd5e1",
        section_bg="transparent",
        section_style="rule",
        name_size="20pt",
        margin="14mm 16mm 14mm 16mm",
        header_align="left",
    ),
    "executive": _theme(
        "executive",
        label="Executive",
        description="Refined spacing and typography for senior roles.",
        font_stack='"Libre Franklin", "IBM Plex Sans", "Segoe UI", Helvetica, Arial, sans-serif',
        text="#1c1917",
        muted="#57534e",
        heading="#0c0a09",
        accent="#44403c",
        rule="#a8a29e",
        section_bg="transparent",
        section_style="underline",
        name_size="22pt",
        name_tracking="0.6px",
        section_tracking="1.2px",
        margin="16mm 18mm 16mm 18mm",
        body_size="10.2pt",
        line_height="1.48",
        header_align="left",
    ),
    "minimal": _theme(
        "minimal",
        label="Minimal",
        description="Quiet, airy layout with restrained accents.",
        font_stack='"Nunito Sans", "IBM Plex Sans", "Segoe UI", Helvetica, Arial, sans-serif',
        text="#262626",
        muted="#525252",
        heading="#171717",
        accent="#404040",
        rule="#e5e5e5",
        section_bg="transparent",
        section_style="rule",
        name_size="19pt",
        name_weight="600",
        section_transform="none",
        section_tracking="0.3px",
        margin="15mm 17mm 15mm 17mm",
        header_align="left",
    ),
    "classic": ResumeTheme(
        id="classic",
        label="Classic",
        description="Traditional centered header with familiar ATS styling.",
        # Exact legacy stylesheet for backward-compatible exports/tests.
        css="""
@page {
    size: A4;
    margin: 10mm 12mm 10mm 12mm;
}
* {
    box-sizing: border-box;
}
body {
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1e293b;
    line-height: 1.35;
    font-size: 9.5pt;
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}
.header {
    text-align: center;
    margin-bottom: 3mm;
    border-bottom: 2px solid #0f172a;
    padding-bottom: 2mm;
}
.header h1 {
    font-size: 22pt;
    font-weight: 800;
    color: #0f172a;
    margin: 0 0 1mm 0;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.contact-info {
    font-size: 9pt;
    color: #475569;
    font-weight: 500;
    margin-bottom: 0;
}
.contact-info a {
    color: #475569;
    text-decoration: none;
}
.target-role {
    font-size: 11pt;
    font-weight: 700;
    color: #1d4ed8;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin: 1mm 0 0 0;
}
h2.section-title {
    font-size: 11pt;
    font-weight: 700;
    color: #0f172a;
    background-color: #f1f5f9;
    padding: 1mm 2mm;
    text-transform: uppercase;
    margin: 3mm 0 1.5mm 0;
    letter-spacing: 0.5px;
    border-left: 3px solid #1d4ed8;
    page-break-after: avoid;
    break-after: avoid;
}
.resume-entry {
    margin: 0 0 2mm 0;
    page-break-inside: avoid;
    break-inside: avoid;
}
.resume-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 4mm;
    margin-bottom: 0.2mm;
}
.title-main {
    font-weight: 700;
    color: #0f172a;
    font-size: 10pt;
}
.title-sub {
    font-weight: 600;
    color: #475569;
    font-style: italic;
}
.meta-right {
    font-size: 9pt;
    color: #64748b;
    font-weight: 500;
    text-align: right;
    white-space: nowrap;
    flex-shrink: 0;
}
ul {
    margin: 0.5mm 0 2mm 0;
    padding-left: 4mm;
}
li {
    margin-bottom: 0.7mm;
    color: #334155;
    text-align: left;
}
li strong {
    color: #0f172a;
    font-weight: 700;
}
li::marker {
    color: #64748b;
}
.skills-container {
    margin-top: 1mm;
    line-height: 1.4;
}
.skills-line {
    margin-bottom: 0.8mm;
    font-size: 9.5pt;
    color: #334155;
}
.skills-category {
    font-weight: 700;
    color: #0f172a;
}
.summary-text {
    margin: 0 0 1.5mm 0;
    color: #334155;
}
""".strip(),
    ),
}


def resolve_theme(theme: str | None) -> ResumeTheme:
    key = (theme or DEFAULT_THEME).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "modern": "modern_ats",
        "ats": "modern_ats",
        "default": "modern_ats",
        "corp": "professional",
        "corporate": "professional",
        "exec": "executive",
        "simple": "minimal",
        "legacy": "classic",
        "original": "classic",
    }
    key = aliases.get(key, key)
    return _THEMES.get(key) or _THEMES[DEFAULT_THEME]


def list_themes() -> list[dict[str, str]]:
    return [
        {"id": t.id, "label": t.label, "description": t.description}
        for t in (_THEMES[i] for i in THEME_IDS)
    ]


class ModernTemplateManager:
    """Resolve and list ATS-friendly resume themes."""

    default_theme = DEFAULT_THEME

    def get(self, theme: str | None = None) -> ResumeTheme:
        return resolve_theme(theme)

    def css(self, theme: str | None = None) -> str:
        return resolve_theme(theme).css

    def list(self) -> list[dict[str, str]]:
        return list_themes()

    def metadata(self, theme: str | None = None) -> dict[str, Any]:
        t = resolve_theme(theme)
        return {"id": t.id, "label": t.label, "description": t.description}
