"""
Weave technologies into experience/project bullets when evidence supports it.

Scope-safe: only uses technologies already listed on that entry.
Never invents tools the candidate did not use in that role/project.
"""

from __future__ import annotations

import re
from typing import Any


_VAGUE_BACKEND = re.compile(
    r"\b(backend services?|rest apis?|web services?|api(?:s)?|"
    r"microservices?|data (?:layer|model|pipeline)s?|"
    r"database(?:s)?|schemas?|endpoints?)\b",
    re.I,
)
_VAGUE_FRONTEND = re.compile(
    r"\b(user interfaces?|front[- ]?end|ui components?|web applications?|"
    r"responsive (?:ui|interfaces?))\b",
    re.I,
)
_VAGUE_CLOUD = re.compile(
    r"\b(cloud(?:[- ]native)?|deploy(?:ed|ments?)|infrastructure|"
    r"ci/?cd|containers?|orchestration)\b",
    re.I,
)
_VAGUE_DATA = re.compile(
    r"\b(data (?:models?|validation|integrity|persistence)|"
    r"relational (?:schemas?|databases?)|query(?:ing)?|orm|"
    r"database(?:s)?|schemas?)\b",
    re.I,
)
_VAGUE_TEST = re.compile(
    r"\b(test(?:ing|s| coverage| suites?)|unit tests?|integration tests?|"
    r"qa|quality assurance)\b",
    re.I,
)

# Thin activity stubs → value-oriented wording (facts/tech must already exist).
_STUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^created database schema\.?$", re.I),
        "Designed relational {data} schemas supporting scalable backend services, "
        "data validation, and efficient request tracking",
    ),
    (
        re.compile(r"^implemented (?:request )?validation(?: endpoints?)?\.?$", re.I),
        "Implemented request validation and structured {backend} endpoints "
        "for reliable API handling",
    ),
    (
        re.compile(r"^built (?:the )?api\.?$", re.I),
        "Built REST APIs with {backend} backed by {data}",
    ),
    (
        re.compile(r"^developed frontend\.?$", re.I),
        "Developed responsive user interfaces with {frontend}",
    ),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _already_mentions(bullet: str, tech: str) -> bool:
    return bool(tech) and tech.lower() in (bullet or "").lower()


def _pick_unused(techs: list[str], bullet: str, n: int = 2) -> list[str]:
    out: list[str] = []
    for t in techs:
        if not t or _already_mentions(bullet, t):
            continue
        out.append(t)
        if len(out) >= n:
            break
    return out


def _classify_bucket(techs: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {
        "backend": [],
        "frontend": [],
        "data": [],
        "cloud": [],
        "test": [],
        "other": [],
    }
    for t in techs:
        tl = t.lower()
        if any(
            x in tl
            for x in (
                "fastapi",
                "django",
                "flask",
                "spring",
                "express",
                "nestjs",
                "node",
                ".net",
                "laravel",
                "python",
            )
        ):
            buckets["backend"].append(t)
        elif any(
            x in tl
            for x in (
                "react",
                "vue",
                "angular",
                "next.js",
                "svelte",
                "typescript",
                "javascript",
                "html",
                "css",
                "tailwind",
            )
        ):
            buckets["frontend"].append(t)
        elif any(
            x in tl
            for x in (
                "postgres",
                "mysql",
                "mongo",
                "redis",
                "sql",
                "sqlalchemy",
                "prisma",
                "dynamodb",
                "elasticsearch",
            )
        ):
            buckets["data"].append(t)
        elif any(
            x in tl
            for x in (
                "aws",
                "azure",
                "gcp",
                "docker",
                "kubernetes",
                "k8s",
                "terraform",
                "ci/cd",
                "jenkins",
                "github actions",
            )
        ):
            buckets["cloud"].append(t)
        elif any(
            x in tl
            for x in ("pytest", "jest", "cypress", "selenium", "junit", "mocha", "testing")
        ):
            buckets["test"].append(t)
        else:
            buckets["other"].append(t)
    return buckets


def _phrase_with(techs: list[str]) -> str:
    if not techs:
        return ""
    if len(techs) == 1:
        return f" using {techs[0]}"
    if len(techs) == 2:
        return f" using {techs[0]} and {techs[1]}"
    return f" using {', '.join(techs[:-1])}, and {techs[-1]}"


def _label(techs: list[str], fallback: str) -> str:
    if not techs:
        return fallback
    if len(techs) == 1:
        return techs[0]
    return f"{techs[0]} and {techs[1]}"


def upgrade_stub_bullet(bullet: str, technologies: list[str]) -> str:
    """Turn activity stubs into value statements when entry tech supports it."""
    text = _norm(bullet)
    if not text:
        return text
    buckets = _classify_bucket(technologies)
    for pattern, template in _STUB_PATTERNS:
        if not pattern.match(text):
            continue
        # Only apply when supporting tech exists for placeholders used
        if "{data}" in template and not buckets["data"]:
            continue
        if "{backend}" in template and not (buckets["backend"] or buckets["other"]):
            continue
        if "{frontend}" in template and not (buckets["frontend"] or buckets["other"]):
            continue
        filled = template.format(
            backend=_label(buckets["backend"] or buckets["other"], "backend services"),
            data=_label(buckets["data"], "database"),
            frontend=_label(buckets["frontend"] or buckets["other"], "modern UI tools"),
            cloud=_label(buckets["cloud"], "cloud infrastructure"),
        )
        return filled if filled.endswith(".") else filled + "."
    return text


def weave_technologies_into_bullet(
    bullet: str,
    technologies: list[str],
) -> str:
    """Append a concise 'using X and Y' phrase when the bullet is tech-vague."""
    text = upgrade_stub_bullet(_norm(bullet), technologies)
    if not text or not technologies:
        return text

    buckets = _classify_bucket(technologies)
    to_add: list[str] = []

    if _VAGUE_BACKEND.search(text):
        to_add.extend(_pick_unused(buckets["backend"] or buckets["other"], text, 2))
    if _VAGUE_DATA.search(text) or _VAGUE_BACKEND.search(text):
        for t in _pick_unused(buckets["data"], text, 2):
            if t not in to_add:
                to_add.append(t)
    if _VAGUE_FRONTEND.search(text):
        for t in _pick_unused(buckets["frontend"], text, 2):
            if t not in to_add:
                to_add.append(t)
    if _VAGUE_CLOUD.search(text):
        for t in _pick_unused(buckets["cloud"], text, 2):
            if t not in to_add:
                to_add.append(t)
    if _VAGUE_TEST.search(text):
        for t in _pick_unused(buckets["test"], text, 1):
            if t not in to_add:
                to_add.append(t)

    to_add = to_add[:2]
    if not to_add:
        return text

    # Avoid double "using"
    if re.search(r"\busing\b", text, re.I):
        return text

    phrase = _phrase_with(to_add)
    if text.endswith("."):
        return text[:-1] + phrase + "."
    return text + phrase + "."


def _entry_technologies(entry: dict[str, Any]) -> list[str]:
    """Collect technologies evidenced on this entry only (never cross-entry)."""
    explicit = [str(t).strip() for t in (entry.get("technologies") or []) if str(t).strip()]
    blob = " ".join(
        [str(entry.get("description") or "")]
        + [str(b) for b in (entry.get("bullets") or [])]
        + explicit
    )
    try:
        from intelligent_tailoring.scope_validator import extract_tech_mentions

        mentioned = sorted(extract_tech_mentions(blob))
    except Exception:
        mentioned = []
    # Prefer original casing from explicit list when available
    casing = {t.lower(): t for t in explicit}
    out: list[str] = []
    for t in explicit + mentioned:
        key = t.lower()
        label = casing.get(key, t)
        if label not in out and key not in {x.lower() for x in out}:
            out.append(label)
    return out


def weave_resume_technologies(resume: dict[str, Any]) -> dict[str, Any]:
    """Apply technology weaving across experience and projects."""
    out = dict(resume or {})

    experience: list[dict[str, Any]] = []
    for role in list(out.get("experience") or []):
        if not isinstance(role, dict):
            continue
        r = dict(role)
        techs = _entry_technologies(r)
        bullets = []
        for b in list(r.get("bullets") or []):
            if not isinstance(b, str):
                continue
            bullets.append(weave_technologies_into_bullet(b, techs))
        r["bullets"] = bullets
        experience.append(r)
    out["experience"] = experience

    projects: list[dict[str, Any]] = []
    for proj in list(out.get("projects") or []):
        if not isinstance(proj, dict):
            continue
        p = dict(proj)
        techs = _entry_technologies(p)
        bullets = []
        for b in list(p.get("bullets") or []):
            if not isinstance(b, str):
                continue
            bullets.append(weave_technologies_into_bullet(b, techs))
        p["bullets"] = bullets
        desc = _norm(str(p.get("description") or ""))
        if desc and techs and not any(_already_mentions(desc, t) for t in techs[:2]):
            p["description"] = weave_technologies_into_bullet(desc, techs)
        projects.append(p)
    out["projects"] = projects

    return out
