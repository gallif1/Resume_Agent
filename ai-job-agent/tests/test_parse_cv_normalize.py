"""Tests for resume text normalization."""

from __future__ import annotations

from parse_cv import normalize_text


def test_normalize_text_expands_pdf_ligatures():
    # U+FB00 is the Latin "ff" ligature often found in PDF email addresses.
    text = "contact@\ufb00example.com"
    normalized = normalize_text(text)
    assert "\ufb00" not in normalized
    assert "ff" in normalized
