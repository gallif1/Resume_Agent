"""Structured professional summary builder — human, role-specific, evidence-based.

Answers: Why is this candidate a good fit for THIS role?
Never emits AI filler ("Professional with Knowledge...", "Passionate about...").
"""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.linguistic_integrity import (
    detect_broken_patterns,
    has_duplicate_sentence,
    has_repeated_ngram,
    validate_claim_linguistics,
)
from intelligent_tailoring.scope_validator import extract_tech_mentions, has_unsupported_impact

_PROHIBITED_PHRASES = (
    "candidate for",
    "knowledge experience",
    "strong understanding experience",
    "professional with experience",
    "professional with knowledge",
    "professional with hands-on",
    "results-driven",
    "proven track record",
    "highly motivated",
    "passionate about",
    "strong understanding",
    "experienced in building",  # ok sometimes — blocked only as lead-in via regex
    "as an ai",
    "confidence",
    "applies these skills across",
    "delivery-focused projects",
)

_BANNED_LEAD_INS = (
    r"^professional with\b",
    r"^experienced professional\b",
    r"^results-driven\b",
    r"^highly motivated\b",
    r"^passionate\b",
    r"^dedicated professional\b",
    r"^seasoned\b",
)


def build_summary_plan(
    *,
    strategy: dict[str, Any],
    resume_facts: dict[str, Any],
    resume_text: str,
    output_language: str = "en",
    maximum_words: int = 58,
) -> dict[str, Any]:
    title = str(
        strategy.get("honest_title")
        or strategy.get("primary_role")
        or strategy.get("target_title")
        or strategy.get("job_family")
        or ""
    ).strip()
    if title.lower() in {"general", "this role", "the target role"}:
        title = ""

    emphasize = [
        str(s).strip()
        for s in (
            strategy.get("skills_to_emphasize")
            or strategy.get("propagate_terms")
            or strategy.get("must_highlight_in_summary")
            or []
        )
        if str(s).strip()
    ]
    must_highlight = [
        str(s).strip()
        for s in (strategy.get("must_highlight_in_summary") or [])
        if str(s).strip()
    ]
    source_l = (resume_text or "").lower()

    def _skill_like(token: str) -> bool:
        t = token.strip()
        if not t or len(t) < 2:
            return False
        low = t.lower()
        if low in {
            "investigate", "issues", "issue", "debug", "support", "build",
            "deploy", "write", "manage", "improve", "optimize", "ensure",
            "experience", "knowledge", "understanding", "strong", "good",
        }:
            return False
        return low in source_l

    evidenced = []
    for s in must_highlight + emphasize:
        if _skill_like(s) and s not in evidenced:
            evidenced.append(s)
        if len(evidenced) >= 5:
            break
    if not evidenced:
        skills = resume_facts.get("display_skills") or resume_facts.get("skills") or []
        if isinstance(skills, dict):
            for key in ("frameworks", "languages", "cloud", "other", "tools"):
                evidenced.extend(str(x) for x in (skills.get(key) or [])[:2])
        else:
            for s in skills:
                atom = str(s).split(":")[-1].strip()
                for part in atom.split(","):
                    p = part.strip()
                    if p and _skill_like(p) and p not in evidenced:
                        evidenced.append(p)
        evidenced = evidenced[:5]

    # Strongest evidence bullets — prefer achievements / design decisions
    strongest_bits: list[str] = []
    for proj in resume_facts.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        for b in proj.get("bullets") or []:
            text = str(b).strip()
            if text and text not in strongest_bits:
                strongest_bits.append(text)
            if len(strongest_bits) >= 2:
                break
        if len(strongest_bits) >= 2:
            break
    if len(strongest_bits) < 2:
        for role in resume_facts.get("experience_roles") or resume_facts.get("experience") or []:
            if not isinstance(role, dict):
                continue
            for b in role.get("bullets") or []:
                text = str(b).strip()
                if text and text not in strongest_bits:
                    strongest_bits.append(text)
                if len(strongest_bits) >= 2:
                    break
            if len(strongest_bits) >= 2:
                break

    summary_focus = str(strategy.get("summary_focus") or "").strip()
    value_prop = str(
        strategy.get("candidate_value_proposition")
        or strategy.get("target_positioning")
        or ""
    ).strip()

    return {
        "target_role": title,
        "candidate_positioning": title,
        "top_supported_competencies": evidenced,
        "strongest_evidence": (strongest_bits[0] if strongest_bits else "")[:180],
        "secondary_evidence": (strongest_bits[1] if len(strongest_bits) > 1 else "")[:160],
        "summary_focus": summary_focus,
        "value_proposition": value_prop,
        "seniority": str(strategy.get("seniority") or ""),
        "job_family": str(strategy.get("job_family") or strategy.get("target_job_family") or ""),
        "prohibited_claims": list(_PROHIBITED_PHRASES),
        "output_language": output_language if output_language in ("en", "he") else "en",
        "maximum_words": maximum_words,
    }


def _safe_role_label(role: str, resume_text: str) -> str:
    """Keep a role label only when its distinctive tokens appear in the resume."""
    role = (role or "").strip()
    if not role:
        return ""
    source = (resume_text or "").lower()
    tokens = [
        t
        for t in re.findall(r"[A-Za-z]{3,}", role)
        if t.lower()
        not in {
            "engineer", "developer", "specialist", "manager", "analyst",
            "coordinator", "professional", "senior", "junior", "lead",
            "registered", "account", "executive",
        }
    ]
    if tokens and not any(t.lower() in source for t in tokens):
        # Soften to a generic but natural label from family-like words
        for generic in ("engineer", "developer", "analyst", "nurse", "teacher", "manager"):
            if generic in role.lower() and generic in source:
                # Keep full role if base noun is evidenced
                return role
        return ""
    return role


def _natural_role_phrase(role: str, family: str) -> str:
    role = (role or "").strip()
    if role:
        # Prefer lowercase profession noun after article logic handled by caller
        return role
    mapping = {
        "backend": "Backend developer",
        "frontend": "Frontend developer",
        "devops": "DevOps engineer",
        "qa": "QA engineer",
        "support": "Support specialist",
        "data": "Data professional",
        "sales": "Sales professional",
        "finance": "Finance professional",
        "healthcare": "Healthcare professional",
        "education": "Educator",
        "hospitality": "Hospitality professional",
        "operations": "Operations professional",
        "marketing": "Marketing professional",
        "customer_service": "Customer service professional",
    }
    return mapping.get((family or "").lower(), "Professional")


def _lead_sentence(role_phrase: str, comps: list[str], family: str) -> str:
    """Natural opening that never uses banned 'Professional with Knowledge...' forms."""
    joined = _join(comps[:4]) if comps else ""
    family = (family or "").lower()
    role = (role_phrase or "Contributor").strip()
    if role.lower() == "professional":
        role = "Contributor"

    if not joined:
        return f"{role} with a track record of practical delivery across completed roles and projects."

    if family in {"backend", "frontend", "devops", "qa", "support", "data"}:
        return (
            f"{role} with hands-on experience building scalable services and applications "
            f"using {joined}."
        )
    if family in {"sales", "marketing"}:
        return (
            f"{role} who drives measurable outcomes through {joined} "
            f"and clear stakeholder communication."
        )
    if family in {"healthcare", "education", "hospitality", "customer_service"}:
        return (
            f"{role} focused on people-centered outcomes, with demonstrated strength in {joined}."
        )
    if family in {"finance", "operations", "legal", "hr"}:
        return (
            f"{role} delivering accurate, process-aware work involving {joined}."
        )
    return f"{role} with hands-on experience in {joined}."


def _compose_english(plan: dict[str, Any], *, resume_text: str = "") -> str:
    family = str(plan.get("job_family") or "")
    role = _safe_role_label(str(plan.get("target_role") or ""), resume_text)
    role_phrase = _natural_role_phrase(role, family)
    comps = [
        str(c).strip()
        for c in (plan.get("top_supported_competencies") or [])
        if str(c).strip()
    ]
    evidence = str(plan.get("strongest_evidence") or "").strip().rstrip(".")
    secondary = str(plan.get("secondary_evidence") or "").strip().rstrip(".")

    # Sentence 1 — specialization + core strengths (why fit)
    lead = _lead_sentence(role_phrase, comps, family)

    sentences = [lead]

    # Sentence 2 — concrete evidence (business/technical value)
    if evidence and evidence.lower() not in lead.lower():
        if evidence[0].islower():
            evidence = evidence[0].upper() + evidence[1:]
        sentences.append(evidence + ".")
    elif secondary:
        if secondary[0].islower():
            secondary = secondary[0].upper() + secondary[1:]
        sentences.append(secondary + ".")

    # Sentence 3 — learning / breadth without filler clichés
    if len(sentences) < 3 and comps:
        extras = [c for c in comps if c.lower() not in lead.lower()][:3]
        if extras and secondary and secondary.lower() not in " ".join(sentences).lower():
            bit = secondary[0].upper() + secondary[1:] if secondary else ""
            if bit:
                sentences.append(bit + ".")
        elif extras:
            sentences.append(
                f"Comfortable working across {_join(extras)} when the work requires it."
            )

    text = " ".join(sentences)
    text = _cleanup_summary_text(text)
    # Final ban on banned lead-ins
    for pat in _BANNED_LEAD_INS:
        if re.search(pat, text, re.I):
            # Rebuild without the banned lead
            text = re.sub(pat, role_phrase if role_phrase else "Contributor", text, count=1, flags=re.I)
            text = _cleanup_summary_text(text)
            break
    return _trim_words(text, int(plan.get("maximum_words") or 58))


def _compose_hebrew(plan: dict[str, Any]) -> str:
    role = str(plan.get("target_role") or "").strip()
    comps = [str(c).strip() for c in (plan.get("top_supported_competencies") or []) if str(c).strip()]
    if role and comps:
        return _trim_words(
            f"{role} עם ניסיון מעשי ב{_join(comps)}. "
            f"מתמקד ביישום הכישורים האלה בפרויקטים ובתפקידים רלוונטיים.",
            int(plan.get("maximum_words") or 58),
        )
    if comps:
        return _trim_words(
            f"בעל/ת ניסיון מעשי ב{_join(comps)}, עם התאמה לתפקיד היעד.",
            int(plan.get("maximum_words") or 58),
        )
    return "בעל/ת ניסיון מעשי בפרויקטים ובתפקידים רלוונטיים."


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _cleanup_summary_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r",\s*,", ", ", cleaned)
    cleaned = re.sub(r",\s*\.", ".", cleaned)
    cleaned = re.sub(r"\band\s*\.", ".", cleaned)
    cleaned = re.sub(r"\bin\s*\.", ".", cleaned)
    cleaned = re.sub(r"\s+\.", ".", cleaned)
    cleaned = re.sub(r"^(contributor|professional)\s+with\s+with\b", r"\1 with", cleaned, flags=re.I)
    return cleaned.strip()


def _trim_words(text: str, maximum: int) -> str:
    words = _cleanup_summary_text(text).split()
    if len(words) <= maximum:
        return _cleanup_summary_text(text)
    trimmed = " ".join(words[:maximum]).rstrip(",;")
    if not trimmed.endswith("."):
        trimmed += "."
    return _cleanup_summary_text(trimmed)


def summary_passes_checks(summary: str, *, resume_text: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    text = (summary or "").strip()
    if not text:
        return False, ["empty_summary"]
    low = text.lower()
    for phrase in _PROHIBITED_PHRASES:
        if phrase in low:
            # Allow "experienced in" mid-sentence in natural prose, but not lead-ins handled separately
            if phrase == "experienced in building" and not low.startswith("experienced"):
                continue
            errors.append(f"prohibited:{phrase}")
    for pat in _BANNED_LEAD_INS:
        if re.search(pat, text, re.I):
            errors.append(f"banned_lead_in:{pat}")
    if has_duplicate_sentence(text):
        errors.append("duplicate_sentence")
    if has_repeated_ngram(text, n=3):
        errors.append("repeated_ngram")
    errors.extend(detect_broken_patterns(text))
    words = text.split()
    if len(words) < 18:
        errors.append("summary_too_short")
    if len(words) > 100:
        errors.append("summary_too_long")
    novel = extract_tech_mentions(text) - extract_tech_mentions(resume_text)
    still = {t for t in novel if t not in resume_text.lower()}
    if still:
        errors.append(f"unsupported_entities:{','.join(sorted(still)[:5])}")
    if has_unsupported_impact(text, resume_text):
        errors.append("unsupported_impact")
    ling = validate_claim_linguistics(text, allow_summary_style=True)
    if not ling["passed"]:
        errors.extend(ling["detected_patterns"])
    errors = list(dict.fromkeys(errors))
    return len(errors) == 0, errors


def build_professional_summary(
    *,
    strategy: dict[str, Any],
    resume_facts: dict[str, Any],
    resume_text: str,
    output_language: str = "en",
    existing_summary: str = "",
) -> dict[str, Any]:
    """Return a validated summary, preferring a clean existing one when possible."""
    existing = (existing_summary or "").strip()
    if existing:
        ok, errs = summary_passes_checks(existing, resume_text=resume_text)
        if ok:
            return {
                "summary": existing,
                "repair_method": "accepted_existing",
                "plan": {},
                "errors": [],
            }

    plan = build_summary_plan(
        strategy=strategy,
        resume_facts=resume_facts,
        resume_text=resume_text,
        output_language=output_language,
    )
    if plan.get("output_language") == "he":
        draft = _compose_hebrew(plan)
    else:
        draft = _compose_english(plan, resume_text=resume_text)

    ok, errs = summary_passes_checks(draft, resume_text=resume_text)
    if ok:
        return {
            "summary": draft,
            "repair_method": "structured_plan",
            "plan": plan,
            "errors": [],
        }

    # Natural minimal fallback — never "Professional with Knowledge..."
    comps = plan.get("top_supported_competencies") or []
    role = _natural_role_phrase(
        _safe_role_label(str(plan.get("target_role") or ""), resume_text),
        str(plan.get("job_family") or ""),
    )
    if comps:
        # Avoid banned "Professional with..." lead-ins
        if role.lower() == "professional":
            role = "Contributor"
        evidence = str(plan.get("strongest_evidence") or "").strip().rstrip(".")
        minimal = (
            f"{role} focused on {_join(list(comps)[:4])}. "
            f"Practical delivery grounded in completed work"
            + (f", including {evidence.lower()}." if evidence else ".")
        )
        if not minimal.endswith("."):
            minimal += "."
        ok2, errs2 = summary_passes_checks(minimal, resume_text=resume_text)
        if ok2:
            return {
                "summary": minimal,
                "repair_method": "minimal_fallback",
                "plan": plan,
                "errors": errs,
            }
        # Guaranteed non-empty safe summary for export gates
        safe = (
            f"{role} focused on {_join(list(comps)[:4])}. "
            f"Brings practical strengths drawn from completed roles and projects."
        )
        return {
            "summary": safe,
            "repair_method": "safe_fallback",
            "plan": plan,
            "errors": errs2,
        }

    evidence = str(plan.get("strongest_evidence") or "").strip()
    if evidence:
        if evidence[0].islower():
            evidence = evidence[0].upper() + evidence[1:]
        if not evidence.endswith("."):
            evidence += "."
        safe = (
            f"{role if role.lower() != 'professional' else 'Contributor'} "
            f"with practical delivery experience. {evidence}"
        )
        return {
            "summary": _trim_words(safe, 75),
            "repair_method": "evidence_fallback",
            "plan": plan,
            "errors": errs,
        }

    return {
        "summary": (
            "Contributor with practical experience drawn from completed roles and projects."
        ),
        "repair_method": "generic_safe_fallback",
        "plan": plan,
        "errors": errs,
    }
