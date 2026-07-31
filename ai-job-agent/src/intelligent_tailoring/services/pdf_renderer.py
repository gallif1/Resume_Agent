"""PDFRenderer — thin wrapper over ModernPdfRenderer / pdf_generator_service."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.services.modern_pdf_renderer import ModernPdfRenderer
from intelligent_tailoring.themes.modern_template_manager import DEFAULT_THEME


def render_tailored_pdf(
    markdown_or_cv: str | dict[str, Any],
    *,
    contact: dict[str, Any] | None = None,
    theme: str | None = None,
) -> bytes:
    """Generate PDF bytes from tailored CV markdown (preferred) or dict.

    ``contact`` is accepted for backward compatibility but unused when markdown
    is provided — contact details are embedded in the markdown header.
    """
    if isinstance(markdown_or_cv, dict):
        from tailor_cv_service import render_tailored_cv_markdown

        contact = contact or {}
        contact_line = " | ".join(
            str(contact.get(k) or "").strip()
            for k in ("email", "phone", "linkedin", "location")
            if str(contact.get(k) or "").strip()
        )
        markdown = render_tailored_cv_markdown(
            markdown_or_cv,
            name=str(contact.get("name") or ""),
            contact_line=contact_line,
            target_role=str(markdown_or_cv.get("professional_title") or ""),
        )
    else:
        markdown = str(markdown_or_cv or "")
    return ModernPdfRenderer(theme=theme or DEFAULT_THEME).render_pdf(markdown)
