"""What backend code is actually running.

The EC2 deploy copies source files into a long-lived container instead of
rebuilding the image, so "the workflow succeeded" is not by itself evidence that
the new code is live. The deploy writes ``BUILD_REVISION`` next to this module
and then asserts that ``/api/health`` reports the same revision it just shipped,
which turns a partial or skipped backend deploy into a failed deploy.

The file is read per call (not cached at import time) so a fresh injection is
visible without recreating the container.
"""

from __future__ import annotations

import os
from pathlib import Path

from match_tailor_prompt import MATCH_TAILOR_PROMPT_VERSION

REVISION_FILE = Path(__file__).resolve().parent / "BUILD_REVISION"
UNKNOWN_REVISION = "unknown"


def backend_revision() -> str:
    """The git revision of the running backend source, or ``"unknown"``.

    ``BACKEND_REVISION`` in the environment wins so container-based deploys that
    do rebuild the image can stamp the revision without writing a file.
    """
    env_revision = (os.environ.get("BACKEND_REVISION") or "").strip()
    if env_revision:
        return env_revision
    try:
        return REVISION_FILE.read_text(encoding="utf-8").strip() or UNKNOWN_REVISION
    except OSError:
        return UNKNOWN_REVISION


def build_info() -> dict[str, str]:
    """Version facts for ``/api/health``."""
    return {
        "backend_revision": backend_revision(),
        "tailor_prompt_version": MATCH_TAILOR_PROMPT_VERSION,
    }
