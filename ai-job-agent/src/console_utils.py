"""Safe console output for pipeline scripts on Windows (Hebrew + Unicode)."""

from __future__ import annotations

import sys


def configure_console() -> None:
    """Prefer UTF-8 so Hebrew job titles and PDF text print without crashing."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def safe_print(*args, **kwargs) -> None:
    """Print without raising UnicodeEncodeError on legacy Windows code pages."""
    stream = kwargs.get("file") or sys.stdout
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        end = kwargs.get("end", "\n")
        text = " ".join(str(arg) for arg in args) + end
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode(encoding, errors="replace"))
        else:
            stream.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
