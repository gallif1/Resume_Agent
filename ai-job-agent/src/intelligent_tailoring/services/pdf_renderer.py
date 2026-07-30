"""PDFRenderer — thin wrapper over existing pdf_generator_service."""

from __future__ import annotations

from typing import Any


def render_tailored_pdf(
    tailored_cv: dict[str, Any],
    *,
    contact: dict[str, Any] | None = None,
) -> bytes:
    """Generate PDF bytes from a tailored CV dict using the existing renderer."""
    from pdf_generator_service import generate_tailored_cv_pdf

    return generate_tailored_cv_pdf(tailored_cv, contact=contact or {})
