"""Deterministic job-description parsing (no LLM).

Replaces the former Agent-1 / job-requirement LLM extraction with code that:
1. Reuses a stored JobProfile when present
2. Heuristically extracts responsibilities, seniority, and skill terms from JD text
3. Matches ontology + taxonomy skill phrases against the JD

Output shape matches ``validate_requirements`` so JobIntelligenceAgent and
downstream evidence mapping keep working unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from ai_client import truncate_text
from config import OPENAI_JOB_MAX_CHARS
from intelligent_tailoring.ontology import SkillOntology, get_ontology
from intelligent_tailoring.skill_taxonomy import (
    SOFTWARE_TAXONOMY,
    UNIVERSAL_TAXONOMY,
    display_skill_name,
    normalize_skill_name,
    should_drop_skill_atom,
)
from intelligent_tailoring.stages.job_requirement_extraction import validate_requirements
from job_analyzer import parse_stored_job_profile
from match_tailor_service import build_job_payload

_BULLET_RE = re.compile(
    r"^\s*(?:[-*•●▪◦]|\d+[.)]|[a-z][.)])\s+(.+)$",
    re.I | re.M,
)
_SECTION_HEADERS = {
    "required": re.compile(
        r"\b(requirements?|required|must[- ]have|qualifications?|what you.?ll need|"
        r"you have|you bring|minimum qualifications)\b",
        re.I,
    ),
    "preferred": re.compile(
        r"\b(preferred|nice[- ]to[- ]have|bonus|plus|desired|optional)\b",
        re.I,
    ),
    "responsibilities": re.compile(
        r"\b(responsibilities|what you.?ll do|the role|about the role|"
        r"day[- ]to[- ]day|your mission|key duties)\b",
        re.I,
    ),
}
_SENIORITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("principal", re.compile(r"\b(principal|staff|distinguished)\b", re.I)),
    ("lead", re.compile(r"\b(lead|head of|manager)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?)\b", re.I)),
    ("mid", re.compile(r"\b(mid[- ]?level|intermediate|3\+|3 years|three years)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate|intern)\b", re.I)),
]
_SOFT_SKILL_CUES = (
    "communication",
    "collaborate",
    "teamwork",
    "leadership",
    "mentoring",
    "problem solving",
    "ownership",
    "stakeholder",
    "presentation",
    "customer service",
    "adaptability",
)


def _dedupe(items: list[str], *, max_items: int = 40) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = re.sub(r"\s+", " ", str(item or "").strip())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _detect_language(text: str) -> str:
    hebrew = len(re.findall(r"[\u0590-\u05FF]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    if hebrew > latin * 0.3 and hebrew >= 20:
        return "he"
    return "en"


def _detect_seniority(title: str, jd_text: str) -> str:
    blob = f"{title}\n{jd_text}"
    for label, pattern in _SENIORITY_PATTERNS:
        if pattern.search(blob):
            return label
    return ""


def _split_sections(jd_text: str) -> dict[str, str]:
    """Rough section split by common JD headers."""
    lines = (jd_text or "").splitlines()
    buckets: dict[str, list[str]] = {
        "required": [],
        "preferred": [],
        "responsibilities": [],
        "other": [],
    }
    current = "other"
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        header_hit = None
        for name, pattern in _SECTION_HEADERS.items():
            if pattern.search(stripped) and len(stripped) < 80:
                header_hit = name
                break
        if header_hit:
            current = header_hit
            continue
        buckets[current].append(stripped)
    return {k: "\n".join(v) for k, v in buckets.items()}


def _extract_bullets(text: str) -> list[str]:
    bullets = [m.group(1).strip() for m in _BULLET_RE.finditer(text or "")]
    if bullets:
        return _dedupe(bullets, max_items=25)
    # Fallback: sentence-like lines that look like duties
    lines = []
    for line in (text or "").splitlines():
        s = line.strip(" \t-•*")
        if 28 <= len(s) <= 220 and not s.endswith(":"):
            lines.append(s)
    return _dedupe(lines, max_items=20)


def _collect_skill_phrases(ontology: SkillOntology) -> list[str]:
    phrases: list[str] = []
    for category, members in {**SOFTWARE_TAXONOMY, **UNIVERSAL_TAXONOMY}.items():
        del category  # unused — members only
        phrases.extend(members)
    for rel in ontology.relationships:
        phrases.extend(rel.sources)
        phrases.append(rel.target)
        phrases.extend(rel.also_implies)
    # Longer phrases first for greedy matching
    uniq = _dedupe([p for p in phrases if len(normalize_skill_name(p)) >= 2], max_items=500)
    return sorted(uniq, key=lambda p: (-len(p), p.lower()))


def _skills_in_text(text: str, phrases: list[str]) -> list[str]:
    low = f" {(text or '').lower()} "
    hits: list[str] = []
    for phrase in phrases:
        key = normalize_skill_name(phrase)
        if should_drop_skill_atom(key):
            continue
        # Word-boundary-ish match
        needle = f" {key} "
        alt = f" {key.replace('.', '')} "
        if needle in low or alt in low or f" {key}," in low or f"({key})" in low:
            hits.append(display_skill_name(phrase))
            continue
        # Multiword without requiring surrounding spaces for dotted tokens
        if len(key) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", low):
            hits.append(display_skill_name(phrase))
    return _dedupe(hits, max_items=40)


def _soft_skills_in_text(text: str) -> list[str]:
    low = (text or "").lower()
    return _dedupe([cue for cue in _SOFT_SKILL_CUES if cue in low], max_items=12)


def _from_stored_profile(stored: Any) -> dict[str, Any] | None:
    if stored is None:
        return None
    data = stored.to_dict() if hasattr(stored, "to_dict") else dict(stored or {})
    required = list(data.get("required_skills") or [])
    preferred = list(data.get("preferred_skills") or [])
    tools = list(data.get("technologies") or [])
    responsibilities = list(data.get("responsibilities") or [])
    education = list(data.get("education") or data.get("certifications") or [])
    if not any((required, preferred, tools, responsibilities)):
        return None
    hard = list(data.get("mandatory_requirements") or required)
    return validate_requirements(
        {
            "required_skills": required,
            "preferred_skills": preferred,
            "responsibilities": responsibilities,
            "tools_technologies": tools,
            "industry_terminology": [],
            "seniority_level": str(data.get("seniority") or ""),
            "soft_skills": [],
            "education_certifications": education,
            "ats_keywords": _dedupe(required + tools + preferred, max_items=40),
            "hard_requirements": hard,
            "soft_requirements": preferred,
            "language": "en",
        }
    )


def extract_job_requirements_deterministic(
    job: dict[str, Any],
    *,
    jd_snapshot: str | None = None,
    ontology: SkillOntology | None = None,
) -> dict[str, Any]:
    """Parse a job description into the standard requirements schema (no LLM)."""
    ontology = ontology or get_ontology()
    stored = parse_stored_job_profile(job.get("job_profile"))
    jd_text = jd_snapshot or build_job_payload(job, stored)
    jd_text = truncate_text(jd_text, OPENAI_JOB_MAX_CHARS)
    title = str(job.get("title") or "")

    if len((jd_text or "").strip()) < 40:
        return {
            "required_skills": [],
            "preferred_skills": [],
            "responsibilities": [],
            "tools_technologies": [],
            "industry_terminology": [],
            "seniority_level": "",
            "soft_skills": [],
            "education_certifications": [],
            "ats_keywords": [],
            "hard_requirements": [],
            "soft_requirements": [],
            "language": "en",
            "sparse": True,
            "jd_text": jd_text,
            "extraction_method": "deterministic_sparse",
        }

    phrases = _collect_skill_phrases(ontology)
    sections = _split_sections(jd_text)
    stored_reqs = _from_stored_profile(stored)

    required_text = sections["required"] or jd_text
    preferred_text = sections["preferred"]
    resp_text = sections["responsibilities"] or jd_text

    required_skills = _skills_in_text(required_text, phrases)
    preferred_skills = [
        s for s in _skills_in_text(preferred_text, phrases) if s not in required_skills
    ]
    # Title + whole JD catch skills missed by section split
    all_skills = _skills_in_text(f"{title}\n{jd_text}", phrases)
    for skill in all_skills:
        if skill not in required_skills and skill not in preferred_skills:
            # Skills mentioned near "preferred" stay preferred; else required-ish
            if skill.lower() in preferred_text.lower() and skill.lower() not in required_text.lower():
                preferred_skills.append(skill)
            else:
                required_skills.append(skill)

    responsibilities = _extract_bullets(resp_text)
    if not responsibilities:
        responsibilities = _extract_bullets(jd_text)

    soft_skills = _soft_skills_in_text(jd_text)
    seniority = _detect_seniority(title, jd_text)
    language = _detect_language(jd_text)

    # Merge stored profile signals (never lose structured analysis already done)
    if stored_reqs:
        required_skills = _dedupe(
            list(stored_reqs.get("required_skills") or []) + required_skills
        )
        preferred_skills = _dedupe(
            list(stored_reqs.get("preferred_skills") or []) + preferred_skills
        )
        responsibilities = _dedupe(
            list(stored_reqs.get("responsibilities") or []) + responsibilities,
            max_items=25,
        )
        tools = _dedupe(
            list(stored_reqs.get("tools_technologies") or []) + required_skills
        )
        if stored_reqs.get("seniority_level") and not seniority:
            seniority = str(stored_reqs.get("seniority_level") or "")
    else:
        tools = list(required_skills)

    # Prefer concrete tools as tools_technologies; keep broader list as skills
    tools_technologies = [
        s for s in tools if not should_drop_skill_atom(s)
    ][:30]

    education: list[str] = []
    for cue in (
        "bachelor",
        "master",
        "phd",
        "degree",
        "b.sc",
        "m.sc",
        "certification",
        "license",
    ):
        if cue in jd_text.lower():
            # Capture a short surrounding phrase
            m = re.search(rf"([^.\\n]{{0,40}}{cue}[^.\\n]{{0,40}})", jd_text, re.I)
            if m:
                education.append(re.sub(r"\s+", " ", m.group(1)).strip())
    education = _dedupe(education, max_items=8)

    result = validate_requirements(
        {
            "required_skills": required_skills[:30],
            "preferred_skills": preferred_skills[:20],
            "responsibilities": responsibilities[:20],
            "tools_technologies": tools_technologies,
            "industry_terminology": [],
            "seniority_level": seniority,
            "soft_skills": soft_skills,
            "education_certifications": education,
            "ats_keywords": _dedupe(
                required_skills + preferred_skills + tools_technologies, max_items=40
            ),
            "hard_requirements": required_skills[:30],
            "soft_requirements": preferred_skills[:20],
            "language": language,
        }
    )
    result["sparse"] = False
    result["jd_text"] = jd_text
    result["extraction_method"] = "deterministic"
    return result


def run_deterministic_intelligence_bundle(
    *,
    job: dict[str, Any],
    resume_facts: dict[str, Any],
    ontology: SkillOntology | None = None,
    jd_snapshot: str | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """Code-only replacement for the former Agent-1 LLM intelligence bundle."""
    from intelligent_tailoring.stages.semantic_inference import (
        _dedupe_competencies,
        _from_ontology_hits,
    )

    ontology = ontology or get_ontology()
    requirements = extract_job_requirements_deterministic(
        job, jd_snapshot=jd_snapshot, ontology=ontology
    )
    resume_text = str(resume_facts.get("raw_text") or "")
    inferred = _dedupe_competencies(
        _from_ontology_hits(
            resume_text, requirements, ontology, language=language
        )
    )

    # Genuine gaps: hard requirements with no resume hit
    resume_l = resume_text.lower()
    genuine_gaps: list[str] = []
    safe_inferences = [i.statement for i in inferred[:12]]
    forbidden_claims: list[str] = []
    for req in requirements.get("hard_requirements") or []:
        tokens = [
            t
            for t in re.findall(r"[a-z0-9+#.]{3,}", str(req).lower())
            if t not in {"and", "the", "with", "for"}
        ]
        if tokens and not any(t in resume_l for t in tokens[:4]):
            genuine_gaps.append(str(req))
            forbidden_claims.append(str(req))

    return {
        "job_requirements": requirements,
        "inferred_competencies": inferred,
        "company_cues": {
            "industry": "Unknown",
            "company_stage": "Unknown",
            "culture_signals": [],
            "verified_facts_only": True,
        },
        "genuine_gaps": genuine_gaps[:20],
        "safe_inferences": safe_inferences,
        "forbidden_claims": forbidden_claims[:20],
        "requirement_priorities": list(
            (requirements.get("hard_requirements") or [])[:12]
        ),
        "primary_llm_calls": 0,
        "_from_cache": False,
        "extraction_method": "deterministic",
    }
