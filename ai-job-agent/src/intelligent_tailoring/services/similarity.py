"""Text similarity utilities for tailoring validation."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)


def normalize_text(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(normalize_text(text)))


def jaccard_similarity(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def sequence_similarity(a: str, b: str) -> float:
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm and not b_norm:
        return 1.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def blended_similarity(a: str, b: str) -> float:
    """Blend token and sequence similarity for robust section comparison."""
    return 0.55 * jaccard_similarity(a, b) + 0.45 * sequence_similarity(a, b)


def resume_section_text(resume: dict[str, Any], section: str) -> str:
    if section == "summary":
        return str(
            resume.get("professional_summary") or resume.get("summary") or ""
        )
    if section == "skills":
        skills = resume.get("skills") or []
        return " ".join(str(s) for s in skills)
    if section == "experience":
        parts: list[str] = []
        for entry in resume.get("experience") or []:
            if not isinstance(entry, dict):
                continue
            parts.append(str(entry.get("title") or ""))
            parts.append(str(entry.get("company") or ""))
            parts.extend(str(b) for b in (entry.get("bullets") or []))
        return " ".join(parts)
    if section == "projects":
        parts: list[str] = []
        for proj in resume.get("projects") or []:
            if not isinstance(proj, dict):
                continue
            parts.append(str(proj.get("name") or ""))
            parts.append(str(proj.get("description") or ""))
            parts.extend(str(b) for b in (proj.get("bullets") or []))
        return " ".join(parts)
    return ""


def compare_resumes(
    tailored: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, float]:
    """Compare tailored resume sections against a baseline (usually original facts)."""
    sections = ("summary", "skills", "experience", "projects")
    metrics: dict[str, float] = {}
    for sec in sections:
        metrics[f"{sec}_similarity"] = round(
            blended_similarity(
                resume_section_text(tailored, sec),
                resume_section_text(baseline, sec),
            ),
            4,
        )
    weights = {"summary": 0.25, "skills": 0.2, "experience": 0.35, "projects": 0.2}
    overall = sum(metrics[f"{s}_similarity"] * weights[s] for s in sections)
    metrics["overall_similarity"] = round(overall, 4)
    return metrics


def compare_resume_pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, float]:
    """Cross-job similarity between two tailored resumes."""
    return compare_resumes(a, b)
