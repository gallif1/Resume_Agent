"""Structured professional summary builder — no keyword soup.

Builds one cohesive paragraph from a SummaryPlan. Never concatenates
raw requirement fragments or confidence labels.
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
    "results-driven",
    "proven track record",
    "as an ai",
    "confidence",
)


def build_summary_plan(
    *,
    strategy: dict[str, Any],
    resume_facts: dict[str, Any],
    resume_text: str,
    output_language: str = "en",
    maximum_words: int = 70,
) -> dict[str, Any]:
    title = str(
        strategy.get("honest_title")
        or strategy.get("primary_role")
        or strategy.get("target_title")
        or ""
    ).strip()
    emphasize = [
        str(s).strip()
        for s in (strategy.get("skills_to_emphasize") or [])
        if str(s).strip()
    ]
    # Keep only skill-like tokens evidenced in the source resume
    source_l = (resume_text or "").lower()

    def _skill_like(token: str) -> bool:
        t = token.strip()
        if not t or len(t) < 2:
            return False
        low = t.lower()
        # Reject verb/requirement fragments that are not skill names
        if low in {
            "investigate", "issues", "issue", "debug", "support", "build",
            "deploy", "write", "manage", "improve", "optimize", "ensure",
            "experience", "knowledge", "understanding", "strong", "good",
            "backend", "frontend", "fullstack", "devops", "sql", "api",
            "cloud", "data", "software", "engineering",
        }:
            return False
        if " " in t and not any(c.isupper() for c in t[1:]):
            # Multi-word lowercase phrases from JD are usually not skill atoms
            if not any(k in low for k in ("api", "ci/cd", "machine learning", "react native")):
                return False
        return low in source_l

    evidenced = [s for s in emphasize if _skill_like(s)][:5]
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
                    if p and _skill_like(p):
                        evidenced.append(p)
        evidenced = evidenced[:5]

    # Strongest evidence: first project or role bullet
    strongest = ""
    for proj in resume_facts.get("projects") or []:
        if isinstance(proj, dict):
            bullets = proj.get("bullets") or []
            if bullets:
                strongest = str(bullets[0]).strip()
                break
    if not strongest:
        for role in resume_facts.get("experience_roles") or resume_facts.get("experience") or []:
            if isinstance(role, dict) and (role.get("bullets") or []):
                strongest = str(role["bullets"][0]).strip()
                break

    return {
        "target_role": title,
        "candidate_positioning": title,
        "top_supported_competencies": evidenced,
        "strongest_evidence": strongest[:180],
        "seniority": str(strategy.get("seniority") or ""),
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
    # Drop the label when it invents a specialty absent from the resume
    tokens = [
        t
        for t in re.findall(r"[A-Za-z]{3,}", role)
        if t.lower()
        not in {
            "engineer", "developer", "specialist", "manager", "analyst",
            "coordinator", "professional", "senior", "junior", "lead",
        }
    ]
    if tokens and not any(t.lower() in source for t in tokens):
        return ""
    return role


def _compose_english(plan: dict[str, Any], *, resume_text: str = "") -> str:
    role = _safe_role_label(str(plan.get("target_role") or ""), resume_text)
    comps = [str(c).strip() for c in (plan.get("top_supported_competencies") or []) if str(c).strip()]
    evidence = str(plan.get("strongest_evidence") or "").strip()
    # Drop trailing period from evidence for embedding
    evidence = evidence.rstrip(".")

    if role and comps:
        lead = f"{role} with hands-on experience in {_join(comps)}."
    elif comps:
        lead = f"Professional with hands-on experience in {_join(comps)}."
    elif role:
        lead = f"{role} with practical experience drawn from completed projects and roles."
    else:
        lead = "Professional with practical experience drawn from completed projects and roles."

    sentences = [lead]
    if evidence and evidence.lower() not in lead.lower():
        # Second sentence from strongest evidence — keep factual
        if evidence[0].islower():
            evidence = evidence[0].upper() + evidence[1:]
        if not evidence.endswith("."):
            evidence += "."
        sentences.append(evidence)
    if comps and len(sentences) < 3:
        sentences.append(
            "Applies these skills across delivery-focused projects and team workflows."
        )
    text = " ".join(sentences)
    return _trim_words(text, int(plan.get("maximum_words") or 70))


def _compose_hebrew(plan: dict[str, Any]) -> str:
    role = str(plan.get("target_role") or "").strip()
    comps = [str(c).strip() for c in (plan.get("top_supported_competencies") or []) if str(c).strip()]
    if role and comps:
        return _trim_words(
            f"{role} עם ניסיון מעשי ב{_join(comps)}. "
            f"מתמקד ביישום הכישורים האלה בפרויקטים ובתפקידים רלוונטיים.",
            int(plan.get("maximum_words") or 70),
        )
    if comps:
        return _trim_words(
            f"איש מקצוע עם ניסיון מעשי ב{_join(comps)}.",
            int(plan.get("maximum_words") or 70),
        )
    return "איש מקצוע עם ניסיון מעשי בפרויקטים ובתפקידים רלוונטיים."


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _role_context(role: str) -> str:
    low = role.lower()
    if "devops" in low or "sre" in low:
        return "infrastructure and delivery"
    if "front" in low:
        return "product and interface"
    if "full" in low:
        return "full-stack delivery"
    if "data" in low:
        return "data and analytics"
    if "support" in low:
        return "support and operations"
    return "professional"


def _cleanup_summary_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r",\s*,", ", ", cleaned)
    cleaned = re.sub(r",\s*\.", ".", cleaned)
    cleaned = re.sub(r"\band\s*\.", ".", cleaned)
    cleaned = re.sub(r"\bin\s*\.", ".", cleaned)
    cleaned = re.sub(r"\s+\.", ".", cleaned)
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
            errors.append(f"prohibited:{phrase}")
    if has_duplicate_sentence(text):
        errors.append("duplicate_sentence")
    if has_repeated_ngram(text, n=3):
        errors.append("repeated_ngram")
    errors.extend(detect_broken_patterns(text))
    words = text.split()
    if len(words) < 12:
        errors.append("summary_too_short")
    if len(words) > 100:
        errors.append("summary_too_long")
    # Unsupported entities vs source
    novel = extract_tech_mentions(text) - extract_tech_mentions(resume_text)
    # Allow only if substring of source
    still = {t for t in novel if t not in resume_text.lower()}
    if still:
        errors.append(f"unsupported_entities:{','.join(sorted(still)[:5])}")
    if has_unsupported_impact(text, resume_text):
        errors.append("unsupported_impact")
    ling = validate_claim_linguistics(text, allow_summary_style=True)
    if not ling["passed"]:
        errors.extend(ling["detected_patterns"])
    # Deduplicate
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

    # Minimal fallback — short evidenced competency list only
    comps = plan.get("top_supported_competencies") or []
    if comps:
        minimal = f"Professional with hands-on experience in {_join(list(comps)[:4])}."
        ok2, errs2 = summary_passes_checks(minimal, resume_text=resume_text)
        if ok2:
            return {
                "summary": minimal,
                "repair_method": "minimal_fallback",
                "plan": plan,
                "errors": errs,
            }
        return {
            "summary": minimal if "prohibited" not in ",".join(errs2) else "",
            "repair_method": "minimal_fallback",
            "plan": plan,
            "errors": errs2,
        }

    return {
        "summary": "",
        "repair_method": "failed",
        "plan": plan,
        "errors": errs,
    }
