"""ModernPdfRenderer — theme-aware ATS PDF generation."""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.themes.modern_template_manager import (
    DEFAULT_THEME,
    ModernTemplateManager,
)
from pdf_generator_service import ModernPdfRenderer as _Renderer


class ModernPdfRenderer:
    """Facade used by the writing/export pipeline."""

    def __init__(self, theme: str | None = None):
        self.theme = theme or DEFAULT_THEME
        self._renderer = _Renderer(theme=self.theme)
        self.templates = ModernTemplateManager()

    def render_html(self, markdown: str, *, theme: str | None = None) -> str:
        return self._renderer.render_html(markdown, theme=theme or self.theme)

    def render_pdf(self, markdown: str, *, theme: str | None = None) -> bytes:
        return self._renderer.render_pdf(markdown, theme=theme or self.theme)

    def render(self, markdown: str, *, theme: str | None = None) -> tuple[bytes, str]:
        return self._renderer.render(markdown, theme=theme or self.theme)

    def list_themes(self) -> list[dict[str, str]]:
        return self.templates.list()


def render_tailored_pdf_bytes(
    markdown: str,
    *,
    theme: str | None = None,
) -> bytes:
    return ModernPdfRenderer(theme=theme).render_pdf(markdown, theme=theme)
