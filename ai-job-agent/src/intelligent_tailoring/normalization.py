"""Canonical skill / source-entry normalization for provenance validators.

Deterministic helpers — never call an LLM for these calculations.
"""

from __future__ import annotations

import re
from typing import Any

# Alias → canonical skill id (lowercase). Display labels stay separate.
_SKILL_ALIASES: dict[str, str] = {
    "firebase": "firebase",
    "google firebase": "firebase",
    "firestore": "firebase",
    "node": "nodejs",
    "nodejs": "nodejs",
    "node.js": "nodejs",
    "node js": "nodejs",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "reactjs": "react",
    "react.js": "react",
    "react native": "react_native",
    "reactnative": "react_native",
    "vue.js": "vue",
    "vuejs": "vue",
    "k8s": "kubernetes",
    "ci/cd": "cicd",
    "ci-cd": "cicd",
    "c++": "cpp",
    "c#": "csharp",
    "sqlalchemy": "sqlalchemy",
    "sqlite": "sqlite",
    "fastapi": "fastapi",
}

_DISPLAY_LABELS: dict[str, str] = {
    "firebase": "Firebase",
    "nodejs": "Node.js",
    "postgresql": "PostgreSQL",
    "react": "React",
    "react_native": "React Native",
    "vue": "Vue.js",
    "kubernetes": "Kubernetes",
    "cicd": "CI/CD",
    "cpp": "C++",
    "csharp": "C#",
    "sqlalchemy": "SQLAlchemy",
    "sqlite": "SQLite",
    "fastapi": "FastAPI",
}


def _slug_token(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower())
    return cleaned.strip("_")


def canonical_skill(value: str) -> str:
    """Return a stable lowercase skill id (aliases and capitalization collapse)."""
    raw = (value or "").strip().lower()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return ""
    if raw in _SKILL_ALIASES:
        return _SKILL_ALIASES[raw]
    # Soft match dotted / spaced variants
    compact = raw.replace(".", "").replace(" ", "")
    for alias, canon in _SKILL_ALIASES.items():
        if alias.replace(".", "").replace(" ", "") == compact:
            return canon
    return _slug_token(raw)


def display_skill(value: str) -> str:
    """Preserve a human display label for a skill token."""
    canon = canonical_skill(value)
    if canon in _DISPLAY_LABELS:
        return _DISPLAY_LABELS[canon]
    text = (value or "").strip()
    if not text:
        return ""
    # Title-case unknown tokens lightly without destroying acronyms
    if text.isupper() and len(text) <= 4:
        return text
    return text[0].upper() + text[1:] if len(text) > 1 else text.upper()


def stable_source_entry_id(
    *,
    section: str,
    name: str,
    index: int | None = None,
    existing_id: str | None = None,
) -> str:
    """Stable entry id independent of display-text renames when possible.

    Prefers an existing ``project_N`` / ``role_N`` id for backward compatibility
    with ResumeKnowledgeBase facts. When only a name is available, returns a
    slug such as ``restaurant_menu_ordering_app``.
    """
    existing = (existing_id or "").strip()
    if existing:
        return existing
    slug = _slug_token(name)
    section_key = _slug_token(section) or "entry"
    if slug:
        # Keep common short slugs readable
        if len(slug) >= 4:
            return slug
    if index is not None:
        return f"{section_key}_{index}"
    return section_key or "entry"


def project_name_tokens(name: str) -> set[str]:
    """Significant tokens for soft project-name matching."""
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "for",
        "to",
        "in",
        "on",
        "app",
        "application",
        "project",
        "system",
        "platform",
        "tool",
        "service",
    }
    tokens = {
        t
        for t in re.findall(r"[a-z0-9]+", (name or "").lower())
        if len(t) >= 3 and t not in stop
    }
    # Keep 'app' only when it is the sole token
    if not tokens and "app" in (name or "").lower():
        tokens.add("app")
    return tokens


def project_names_match(a: str, b: str) -> bool:
    """True when project names refer to the same source entry.

    Supports exact match, containment, and significant-token overlap so that
    ``Restaurant App`` matches ``Restaurant Menu Ordering App``.
    """
    a_l = re.sub(r"\s+", " ", (a or "").strip().lower())
    b_l = re.sub(r"\s+", " ", (b or "").strip().lower())
    if not a_l or not b_l:
        return False
    if a_l == b_l:
        return True
    if a_l in b_l or b_l in a_l:
        return True
    ta, tb = project_name_tokens(a_l), project_name_tokens(b_l)
    if not ta or not tb:
        return False
    overlap = ta & tb
    # Require at least one distinctive shared token, or majority overlap
    if not overlap:
        return False
    if len(overlap) >= 1 and (ta <= tb or tb <= ta):
        return True
    return (len(overlap) / min(len(ta), len(tb))) >= 0.5


def resolve_original_project(
    tailored_project: dict[str, Any],
    original_projects: list[dict[str, Any]],
    *,
    index: int | None = None,
) -> tuple[int, dict[str, Any]]:
    """Find the best original project for a tailored project.

    Returns ``(original_index, original_dict)``. Index is ``-1`` when unresolved.
    """
    name = str(tailored_project.get("name") or "")
    preferred = str(tailored_project.get("source_entry_id") or "").strip()

    # Prefer explicit source_entry_id of form project_N
    if preferred.startswith("project_"):
        try:
            p_idx = int(preferred.split("_", 1)[1])
            if 0 <= p_idx < len(original_projects):
                return p_idx, original_projects[p_idx]
        except (TypeError, ValueError):
            pass

    best_idx = -1
    best_score = 0.0
    for o_idx, op in enumerate(original_projects or []):
        op_name = str(op.get("name") or "")
        if project_names_match(name, op_name):
            # Prefer tighter matches
            a_l, b_l = name.lower().strip(), op_name.lower().strip()
            score = 1.0 if a_l == b_l else 0.8
            if a_l in b_l or b_l in a_l:
                score = max(score, 0.9)
            ta, tb = project_name_tokens(a_l), project_name_tokens(b_l)
            if ta and tb:
                score = max(score, len(ta & tb) / max(len(ta | tb), 1))
            if score > best_score:
                best_score = score
                best_idx = o_idx
    if best_idx >= 0:
        return best_idx, original_projects[best_idx]

    if index is not None and 0 <= index < len(original_projects or []):
        return index, original_projects[index]
    return -1, {}
