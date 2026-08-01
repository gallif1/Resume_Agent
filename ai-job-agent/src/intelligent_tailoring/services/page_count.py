"""
PDF page-count measurement for one-page enforcement.

Uses pypdf when available; falls back to content-pressure estimate when PDF
bytes are unavailable.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.services.one_page_compressor import estimate_page_pressure


def count_pdf_pages(pdf_bytes: bytes | None) -> int | None:
    """Return page count for a PDF, or None if unreadable/unavailable."""
    if not pdf_bytes:
        return None
    try:
        from io import BytesIO

        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(BytesIO(pdf_bytes))
        n = len(reader.pages)
        return int(n) if n > 0 else None
    except Exception:
        try:
            import fitz  # type: ignore  # pymupdf

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            n = doc.page_count
            doc.close()
            return int(n) if n > 0 else None
        except Exception:
            return None


def estimate_pages_from_resume(resume: dict[str, Any] | None) -> float:
    """
    Heuristic page estimate used when PDF rendering is not available in-process.

    Pressure ≤ 8 maps to one page; each additional ~16 pressure points ≈ +1 page.
    """
    info = estimate_page_pressure(resume or {})
    pressure = float(info.get("pressure") or 0)
    if pressure <= 8:
        return 1.0
    return round(1.0 + (pressure - 8) / 16.0, 2)


def assert_one_page(
    *,
    pdf_bytes: bytes | None = None,
    resume: dict[str, Any] | None = None,
    allow_multi_page: bool = False,
) -> tuple[bool, str]:
    """
    Validate one-page constraint.

    Returns (ok, reason). When allow_multi_page is True, always ok.
    """
    if allow_multi_page:
        return True, "multi_page_allowed"

    pages = count_pdf_pages(pdf_bytes)
    if pages is not None:
        if pages <= 1:
            return True, f"pdf_pages={pages}"
        return False, f"page_count:{pages}"

    info = estimate_page_pressure(resume or {})
    if info.get("likely_fits_one_page"):
        return True, f"estimated_pressure={info.get('pressure')}"
    est = estimate_pages_from_resume(resume)
    return False, f"page_count:estimated={est}"


def allow_multi_page_requested(*sources: dict[str, Any] | None) -> bool:
    """True when the user explicitly requested a multi-page resume."""
    for src in sources:
        if not isinstance(src, dict):
            continue
        if src.get("allow_multi_page") is True:
            return True
        max_pages = src.get("max_pages")
        if max_pages is not None:
            try:
                if int(max_pages) > 1:
                    return True
            except (TypeError, ValueError):
                pass
        pref = str(
            src.get("resume_length") or src.get("page_preference") or ""
        ).strip().lower()
        if pref in {"multi", "multi_page", "two_page", "2", "long", "2-page"}:
            return True
    return False
