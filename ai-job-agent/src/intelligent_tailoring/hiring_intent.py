"""Hiring-intent inference — what person the company actually wants.

Profession-agnostic. Used by Job Intelligence + Strategy to steer storytelling
toward interview probability, not keyword extraction.
"""

from __future__ import annotations

import re
from typing import Any

# Family → ordered hiring priorities a recruiter typically scans for.
_FAMILY_PRIORITIES: dict[str, list[str]] = {
    "backend": [
        "Scalable systems",
        "API design",
        "Databases",
        "Problem solving",
        "Cloud / infrastructure",
        "Reliability",
    ],
    "frontend": [
        "UI craft",
        "React / modern frameworks",
        "Performance",
        "Accessibility",
        "Component architecture",
        "UX collaboration",
    ],
    "fullstack": [
        "End-to-end ownership",
        "APIs + UI",
        "Product mindset",
        "Databases",
        "Delivery speed",
    ],
    "devops": [
        "Cloud platforms",
        "CI/CD automation",
        "Reliability / monitoring",
        "Infrastructure as code",
        "Incident response",
    ],
    "data": [
        "Data modeling",
        "SQL / analytics",
        "Pipelines",
        "Insight communication",
        "Quality / governance",
    ],
    "sales": [
        "Revenue ownership",
        "Negotiation",
        "CRM discipline",
        "Communication",
        "Pipeline management",
    ],
    "marketing": [
        "Campaign ownership",
        "Audience insight",
        "Content / messaging",
        "Analytics",
        "Cross-functional collaboration",
    ],
    "finance": [
        "Accuracy / reconciliation",
        "Forecasting",
        "Controls / compliance",
        "Stakeholder reporting",
        "Systems fluency",
    ],
    "healthcare": [
        "Patient care",
        "Clinical documentation",
        "Compliance",
        "Communication",
        "Care coordination",
    ],
    "education": [
        "Teaching / facilitation",
        "Communication",
        "Classroom leadership",
        "Curriculum ownership",
        "Patience / coaching",
    ],
    "legal": [
        "Risk judgment",
        "Research / writing",
        "Client counsel",
        "Compliance",
        "Attention to detail",
    ],
    "operations": [
        "Process ownership",
        "Cross-functional coordination",
        "Quality / safety",
        "Problem solving",
        "Continuous improvement",
    ],
    "customer_support": [
        "Customer empathy",
        "Issue resolution",
        "Communication",
        "Product knowledge",
        "Escalation judgment",
    ],
    "customer_service": [
        "Customer empathy",
        "Issue resolution",
        "Communication",
        "Product knowledge",
        "Escalation judgment",
    ],
    "support": [
        "Issue diagnosis",
        "Customer communication",
        "Product knowledge",
        "Documentation",
        "Escalation judgment",
    ],
    "qa": [
        "Test coverage",
        "Defect discovery",
        "Quality judgment",
        "Automation",
        "Risk communication",
    ],
    "hr": [
        "People partnership",
        "Communication",
        "Process ownership",
        "Confidentiality",
        "Stakeholder management",
    ],
    "design": [
        "User-centered craft",
        "Visual systems",
        "Collaboration with eng/product",
        "Prototyping",
        "Accessibility",
    ],
    "administration": [
        "Organization",
        "Communication",
        "Process ownership",
        "Attention to detail",
        "Stakeholder support",
    ],
    "management": [
        "Leadership",
        "Decision making",
        "Cross-functional coordination",
        "Ownership",
        "Communication",
    ],
    "general": [
        "Ownership",
        "Problem solving",
        "Communication",
        "Learning ability",
        "Reliability",
    ],
}

_FAMILY_ALIASES: dict[str, str] = {
    "customer_service": "customer_support",
    "support": "support",
    "fullstack": "fullstack",
    "full-stack": "fullstack",
    "full_stack": "fullstack",
}


def _normalize_family(job_family: str) -> str:
    family = (job_family or "general").lower().strip() or "general"
    family = _FAMILY_ALIASES.get(family, family)
    if family in _FAMILY_PRIORITIES:
        return family
    return "general"

_SIGNAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ownership", re.compile(r"\b(own|ownership|accountable|end[- ]to[- ]end|drove)\b", re.I)),
    ("problem_solving", re.compile(r"\b(problem.?solv|troubleshoot|debug|root cause|diagnos)\b", re.I)),
    ("scalability", re.compile(r"\b(scalab|high.?traffic|throughput|latency|distributed)\b", re.I)),
    ("learning_agility", re.compile(r"\b(learn|curious|fast.?paced|startup|adapt|upskill)\b", re.I)),
    ("collaboration", re.compile(r"\b(cross[- ]functional|collaborat|stakeholder|partner with)\b", re.I)),
    ("customer_focus", re.compile(r"\b(customer|client|patient|guest|user.?facing)\b", re.I)),
    ("leadership", re.compile(r"\b(lead|mentor|coach|manage|supervise)\b", re.I)),
    ("communication", re.compile(r"\b(present|communicat|document|negotiate|teach|tutor)\b", re.I)),
    ("quality", re.compile(r"\b(quality|test|qa|compliance|audit|reliab)\b", re.I)),
    ("automation", re.compile(r"\b(automat|script|ci/?cd|pipeline|orchestrat)\b", re.I)),
]


def infer_hiring_intent(
    *,
    title: str = "",
    job_family: str = "general",
    responsibilities: list[str] | None = None,
    required_skills: list[str] | None = None,
    soft_skills: list[str] | None = None,
    leadership_expectations: list[str] | None = None,
    learning_expectations: list[str] | None = None,
    communication_expectations: list[str] | None = None,
    customer_interaction: list[str] | None = None,
    jd_text: str = "",
    company_traits: list[str] | None = None,
    business_priorities: list[str] | None = None,
) -> dict[str, Any]:
    """Return a structured hiring-intent profile for THIS job."""
    family = _normalize_family(job_family)
    priorities = list(_FAMILY_PRIORITIES.get(family, _FAMILY_PRIORITIES["general"]))

    blob_parts = [
        title,
        jd_text,
        " ".join(str(x) for x in (responsibilities or [])),
        " ".join(str(x) for x in (required_skills or [])),
        " ".join(str(x) for x in (soft_skills or [])),
    ]
    blob = " ".join(blob_parts)

    must_signals: list[str] = []
    for label, pattern in _SIGNAL_PATTERNS:
        if pattern.search(blob):
            pretty = label.replace("_", " ")
            if pretty not in must_signals:
                must_signals.append(pretty)

    # Promote family priorities that also appear in JD text
    jd_boosted: list[str] = []
    for p in priorities:
        token = p.split()[0].lower()
        if token and token in blob.lower() and p not in jd_boosted:
            jd_boosted.append(p)
    narrative_priorities = list(dict.fromkeys(jd_boosted + priorities))[:6]

    person_archetype = _archetype(family, title, must_signals)
    problem_to_solve = _problem_to_solve(family, responsibilities or [], title)

    traits = []
    for src in (
        leadership_expectations or [],
        learning_expectations or [],
        communication_expectations or [],
        customer_interaction or [],
        company_traits or [],
    ):
        for t in src:
            text = str(t).strip()
            if text and text not in traits:
                traits.append(text)
            if len(traits) >= 8:
                break

    anti_signals = [
        "Generic keyword stuffing",
        "Duty lists without ownership or outcomes",
        "Claims without supporting evidence",
    ]

    interview_screening_focus = list(
        dict.fromkeys(
            [person_archetype, problem_to_solve]
            + narrative_priorities[:3]
            + must_signals[:3]
        )
    )[:6]

    intent = {
        "person_archetype": person_archetype,
        "problem_to_solve": problem_to_solve,
        "hiring_priorities": narrative_priorities,
        "must_signal_traits": must_signals[:8] or ["ownership", "problem solving"],
        "preferred_traits": traits[:8],
        "business_priorities": [str(b) for b in (business_priorities or []) if str(b).strip()][:6],
        "anti_signals": anti_signals,
        "job_family": family,
        "interview_screening_focus": interview_screening_focus,
        "interview_lens": (
            f"Would a busy recruiter hiring a {person_archetype} stop and "
            f"interview this person for: {problem_to_solve}?"
        ),
    }
    intent["narrative_themes"] = build_narrative_themes(
        hiring_intent=intent,
        top_interview_reasons=narrative_priorities[:3],
        matched_hard=list(required_skills or [])[:3],
        company_priorities=list(business_priorities or []),
        limit=4,
    )
    return intent


def _clean_theme_token(text: str) -> str:
    """Normalize theme/requirement crumbs from noisy JD word-splits."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = cleaned.strip(" \t\r\n,;:.-")
    cleaned = re.sub(
        r"^(required|responsibilities|requirements|preferred|qualifications)\s*:?\s*",
        "",
        cleaned,
        flags=re.I,
    ).strip(" \t\r\n,;:.-")
    return cleaned


_THEME_STOPWORDS = {
    "required",
    "responsibilities",
    "requirements",
    "preferred",
    "qualifications",
    "investigate",
    "issues",
    "issue",
    "communicate",
    "analyze",
    "verify",
    "and",
    "with",
    "the",
    "for",
}


def build_narrative_themes(
    *,
    hiring_intent: dict[str, Any] | None,
    top_interview_reasons: list[str] | None,
    matched_hard: list[str] | None = None,
    company_priorities: list[str] | None = None,
    limit: int = 3,
) -> list[str]:
    """Top narrative themes for the professional story (job-specific)."""
    themes: list[str] = []
    intent = hiring_intent or {}

    archetype = _clean_theme_token(str(intent.get("person_archetype") or ""))
    if archetype:
        themes.append(archetype)

    for reason in list(top_interview_reasons or []) + list(matched_hard or []):
        text = _clean_theme_token(str(reason))
        if not text or text.lower() in _THEME_STOPWORDS:
            continue
        # Prefer multi-word priorities / real competencies over JD crumbs
        if len(text.split()) == 1 and len(text) < 4:
            continue
        if text and text not in themes:
            themes.append(text)
        if len(themes) >= limit:
            return themes[:limit]

    for p in list(intent.get("hiring_priorities") or []) + list(company_priorities or []):
        text = _clean_theme_token(str(p))
        if not text or text.lower() in _THEME_STOPWORDS:
            continue
        if text and text not in themes:
            themes.append(text)
        if len(themes) >= limit:
            break
    return themes[:limit]


def _archetype(family: str, title: str, signals: list[str]) -> str:
    title = (title or "").strip()
    if title and len(title.split()) <= 6:
        base = title
    else:
        labels = {
            "backend": "Backend engineer who builds reliable systems",
            "frontend": "Frontend engineer focused on polished user experiences",
            "fullstack": "Full-stack builder who owns features end-to-end",
            "devops": "Cloud/DevOps engineer focused on reliable delivery",
            "data": "Data practitioner who turns information into decisions",
            "sales": "Sales professional who drives revenue conversations",
            "healthcare": "Healthcare professional centered on patient outcomes",
            "education": "Educator who communicates clearly and leads learning",
            "finance": "Finance professional who delivers accuracy and insight",
            "operations": "Operations owner who improves processes",
            "customer_support": "Customer-facing problem solver",
            "design": "Designer who balances craft with collaboration",
            "legal": "Legal professional with sharp judgment and writing",
            "hr": "People partner who builds trust and process",
            "marketing": "Marketer who connects audience insight to campaigns",
        }
        base = labels.get(family, "Professional who delivers measurable ownership")
    if "learning agility" in signals and "startup" not in base.lower():
        return f"{base} — rapid learner"
    return base


def _problem_to_solve(family: str, responsibilities: list[str], title: str) -> str:
    if responsibilities:
        first = str(responsibilities[0]).strip()
        if 12 <= len(first) <= 140:
            return first.rstrip(".")
    defaults = {
        "backend": "designing and operating scalable backend services",
        "frontend": "shipping polished, accessible user interfaces",
        "sales": "closing revenue and managing customer relationships",
        "healthcare": "delivering safe, documented patient care",
        "education": "teaching and supporting learner success",
        "devops": "keeping cloud systems reliable and automated",
        "data": "turning data into trustworthy insight",
    }
    return defaults.get(family, f"succeeding in the {title or 'target'} role")


def classify_requirement_support_tier(support: str) -> str:
    """Map amplifier support labels to the user-facing 4-tier model."""
    low = (support or "").lower()
    if "explicit" in low:
        return "Explicit Evidence"
    if "strong" in low:
        return "Strong Supporting Evidence"
    if "weak" in low:
        return "Transferable Evidence"
    return "No Evidence"
