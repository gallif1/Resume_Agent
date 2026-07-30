"""Job-family detection and per-family emphasis rules for deep tailoring."""

from __future__ import annotations

import re
from typing import Any

JOB_FAMILIES = (
    "backend",
    "frontend",
    "devops",
    "qa",
    "support",
    "general",
)

_FAMILY_PATTERNS: dict[str, list[str]] = {
    "backend": [
        r"backend",
        r"back-end",
        r"server side",
        r"api developer",
        r"microservice",
        r"fastapi",
        r"django",
        r"spring boot",
        r"node\.?js backend",
    ],
    "frontend": [
        r"frontend",
        r"front-end",
        r"front end",
        r"ui developer",
        r"react developer",
        r"angular",
        r"client-side",
        r"web developer",
    ],
    "devops": [
        r"devops",
        r"dev ops",
        r"sre",
        r"site reliability",
        r"infrastructure",
        r"platform engineer",
        r"cloud engineer",
        r"ci/?cd",
        r"kubernetes",
    ],
    "qa": [
        r"\bqa\b",
        r"quality assurance",
        r"test engineer",
        r"software tester",
        r"automation tester",
        r"selenium",
    ],
    "support": [
        r"technical support",
        r"it support",
        r"help ?desk",
        r"customer support",
        r"support engineer",
        r"service desk",
    ],
}

# Keyword bonuses applied when scoring resume content for a job family.
_EMPHASIS_KEYWORDS: dict[str, dict[str, int]] = {
    "backend": {
        "rest": 28,
        "api": 28,
        "fastapi": 32,
        "postgresql": 30,
        "sql": 26,
        "database": 24,
        "backend": 22,
        "server": 18,
        "websocket": 22,
        "validation": 16,
        "concurrency": 18,
        "performance": 16,
        "architecture": 18,
        "business logic": 20,
        "sqlalchemy": 22,
        "laravel": 18,
        "node.js": 14,
    },
    "frontend": {
        "react": 32,
        "angular": 30,
        "react native": 28,
        "ui": 24,
        "ux": 20,
        "html": 22,
        "css": 22,
        "responsive": 22,
        "client": 20,
        "frontend": 22,
        "expo": 18,
        "component": 18,
        "interface": 16,
        "javascript": 18,
        "typescript": 18,
    },
    "devops": {
        "aws": 32,
        "deploy": 28,
        "deployment": 28,
        "ci/cd": 30,
        "cicd": 30,
        "infrastructure": 28,
        "monitor": 26,
        "monitoring": 26,
        "logging": 24,
        "automation": 22,
        "git": 20,
        "cloud": 26,
        "server": 20,
        "health": 18,
        "docker": 24,
        "kubernetes": 24,
        "threadpool": 16,
    },
    "qa": {
        "test": 28,
        "testing": 28,
        "debug": 30,
        "debugging": 30,
        "troubleshoot": 28,
        "validation": 26,
        "bug": 26,
        "reproduce": 24,
        "documentation": 20,
        "reliability": 22,
        "quality": 24,
        "regression": 22,
        "verify": 20,
    },
    "support": {
        "customer": 28,
        "support": 28,
        "troubleshoot": 30,
        "investigate": 26,
        "root cause": 28,
        "logs": 24,
        "ticket": 22,
        "issue": 22,
        "problem solving": 24,
        "collaboration": 20,
        "cross-functional": 22,
        "erp": 18,
        "communication": 20,
    },
}

_DEPRIORITIZE_KEYWORDS: dict[str, list[str]] = {
    "backend": ["html", "css", "react native", "expo", "ui design"],
    "frontend": ["sqlalchemy", "database design", "microservice"],
    "devops": ["react", "angular", "ui", "css"],
    "qa": ["marketing", "sales"],
    "support": ["machine learning", "generative ai"],
}

_SKILL_CATEGORY_ORDER: dict[str, list[str]] = {
    "backend": [
        "Languages & Frameworks",
        "APIs & Backend",
        "Databases",
        "Cloud & DevOps",
        "Tools",
        "Other",
    ],
    "frontend": [
        "Frontend Frameworks",
        "UI & Styling",
        "Languages",
        "API Integration",
        "Tools",
        "Other",
    ],
    "devops": [
        "Cloud & Infrastructure",
        "CI/CD & Automation",
        "Monitoring & Logging",
        "Languages",
        "Databases",
        "Other",
    ],
    "qa": [
        "Testing & Quality",
        "Debugging & Analysis",
        "Languages",
        "Tools",
        "Other",
    ],
    "support": [
        "Support & Troubleshooting",
        "Communication",
        "Systems & Networking",
        "Tools",
        "Other",
    ],
    "general": ["Skills", "Tools", "Other"],
}

_PROJECT_PRIORITY_HINTS: dict[str, list[str]] = {
    "backend": ["restaurant", "api", "backend", "fastapi", "erp"],
    "frontend": ["restaurant", "react", "ui", "app", "expo"],
    "devops": ["server monitor", "monitor", "threadpool", "deployment"],
    "qa": ["server monitor", "debug", "test"],
    "support": ["erp", "support", "troubleshoot"],
}


def detect_job_family(
    job_title: str,
    requirements: dict[str, Any] | None = None,
) -> str:
    """Rule-based job family from title + extracted requirements."""
    blob_parts = [job_title or ""]
    if requirements:
        for key in (
            "required_skills",
            "responsibilities",
            "tools_technologies",
            "industry_terminology",
            "ats_keywords",
            "hard_requirements",
        ):
            vals = requirements.get(key) or []
            if isinstance(vals, list):
                blob_parts.extend(str(v) for v in vals)
    blob = " ".join(blob_parts).lower()

    scores: dict[str, int] = {fam: 0 for fam in JOB_FAMILIES if fam != "general"}
    for family, patterns in _FAMILY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, blob, re.I):
                scores[family] += 3
    for family, keywords in _EMPHASIS_KEYWORDS.items():
        for kw, weight in keywords.items():
            if kw in blob:
                scores[family] += min(weight // 4, 8)

    best = max(scores.items(), key=lambda x: x[1])
    if best[1] < 3:
        return "general"
    # Secondary check: title often decisive
    title_lower = (job_title or "").lower()
    for family, patterns in _FAMILY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, title_lower, re.I):
                scores[family] += 5
    best = max(scores.items(), key=lambda x: x[1])
    return best[0] if best[1] >= 3 else "general"


def emphasis_keywords(job_family: str) -> dict[str, int]:
    return dict(_EMPHASIS_KEYWORDS.get(job_family) or _EMPHASIS_KEYWORDS["backend"])


def deprioritize_keywords(job_family: str) -> list[str]:
    return list(_DEPRIORITIZE_KEYWORDS.get(job_family) or [])


def skill_category_order(job_family: str) -> list[str]:
    return list(_SKILL_CATEGORY_ORDER.get(job_family) or _SKILL_CATEGORY_ORDER["general"])


def project_priority_hints(job_family: str) -> list[str]:
    return list(_PROJECT_PRIORITY_HINTS.get(job_family) or [])


def infer_primary_role(job_family: str, job_title: str) -> str:
    if job_title.strip():
        return job_title.strip()
    return {
        "backend": "Backend Engineer",
        "frontend": "Frontend Developer",
        "devops": "DevOps Engineer",
        "qa": "QA Engineer",
        "support": "Technical Support Specialist",
        "general": "Software Engineer",
    }.get(job_family, "Software Engineer")


def infer_secondary_role(job_family: str) -> str:
    return {
        "backend": "API & data-layer development",
        "frontend": "Client-side UI development",
        "devops": "Infrastructure & deployment",
        "qa": "Quality assurance & testing",
        "support": "Technical troubleshooting & customer issues",
        "general": "Software development",
    }.get(job_family, "Software development")
