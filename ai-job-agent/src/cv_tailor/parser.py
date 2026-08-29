"""CV upload validation and text extraction for the CV Tailor MVP."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from cv_reader import extract_text_from_resume

logger = logging.getLogger("cv_tailor.parser")

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MIN_EXTRACTED_CHARS = 40


class CvParseError(ValueError):
    """Raised when an uploaded CV cannot be parsed."""


def sanitize_filename(filename: str) -> str:
    """Return a safe basename for temporary storage."""
    name = Path(filename or "cv").name
    name = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE).strip()
    if not name or name in {".", ".."}:
        name = "cv"
    return name[:180]


def validate_extension(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise CvParseError(f"Unsupported file type '{ext or 'unknown'}'. Allowed: {allowed}")
    return ext


def parse_cv_bytes(file_bytes: bytes, filename: str) -> tuple[str, str]:
    """Validate upload, extract text, and return (text, source_label)."""
    if not file_bytes:
        raise CvParseError("Uploaded file is empty")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise CvParseError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB")

    ext = validate_extension(filename)
    safe_name = sanitize_filename(filename)
    if not safe_name.lower().endswith(ext):
        safe_name = f"{Path(safe_name).stem}{ext}"

    with tempfile.TemporaryDirectory(prefix="cv_tailor_parse_") as tmp_dir:
        temp_path = Path(tmp_dir) / safe_name
        temp_path.write_bytes(file_bytes)
        try:
            text, source = extract_text_from_resume(temp_path)
        except ValueError as exc:
            logger.warning("CV parse failed for %s: %s", safe_name, exc)
            raise CvParseError(str(exc)) from exc
        except Exception as exc:
            logger.exception("Unexpected CV parse failure for %s", safe_name)
            raise CvParseError("Could not read the uploaded CV file") from exc

    cleaned = (text or "").strip()
    if len(cleaned) < MIN_EXTRACTED_CHARS:
        logger.warning(
            "CV parse produced insufficient text (%d chars) from %s via %s",
            len(cleaned),
            safe_name,
            source,
        )
        raise CvParseError(
            "Could not extract enough text from the CV. "
            "Try a text-based PDF or DOCX export."
        )

    logger.info(
        "CV parsed successfully (%d chars, source=%s, filename=%s)",
        len(cleaned),
        source,
        safe_name,
    )
    return cleaned, source
