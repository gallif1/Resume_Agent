"""Deterministic skill categorization — profession-agnostic taxonomy.

The LLM must not invent category membership. Unknown items go to
"Other Relevant Skills" rather than an incorrect bucket.
"""

from __future__ import annotations

import re
from typing import Any

# Canonical tech/software categories (also usable as a domain pack).
SOFTWARE_TAXONOMY: dict[str, tuple[str, ...]] = {
    "Languages": (
        "python", "javascript", "typescript", "java", "c++", "c#", "csharp",
        "kotlin", "swift", "go", "golang", "rust", "ruby", "php", "scala",
        "r", "matlab",
    ),
    "Frontend": (
        "react", "react native", "angular", "vue", "vue.js", "html", "css",
        "expo", "next.js", "nextjs", "svelte", "tailwind",
    ),
    "Backend": (
        "fastapi", "django", "flask", "node.js", "nodejs", "nestjs", "express",
        "laravel", "spring", "rails", "rest api", "rest apis", "rest",
        "websockets", "websocket", "graphql", "sqlalchemy", "prisma",
    ),
    "Databases": (
        "postgresql", "postgres", "mysql", "mongodb", "sqlite", "firebase",
        "redis", "dynamodb", "sql server", "oracle", "elasticsearch",
    ),
    "Cloud & DevOps": (
        "aws", "ec2", "rds", "s3", "azure", "gcp", "docker", "kubernetes",
        "k8s", "terraform", "ci/cd", "jenkins", "github actions", "nginx",
    ),
    "Testing": (
        "pytest", "jest", "selenium", "cypress", "integration testing",
        "integration tests", "unit testing", "automated testing",
    ),
    "Tools & Version Control": (
        "git", "github", "gitlab", "jira", "postman", "figma",
    ),
    "AI & Data": (
        "machine learning", "llm", "llms", "generative ai", "openai",
        "pandas", "numpy", "tensorflow", "pytorch", "data analysis",
    ),
}

# Non-technical / universal categories.
UNIVERSAL_TAXONOMY: dict[str, tuple[str, ...]] = {
    "Customer Service": (
        "customer service", "customer support", "complaint handling",
        "client relations", "help desk", "call center",
    ),
    "Sales": (
        "sales", "crm", "salesforce", "hubspot", "lead generation",
        "account management", "negotiation",
    ),
    "Administration": (
        "administration", "scheduling", "calendar management", "filing",
        "office management", "data entry", "documentation",
    ),
    "Finance": (
        "bookkeeping", "accounting", "quickbooks", "invoicing", "payroll",
        "budgeting", "excel reporting",
    ),
    "Operations": (
        "operations", "inventory", "logistics", "supply chain", "warehouse",
        "workforce scheduling", "process improvement",
    ),
    "Leadership": (
        "leadership", "team leadership", "mentoring", "supervision",
        "people management",
    ),
    "Communication": (
        "communication", "presentation", "public speaking", "writing",
        "stakeholder communication",
    ),
    "Healthcare Systems": (
        "ehr", "emr", "hipaa", "patient scheduling", "clinical",
    ),
    "Equipment": (
        "forklift", "cnc", "manufacturing equipment", "heavy machinery",
    ),
    "Certifications": (
        "certified", "certification", "license", "licensed",
    ),
}

OTHER = "Other Relevant Skills"

_ALIAS_NORMALIZE = {
    "nodejs": "node.js",
    "node": "node.js",
    "postgres": "postgresql",
    "restful": "rest api",
    "restful api": "rest api",
    "restful apis": "rest apis",
    "k8s": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
}


def normalize_skill_name(skill: str) -> str:
    text = re.sub(r"\s+", " ", (skill or "").strip())
    low = text.lower()
    return _ALIAS_NORMALIZE.get(low, low)


def categorize_skill(skill: str) -> str:
    """Return the canonical category for a skill atom."""
    # Strip existing category prefixes
    atom = skill.split(":", 1)[-1].strip() if ":" in skill else skill.strip()
    low = normalize_skill_name(atom)

    for category, members in SOFTWARE_TAXONOMY.items():
        for member in members:
            if low == member or low.startswith(member + " ") or member in low.split(", "):
                return category
            # Exact-ish containment for multiword
            if len(member) >= 4 and (low == member or re.search(rf"\b{re.escape(member)}\b", low)):
                return category

    for category, members in UNIVERSAL_TAXONOMY.items():
        for member in members:
            if low == member or (len(member) >= 5 and member in low):
                return category

    return OTHER


def _split_skill_atoms(skill_line: str) -> list[str]:
    text = str(skill_line or "").strip()
    if not text:
        return []
    if ":" in text:
        _, rest = text.split(":", 1)
        text = rest
    return [a.strip() for a in text.split(",") if a.strip()]


def normalize_skill_lines(
    skills: list[str],
    *,
    emphasize: list[str] | None = None,
) -> list[str]:
    """Rebuild skill lines with deterministic categories.

    Preserves every evidenced skill atom; never invents new skills.
    When ``emphasize`` is provided, those atoms (and their categories) are
    ordered first so different target jobs produce different skill rankings.
    """
    buckets: dict[str, list[str]] = {}
    order: list[str] = (
        list(SOFTWARE_TAXONOMY.keys())
        + list(UNIVERSAL_TAXONOMY.keys())
        + [OTHER]
    )
    for cat in order:
        buckets[cat] = []

    emphasize_norm = [normalize_skill_name(str(s)) for s in (emphasize or []) if str(s).strip()]

    seen_atoms: set[str] = set()
    for line in skills or []:
        for atom in _split_skill_atoms(str(line)):
            key = normalize_skill_name(atom)
            if key in seen_atoms:
                continue
            seen_atoms.add(key)
            cat = categorize_skill(atom)
            buckets.setdefault(cat, []).append(atom)

    def _atom_rank(atom: str) -> tuple[int, str]:
        key = normalize_skill_name(atom)
        try:
            return (emphasize_norm.index(key), key)
        except ValueError:
            # Partial match against emphasize list
            for i, emp in enumerate(emphasize_norm):
                if emp and (emp in key or key in emp):
                    return (i, key)
            return (1000, key)

    # Categories containing emphasized skills come first
    def _cat_rank(cat: str) -> tuple[int, int]:
        atoms = buckets.get(cat) or []
        if not atoms:
            return (999, order.index(cat) if cat in order else 999)
        best = min(_atom_rank(a)[0] for a in atoms)
        return (best, order.index(cat) if cat in order else 999)

    ranked_cats = sorted(
        [c for c in order if buckets.get(c)],
        key=_cat_rank,
    )

    result: list[str] = []
    for cat in ranked_cats:
        atoms = sorted(buckets.get(cat) or [], key=_atom_rank)
        if atoms:
            result.append(f"{cat}: {', '.join(atoms)}")
    for cat, atoms in buckets.items():
        if cat not in order and atoms:
            result.append(f"{cat}: {', '.join(sorted(atoms, key=_atom_rank))}")
    return result


def assert_skill_classification_examples() -> dict[str, str]:
    """Helper used by tests — expected classifications for common items."""
    samples = [
        "Laravel", "REST API", "Git", "React", "Angular", "HTML", "CSS",
        "FastAPI", "PostgreSQL", "Python", "pytest", "Docker",
    ]
    return {s: categorize_skill(s) for s in samples}
