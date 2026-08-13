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
        "expo", "next.js", "nextjs", "svelte", "tailwind", "ajax",
    ),
    "Backend": (
        "fastapi", "django", "flask", "node.js", "nodejs", "nestjs", "express",
        "laravel", "spring", "rails", "rest api", "rest apis", "rest",
        "websockets", "websocket", "graphql", "sqlalchemy", "sql/alchemy",
        "prisma",
    ),
    "Databases": (
        "postgresql", "postgres", "mysql", "mongodb", "sqlite", "firebase",
        "redis", "dynamodb", "sql server", "oracle", "elasticsearch", "lucene",
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
    "AI-Assisted Development": (
        "cursor", "chatgpt", "claude", "github copilot", "copilot",
        "ai-assisted development", "ai assisted development",
        "ai coding tools", "ai pair programming",
    ),
    "AI & Data": (
        "machine learning", "llm", "llms", "generative ai", "openai",
        "pandas", "numpy", "tensorflow", "pytorch", "data analysis",
    ),
}

# Bare / generic atoms that must never appear as standalone skill lines.
_DROP_SKILL_ATOMS = frozenset(
    {
        "api",
        "apis",
        "web",
        "software",
        "programming",
        "coding",
        "development",
        "developer",
        "engineer",
        "technology",
        "technologies",
        "tools",
        "other",
        "relevant",
        "skills",
        "framework",
        "frameworks",
        "language",
        "languages",
        "database",
        "databases",
        "cloud",
        "backend",
        "frontend",
        "fullstack",
        "full stack",
        "full-stack",
        # Abstract competencies that must not appear as free-form skills
        "architecture",
        "architectures",
        "design",
        "system design",
        "systems",
        "applications",
        "services",
        "platform",
        "platforms",
        "infrastructure",
        "scalability",
        "performance",
        "problem solving",
        "problem-solving",
        "teamwork",
        "collaboration",
    }
)

# Prefer canonical display names when normalizing atoms.
_DISPLAY_CANONICAL = {
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "rest": "REST APIs",
    "rest api": "REST APIs",
    "rest apis": "REST APIs",
    "restful api": "REST APIs",
    "restful apis": "REST APIs",
    "websocket": "WebSockets",
    "websockets": "WebSockets",
    "sqlalchemy": "SQLAlchemy",
    "sql/alchemy": "SQLAlchemy",
    "sql alchemy": "SQLAlchemy",
    "ci/cd": "CI/CD",
    "react native": "React Native",
    "ajax": "Ajax",
    "lucene": "Lucene",
    "generative ai": "Generative AI",
    "github copilot": "GitHub Copilot",
    "chatgpt": "ChatGPT",
    "integration testing": "integration testing",
    "integration tests": "integration testing",
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
    "sql/alchemy": "sqlalchemy",
    "sql alchemy": "sqlalchemy",
    "k8s": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
}


def normalize_skill_name(skill: str) -> str:
    text = re.sub(r"\s+", " ", (skill or "").strip())
    low = text.lower()
    return _ALIAS_NORMALIZE.get(low, low)


def should_drop_skill_atom(skill: str) -> bool:
    """True for bare generics that produce noise like ``Other Relevant Skills: api``."""
    atom = skill.split(":", 1)[-1].strip() if ":" in skill else skill.strip()
    low = normalize_skill_name(atom)
    if low in _DROP_SKILL_ATOMS:
        return True
    # Single ultra-generic tokens under 4 chars (except known acronyms)
    if len(low) <= 2 and low not in {"c#", "r", "go", "c++", "js", "ts", "sql"}:
        return True
    return False


def display_skill_name(skill: str) -> str:
    atom = skill.split(":", 1)[-1].strip() if ":" in skill else skill.strip()
    low = normalize_skill_name(atom)
    if low in _DISPLAY_CANONICAL:
        return _DISPLAY_CANONICAL[low]
    # Preserve original casing for multi-word verified skills
    return atom


def categorize_skill(skill: str) -> str:
    """Return the canonical category for a skill atom."""
    # Strip existing category prefixes
    atom = skill.split(":", 1)[-1].strip() if ":" in skill else skill.strip()
    low = normalize_skill_name(atom)

    if should_drop_skill_atom(atom):
        return OTHER

    # Check more specific categories first (AI-Assisted before Tools / AI & Data)
    ordered_software = list(SOFTWARE_TAXONOMY.items())
    for category, members in ordered_software:
        for member in members:
            if low == member or low.startswith(member + " ") or member in low.split(", "):
                return category
            # Exact-ish containment for multiword — avoid matching "api" inside "rest api"
            # via bare "api" alone (already dropped). Require member length >= 3 with word boundary.
            if len(member) >= 3 and (low == member or re.search(rf"\b{re.escape(member)}\b", low)):
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


def category_order_for_role(
    job_family: str | None = None,
    *,
    emphasize: list[str] | None = None,
) -> list[str]:
    """Dynamic category order for the target role — profession-aware.

    Uses canonical taxonomy names so rebuilder + normalize_skill_lines agree.
    """
    family = (job_family or "general").strip().lower()
    presets: dict[str, list[str]] = {
        "backend": [
            "Backend", "Languages", "Databases", "Cloud & DevOps",
            "AI-Assisted Development", "AI & Data",
            "Frontend", "Testing", "Tools & Version Control",
        ],
        "frontend": [
            "Frontend", "Languages", "Backend", "Testing", "Cloud & DevOps",
            "AI-Assisted Development", "Tools & Version Control", "Databases",
        ],
        "devops": [
            "Cloud & DevOps", "Languages", "Backend", "Databases", "Testing",
            "AI-Assisted Development", "Tools & Version Control", "Frontend",
        ],
        "qa": [
            "Testing", "Languages", "Backend", "Frontend", "Tools & Version Control",
            "Cloud & DevOps", "AI-Assisted Development", "Databases",
        ],
        "support": [
            "Tools & Version Control", "Cloud & DevOps", "Backend", "Databases",
            "Communication", "Customer Service", "Languages",
        ],
        "data": [
            "AI & Data", "AI-Assisted Development", "Languages", "Databases",
            "Cloud & DevOps", "Backend", "Tools & Version Control",
        ],
        "sales": [
            "Sales", "Communication", "Customer Service", "Leadership",
            "Administration", "Tools & Version Control",
        ],
        "marketing": [
            "Communication", "Sales", "AI & Data", "Tools & Version Control",
            "Frontend", "Leadership",
        ],
        "finance": [
            "Finance", "Administration", "AI & Data", "Tools & Version Control",
            "Communication", "Leadership",
        ],
        "healthcare": [
            "Healthcare Systems", "Customer Service", "Communication",
            "Administration", "Leadership", "Certifications",
        ],
        "education": [
            "Communication", "Leadership", "Administration", "Customer Service",
            "Tools & Version Control", "Certifications",
        ],
        "hospitality": [
            "Customer Service", "Communication", "Leadership", "Operations",
            "Administration",
        ],
        "operations": [
            "Operations", "Leadership", "Administration", "Communication",
            "Tools & Version Control",
        ],
        "customer_service": [
            "Customer Service", "Communication", "Sales", "Administration",
            "Leadership",
        ],
        "hr": [
            "Communication", "Leadership", "Administration", "Customer Service",
            "Certifications",
        ],
        "legal": [
            "Administration", "Communication", "Certifications", "Leadership",
        ],
        "manufacturing": [
            "Operations", "Equipment", "Leadership", "Administration",
            "Communication",
        ],
        "construction": [
            "Equipment", "Operations", "Leadership", "Certifications",
            "Communication",
        ],
        "retail": [
            "Sales", "Customer Service", "Operations", "Communication",
            "Leadership",
        ],
    }
    base = list(SOFTWARE_TAXONOMY.keys()) + list(UNIVERSAL_TAXONOMY.keys()) + [OTHER]
    preferred = presets.get(family, [])
    order = [c for c in preferred if c in base or c == OTHER]
    for cat in base:
        if cat not in order:
            order.append(cat)

    # Boost categories that contain emphasized skills to the front — but keep
    # the family's primary categories locked ahead (Frontend JD must not lead
    # with Cloud just because AWS fragments appeared in emphasize).
    if emphasize:
        boost: list[str] = []
        for skill in emphasize:
            cat = categorize_skill(str(skill))
            if cat not in boost:
                boost.append(cat)
        if boost:
            primary = preferred[:2]
            boost = [c for c in boost if c not in primary]
            locked = [c for c in primary if c in order]
            order = (
                locked
                + [c for c in boost if c in order]
                + [c for c in order if c not in locked and c not in boost]
            )
    return order


def normalize_skill_lines(
    skills: list[str],
    *,
    emphasize: list[str] | None = None,
    job_family: str | None = None,
    category_order: list[str] | None = None,
) -> list[str]:
    """Rebuild skill lines with deterministic categories.

    Preserves every evidenced skill atom; never invents new skills.
    When ``emphasize`` is provided, those atoms (and their categories) are
    ordered first so different target jobs produce different skill rankings.
    """
    buckets: dict[str, list[str]] = {}
    order: list[str] = list(
        category_order
        or category_order_for_role(job_family, emphasize=emphasize)
    )
    for cat in order:
        buckets[cat] = []

    emphasize_norm = [
        normalize_skill_name(str(s))
        for s in (emphasize or [])
        if str(s).strip() and not re.search(r"[()]", str(s))
    ]

    seen_atoms: set[str] = set()
    for line in skills or []:
        for atom in _split_skill_atoms(str(line)):
            if should_drop_skill_atom(atom):
                continue
            key = normalize_skill_name(atom)
            if key in seen_atoms:
                continue
            seen_atoms.add(key)
            cat = categorize_skill(atom)
            if cat == OTHER and should_drop_skill_atom(atom):
                continue
            buckets.setdefault(cat, []).append(display_skill_name(atom))

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

    # Family primary categories stay first; emphasize only reorders atoms and
    # secondary categories (Cloud must not leapfrog Frontend on a Frontend JD).
    primary_locked = category_order_for_role(job_family, emphasize=None)[:2] if job_family else []

    def _cat_rank(cat: str) -> tuple[int, int]:
        if cat in primary_locked:
            return (-10 + primary_locked.index(cat), 0)
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
        if cat == OTHER:
            # Never emit a category whose only members are generic drop-atoms
            atoms = [
                a
                for a in sorted(buckets.get(cat) or [], key=_atom_rank)
                if not should_drop_skill_atom(a)
            ]
        else:
            atoms = sorted(buckets.get(cat) or [], key=_atom_rank)
        if atoms:
            result.append(f"{cat}: {', '.join(atoms)}")
    for cat, atoms in buckets.items():
        if cat not in order and atoms:
            cleaned = [a for a in atoms if not should_drop_skill_atom(a)]
            if cleaned:
                result.append(f"{cat}: {', '.join(sorted(cleaned, key=_atom_rank))}")
    # Final guard: never return "Other Relevant Skills: api" style lines
    result = [
        line
        for line in result
        if not re.match(rf"^{re.escape(OTHER)}:\s*(api|apis|web)\s*$", line, re.I)
    ]
    return result


def assert_skill_classification_examples() -> dict[str, str]:
    """Helper used by tests — expected classifications for common items."""
    samples = [
        "Laravel", "REST API", "Git", "React", "Angular", "HTML", "CSS",
        "FastAPI", "PostgreSQL", "Python", "pytest", "Docker",
    ]
    return {s: categorize_skill(s) for s in samples}
