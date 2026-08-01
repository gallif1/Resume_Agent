"""Deterministic duplicate detection / removal before rendering.

Ensures the final resume never contains:
- The same sentence as both a description and a bullet
- Near-duplicate bullets within an entry
- Repeated sentences in the summary
- Cross-entry copy-paste of identical bullets
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s+#./-]", re.UNICODE)


def _norm(text: str) -> str:
    text = _WS_RE.sub(" ", (text or "").strip().lower())
    text = _PUNCT_RE.sub("", text)
    return text.strip()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#.]{3,}", _norm(text)) if t}


def _similar(a: str, b: str, *, threshold: float = 0.82) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        # Containment only when the shorter is substantial
        shorter = min(len(na), len(nb))
        longer = max(len(na), len(nb))
        if shorter >= 24 and shorter / longer >= 0.72:
            return True
    # Shared long prefix (same sentence with a short trailing tweak)
    prefix_len = 0
    for ca, cb in zip(na, nb):
        if ca != cb:
            break
        prefix_len += 1
    if prefix_len >= 36 and prefix_len / max(len(na), len(nb), 1) >= 0.78:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta | tb), 1)
    if overlap >= threshold:
        return True
    # High recall for near-paraphrase bullets that share almost all content words
    if len(ta & tb) >= 5 and len(ta & tb) / max(min(len(ta), len(tb)), 1) >= 0.85:
        return True
    return False


def _dedupe_list(items: list[str], *, threshold: float = 0.82) -> list[str]:
    kept: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        if any(_similar(text, prev, threshold=threshold) for prev in kept):
            continue
        kept.append(text)
    return kept


def _dedupe_summary(summary: str) -> str:
    text = _WS_RE.sub(" ", (summary or "").strip())
    if not text:
        return ""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    if len(parts) < 2:
        return text
    kept = _dedupe_list(parts, threshold=0.78)
    # Also collapse immediate repeated title/tech phrases inside a sentence
    out = " ".join(kept)
    out = re.sub(
        r"\b([A-Z][A-Za-z0-9+.#/-]*(?:\s+[A-Z][A-Za-z0-9+.#/-]*){0,3})\s+\1\b",
        r"\1",
        out,
    )
    return out.strip()


def dedupe_resume_content(resume: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the resume with duplicated prose removed."""
    out = deepcopy(resume) if isinstance(resume, dict) else {}
    summary = str(
        out.get("professional_summary") or out.get("summary") or ""
    ).strip()
    summary = _dedupe_summary(summary)
    out["professional_summary"] = summary
    out["summary"] = summary

    global_seen: list[str] = [summary] if summary else []

    experience: list[dict[str, Any]] = []
    for entry in out.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        bullets = _dedupe_list(
            [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        )
        # Drop bullets already used elsewhere
        filtered = []
        for b in bullets:
            if any(_similar(b, prev, threshold=0.9) for prev in global_seen):
                continue
            filtered.append(b)
            global_seen.append(b)
        if not filtered:
            continue
        experience.append({**entry, "bullets": filtered})
    out["experience"] = experience

    projects: list[dict[str, Any]] = []
    for entry in out.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "").strip()
        bullets = _dedupe_list(
            [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()],
            threshold=0.72,
        )
        # Description duplicated as a bullet → keep the stronger bullet, drop description
        if description and any(
            _similar(b, description, threshold=0.72) for b in bullets
        ):
            description = ""
        elif description:
            if any(_similar(description, prev, threshold=0.9) for prev in global_seen):
                description = ""
            else:
                global_seen.append(description)
        filtered = []
        for b in bullets:
            if any(_similar(b, prev, threshold=0.9) for prev in global_seen):
                continue
            filtered.append(b)
            global_seen.append(b)
        if not description and not filtered:
            continue
        projects.append({**entry, "description": description, "bullets": filtered})
    out["projects"] = projects

    # Skills: drop duplicate atoms across category lines (normalize_skill_lines
    # also handles this; keep a light pass for raw lines)
    skill_lines = []
    seen_atoms: set[str] = set()
    for line in out.get("skills") or []:
        text = str(line).strip()
        if not text:
            continue
        if ":" in text:
            cat, rest = text.split(":", 1)
            atoms = []
            for atom in re.split(r"\s*,\s*", rest.strip()):
                key = _norm(atom)
                if not key or key in seen_atoms:
                    continue
                seen_atoms.add(key)
                atoms.append(atom.strip())
            if atoms:
                skill_lines.append(f"{cat.strip()}: {', '.join(atoms)}")
        else:
            key = _norm(text)
            if key and key not in seen_atoms:
                seen_atoms.add(key)
                skill_lines.append(text)
    out["skills"] = skill_lines
    return out


def resume_has_duplicate_content(resume: dict[str, Any]) -> list[str]:
    """Return human-readable duplicate findings (empty if clean)."""
    findings: list[str] = []
    summary = str(resume.get("professional_summary") or resume.get("summary") or "")
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", summary) if p.strip()]
    norms = [_norm(p) for p in parts]
    if len(norms) != len(set(norms)):
        findings.append("duplicate_sentence_in_summary")

    for idx, entry in enumerate(resume.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        desc = str(entry.get("description") or "").strip()
        for b in entry.get("bullets") or []:
            if desc and _similar(desc, str(b)):
                findings.append(f"project_{idx}_description_bullet_dup")
                break

    all_bullets: list[str] = []
    for section in ("experience", "projects"):
        for entry in resume.get(section) or []:
            if not isinstance(entry, dict):
                continue
            for b in entry.get("bullets") or []:
                text = str(b).strip()
                if any(_similar(text, prev, threshold=0.9) for prev in all_bullets):
                    findings.append(f"cross_entry_duplicate_bullet:{text[:40]}")
                else:
                    all_bullets.append(text)
    return findings
