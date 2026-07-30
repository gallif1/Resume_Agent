"""Universal job-profile detection — profession-agnostic emphasis.

Tech families (backend/frontend/…) remain as optional soft signals for tests,
but scoring and strategy are driven by extracted job requirements and
competency clusters — not hard-coded title templates.
"""

from __future__ import annotations

import re
from typing import Any

# Soft signals only — never the sole driver of generation.
JOB_FAMILIES = (
    "backend",
    "frontend",
    "devops",
    "qa",
    "support",
    "sales",
    "marketing",
    "finance",
    "operations",
    "administration",
    "education",
    "healthcare",
    "customer_service",
    "hr",
    "legal",
    "logistics",
    "manufacturing",
    "hospitality",
    "retail",
    "construction",
    "design",
    "management",
    "public_sector",
    "general",
)

_FAMILY_PATTERNS: dict[str, list[str]] = {
    "backend": [r"backend", r"back-end", r"api developer", r"server.?side"],
    "frontend": [r"frontend", r"front-end", r"ui developer", r"react developer"],
    "devops": [r"devops", r"sre", r"site reliability", r"platform engineer"],
    "qa": [r"\bqa\b", r"quality assurance", r"test engineer"],
    "support": [r"technical support", r"it support", r"help ?desk", r"support engineer"],
    "sales": [r"\bsales\b", r"account executive", r"business development", r"account manager"],
    "marketing": [r"marketing", r"brand manager", r"content strategist", r"growth"],
    "finance": [r"financ", r"accountant", r"bookkeeper", r"controller", r"treasury"],
    "operations": [r"operations", r"ops manager", r"process owner"],
    "administration": [r"administrator", r"office manager", r"executive assistant", r"secretary"],
    "education": [r"teacher", r"tutor", r"instructor", r"educator", r"lecturer", r"מורה"],
    "healthcare": [r"nurse", r"clinic", r"healthcare", r"medical admin", r"patient"],
    "customer_service": [r"customer service", r"customer success", r"client relations"],
    "hr": [r"human resources", r"\bhr\b", r"recruiter", r"people operations"],
    "legal": [r"paralegal", r"legal assistant", r"attorney", r"counsel"],
    "logistics": [r"logistics", r"supply chain", r"warehouse", r"dispatcher"],
    "manufacturing": [r"manufactur", r"production", r"factory", r"machinist"],
    "hospitality": [r"hotel", r"hospitality", r"restaurant manager", r"front desk"],
    "retail": [r"retail", r"store manager", r"cashier", r"merchandis"],
    "construction": [r"construction", r"carpenter", r"electrician", r"plumber", r"foreman"],
    "design": [r"graphic design", r"ux designer", r"ui designer", r"art director"],
    "management": [r"manager", r"director", r"team lead", r"supervisor"],
    "public_sector": [r"municipal", r"government", r"public sector", r"civil service"],
}


def detect_job_family(
    job_title: str,
    requirements: dict[str, Any] | None = None,
) -> str:
    """Soft family label from title + requirements — falls back to general."""
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
    title_lower = (job_title or "").lower()

    scores: dict[str, int] = {fam: 0 for fam in JOB_FAMILIES if fam != "general"}
    for family, patterns in _FAMILY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, title_lower, re.I):
                scores[family] += 6
            elif re.search(pat, blob, re.I):
                scores[family] += 2

    best = max(scores.items(), key=lambda x: x[1])
    return best[0] if best[1] >= 3 else "general"


def detect_industry(job_title: str, requirements: dict[str, Any] | None = None) -> str:
    """Map family soft-signal to a broader industry label."""
    family = detect_job_family(job_title, requirements)
    tech = {"backend", "frontend", "devops", "qa", "support"}
    if family in tech:
        return "technology"
    if family == "general":
        return "general"
    return family


def emphasis_keywords_from_requirements(
    requirements: dict[str, Any] | None = None,
    *,
    job_family: str = "general",
) -> dict[str, int]:
    """Build emphasis weights from JD terms — universal, not hard-coded tech lists."""
    weights: dict[str, int] = {}
    if not requirements:
        return weights

    def _add(terms: list[Any], base: int) -> None:
        for t in terms:
            key = str(t).strip().lower()
            if len(key) < 2:
                continue
            # Prefer multi-word phrases and distinctive tokens
            weights[key] = max(weights.get(key, 0), base)
            for token in re.findall(r"[a-z0-9\u0590-\u05ff]{3,}", key):
                weights[token] = max(weights.get(token, 0), max(12, base - 8))

    _add(list(requirements.get("hard_requirements") or []), 32)
    _add(list(requirements.get("required_skills") or []), 30)
    _add(list(requirements.get("responsibilities") or []), 24)
    _add(list(requirements.get("tools_technologies") or []), 26)
    _add(list(requirements.get("ats_keywords") or []), 22)
    _add(list(requirements.get("soft_requirements") or []), 18)
    _add(list(requirements.get("preferred_skills") or []), 16)
    _add(list(requirements.get("soft_skills") or []), 14)
    _add(list(requirements.get("industry_terminology") or []), 20)

    # Soft family boost retained only as a light prior for known tech families
    soft_prior = _SOFT_FAMILY_PRIOR.get(job_family) or {}
    for k, v in soft_prior.items():
        weights[k] = max(weights.get(k, 0), v)
    return weights


# Light priors only — never sole source of emphasis.
_SOFT_FAMILY_PRIOR: dict[str, dict[str, int]] = {
    "backend": {"api": 10, "database": 10, "backend": 8},
    "frontend": {"ui": 10, "react": 10, "frontend": 8},
    "devops": {"aws": 10, "deploy": 10, "ci/cd": 8},
    "qa": {"test": 10, "debug": 10, "quality": 8},
    "support": {"troubleshoot": 10, "customer": 10, "ticket": 8},
    "sales": {"sales": 10, "pipeline": 8, "quota": 8},
    "marketing": {"campaign": 10, "content": 8, "brand": 8},
    "finance": {"invoice": 8, "budget": 10, "reconcile": 8},
    "operations": {"schedule": 8, "inventory": 8, "process": 10},
    "education": {"teach": 10, "lesson": 8, "student": 8},
    "healthcare": {"patient": 10, "clinical": 8, "appointment": 8},
    "customer_service": {"customer": 10, "complaint": 8, "service": 8},
}


def emphasis_keywords(job_family: str) -> dict[str, int]:
    """Backward-compatible helper — prefer emphasis_keywords_from_requirements."""
    return dict(_SOFT_FAMILY_PRIOR.get(job_family) or {})


def deprioritize_keywords_from_requirements(
    requirements: dict[str, Any] | None,
    resume_skills: list[str],
) -> list[str]:
    """Deprioritize resume skills that do not appear in JD terms."""
    if not requirements:
        return []
    jd_blob = " ".join(
        str(x)
        for key in (
            "required_skills",
            "preferred_skills",
            "tools_technologies",
            "responsibilities",
            "ats_keywords",
            "hard_requirements",
        )
        for x in (requirements.get(key) or [])
    ).lower()
    deprioritize: list[str] = []
    for skill in resume_skills:
        s = str(skill).strip().lower()
        if len(s) < 3:
            continue
        # Keep if mentioned in JD
        if s in jd_blob or any(tok in jd_blob for tok in s.split() if len(tok) > 3):
            continue
        deprioritize.append(str(skill).strip())
    return deprioritize[:12]


def deprioritize_keywords(job_family: str) -> list[str]:
    """Legacy stub — empty by default to avoid tech-biased hiding."""
    return []


def skill_category_order(job_family: str) -> list[str]:
    """Generic category order; strategy builder may override from JD."""
    orders = {
        "sales": ["Sales & CRM", "Communication", "Tools", "Other"],
        "marketing": ["Marketing", "Content & Campaigns", "Tools", "Other"],
        "finance": ["Finance & Accounting", "Tools", "Other"],
        "operations": ["Operations", "Tools", "Other"],
        "education": ["Teaching & Instruction", "Subject Knowledge", "Tools", "Other"],
        "healthcare": ["Healthcare Administration", "Compliance", "Tools", "Other"],
        "customer_service": ["Customer Service", "Communication", "Tools", "Other"],
        "backend": ["Languages & Frameworks", "APIs & Backend", "Databases", "Tools", "Other"],
        "frontend": ["Frontend Frameworks", "UI & Styling", "Languages", "Tools", "Other"],
        "devops": ["Cloud & Infrastructure", "CI/CD & Automation", "Languages", "Other"],
        "qa": ["Testing & Quality", "Debugging & Analysis", "Tools", "Other"],
        "support": ["Support & Troubleshooting", "Communication", "Tools", "Other"],
    }
    return list(orders.get(job_family) or ["Core Competencies", "Tools", "Other"])


def project_priority_hints(job_family: str) -> list[str]:
    """Soft project-name hints — empty for most professions (evidence-driven instead)."""
    return list(
        {
            "devops": ["monitor", "infra", "deploy"],
            "frontend": ["app", "ui", "mobile"],
            "backend": ["api", "backend", "service"],
            "marketing": ["campaign", "brand"],
            "education": ["curriculum", "course"],
        }.get(job_family)
        or []
    )


def infer_primary_role(job_family: str, job_title: str) -> str:
    if job_title.strip():
        return job_title.strip()
    return job_family.replace("_", " ").title() if job_family != "general" else "Professional"


def infer_secondary_role(job_family: str) -> str:
    return {
        "backend": "API & data-layer development",
        "frontend": "Client-side UI development",
        "devops": "Infrastructure & deployment",
        "qa": "Quality assurance & testing",
        "support": "Technical troubleshooting & customer issues",
        "sales": "Customer acquisition & relationship management",
        "marketing": "Campaign execution & audience engagement",
        "finance": "Financial accuracy & reporting",
        "operations": "Process ownership & coordination",
        "education": "Instruction & knowledge transfer",
        "healthcare": "Patient/client administration & documentation",
        "customer_service": "Issue resolution & customer care",
        "administration": "Office coordination & administrative support",
        "hr": "People operations & onboarding",
        "legal": "Document review & compliance support",
        "logistics": "Logistics & supply-chain coordination",
        "manufacturing": "Production & quality operations",
        "hospitality": "Guest service operations",
        "retail": "Retail operations & customer service",
        "construction": "Site operations & skilled trade work",
        "design": "Visual communication & design execution",
        "management": "Team leadership & operational oversight",
        "public_sector": "Public service & case administration",
        "general": "Professional contribution aligned to the role",
    }.get(job_family, "Professional contribution aligned to the role")
