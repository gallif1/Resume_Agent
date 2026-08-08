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


_JD_JUNK_TOKENS = {
    "required",
    "responsibilities",
    "requirements",
    "preferred",
    "qualifications",
    "investigate",
    "issues",
    "issue",
    "debug",
    "support",
    "build",
    "deploy",
    "write",
    "manage",
    "improve",
    "optimize",
    "ensure",
    "experience",
    "knowledge",
    "understanding",
    "strong",
    "good",
    "communicate",
    "analyze",
    "verify",
    "troubleshoot",
}


def _clean_evidence_token(token: str) -> str:
    """Strip JD punctuation/labels so fragments like 'issues,' never enter prose."""
    text = re.sub(r"\s+", " ", (token or "").strip())
    text = text.strip(" \t\r\n,;:.-")
    # Drop leading JD section labels ("Required: foo")
    text = re.sub(
        r"^(required|responsibilities|requirements|preferred|qualifications)\s*:?\s*",
        "",
        text,
        flags=re.I,
    ).strip(" \t\r\n,;:.-")
    return text


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
        _clean_evidence_token(str(s))
        for s in (
            strategy.get("skills_to_emphasize")
            or strategy.get("propagate_terms")
            or strategy.get("must_highlight_in_summary")
            or []
        )
        if _clean_evidence_token(str(s))
    ]
    must_highlight = [
        _clean_evidence_token(str(s))
        for s in (strategy.get("must_highlight_in_summary") or [])
        if _clean_evidence_token(str(s))
    ]
    source_l = (resume_text or "").lower()

    def _skill_like(token: str) -> bool:
        t = _clean_evidence_token(token)
        if not t or len(t) < 2:
            return False
        # Reject single generic verbs / JD crumbs (must be a real competency phrase)
        if len(t.split()) == 1 and t.lower() in _JD_JUNK_TOKENS:
            return False
        if len(t) < 3:
            return False
        low = t.lower()
        # Prefer whole-token evidence, not accidental substring matches on "issues,"
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(low)}(?![a-z0-9])", source_l))

    evidenced = []
    for s in must_highlight + emphasize:
        cleaned = _clean_evidence_token(s)
        if _skill_like(cleaned) and cleaned not in evidenced:
            evidenced.append(cleaned)
        if len(evidenced) >= 5:
            break
    if not evidenced:
        skills = resume_facts.get("display_skills") or resume_facts.get("skills") or []
        if isinstance(skills, dict):
            for key in ("frameworks", "languages", "cloud", "other", "tools"):
                evidenced.extend(
                    _clean_evidence_token(str(x))
                    for x in (skills.get(key) or [])[:2]
                    if _clean_evidence_token(str(x))
                )
        else:
            for s in skills:
                atom = str(s).split(":")[-1].strip()
                for part in atom.split(","):
                    p = _clean_evidence_token(part)
                    if p and _skill_like(p) and p not in evidenced:
                        evidenced.append(p)
        evidenced = evidenced[:5]

    # Strongest evidence — prefer strategy-ranked evidence, then resume bullets
    strongest_bits: list[str] = []
    for bit in list(strategy.get("strongest_evidence") or []) + list(
        strategy.get("top_interview_reasons") or []
    ):
        text = _clean_evidence_token(str(bit))
        # Skip JD crumb tokens that are not real evidence sentences/phrases
        if not text or len(text.split()) < 2 and text.lower() in _JD_JUNK_TOKENS:
            continue
        if text and text not in strongest_bits and (
            text.lower() in source_l
            or any(
                len(t) > 3 and re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", source_l)
                for t in text.lower().split()
            )
        ):
            # Prefer full resume bullets over lone JD crumbs
            if len(text.split()) == 1 and text.lower() in _JD_JUNK_TOKENS:
                continue
            strongest_bits.append(text)
        if len(strongest_bits) >= 2:
            break
    if len(strongest_bits) < 2:
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
        strategy.get("professional_story")
        or strategy.get("candidate_value_proposition")
        or strategy.get("target_positioning")
        or ""
    ).strip()
    themes = [
        _clean_evidence_token(str(t))
        for t in (strategy.get("narrative_themes") or [])
        if _clean_evidence_token(str(t))
        and _clean_evidence_token(str(t)).lower() not in _JD_JUNK_TOKENS
    ][:4]

    return {
        "target_role": title,
        "candidate_positioning": title,
        "top_supported_competencies": evidenced,
        "strongest_evidence": (strongest_bits[0] if strongest_bits else "")[:180],
        "secondary_evidence": (strongest_bits[1] if len(strongest_bits) > 1 else "")[:160],
        "narrative_themes": themes,
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


_ROLE_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "frontend engineer",
            "frontend developer",
            "front-end engineer",
            "front-end developer",
            "front end engineer",
            "front end developer",
        }
    ),
    frozenset(
        {
            "backend engineer",
            "backend developer",
            "back-end engineer",
            "back-end developer",
            "back end engineer",
            "back end developer",
        }
    ),
    frozenset(
        {
            "software engineer",
            "software developer",
            "full stack engineer",
            "full-stack engineer",
            "fullstack engineer",
            "full stack developer",
            "full-stack developer",
        }
    ),
)


def _collapse_role_synonyms(text: str) -> str:
    """Prevent 'Frontend Engineer Frontend Developer' style concatenations."""
    out = re.sub(r"\s+", " ", (text or "").strip())
    for group in _ROLE_SYNONYM_GROUPS:
        # Prefer developer/engineer label that appears first as canonical
        preferred = None
        for label in (
            "Frontend developer",
            "Backend developer",
            "Software developer",
            "Frontend engineer",
            "Backend engineer",
            "Software engineer",
        ):
            if label.lower() in group:
                preferred = label
                break
        if not preferred:
            preferred = next(iter(group)).title()
        # Match two adjacent synonyms from the same group
        alts = "|".join(re.escape(x) for x in sorted(group, key=len, reverse=True))
        pattern = re.compile(rf"\b({alts})\s+({alts})\b", flags=re.I)
        out = pattern.sub(preferred, out)
    return out.strip()


def _natural_role_phrase(role: str, family: str) -> str:
    role = _collapse_role_synonyms(role or "").strip()
    # If multiple role tokens remain, keep the first noun phrase only
    role = re.split(r"\s*/\s*|\s+\|\s+", role)[0].strip()
    if role:
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
    # Competencies must not repeat the role title
    role_low = (role_phrase or "").strip().lower()
    filtered = [
        c
        for c in comps
        if c.strip().lower() not in role_low
        and c.strip().lower() not in {"frontend", "backend", "engineer", "developer"}
    ]
    joined = _join(filtered[:4]) if filtered else ""
    family = (family or "").lower()
    role = _collapse_role_synonyms(role_phrase or "Contributor").strip()
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


def _token_overlap_ratio(a: str, b: str) -> float:
    ta = {t for t in re.findall(r"[a-z0-9+#.]{3,}", (a or "").lower())}
    tb = {t for t in re.findall(r"[a-z0-9+#.]{3,}", (b or "").lower())}
    if not ta or not tb:
        na = re.sub(r"\s+", " ", (a or "").strip().lower())
        nb = re.sub(r"\s+", " ", (b or "").strip().lower())
        if na and nb and na == nb:
            return 1.0
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def _paraphrase_evidence_for_summary(evidence: str) -> str:
    """Rewrite a bullet-like evidence phrase into high-level summary prose."""
    text = re.sub(r"\s+", " ", (evidence or "").strip()).rstrip(".")
    if not text:
        return ""
    rewrites = (
        (r"^Implemented\b", "Experience includes"),
        (r"^Developed\b", "Background includes"),
        (r"^Built\b", "Work includes"),
        (r"^Created\b", "Work includes"),
        (r"^Designed\b", "Experience includes"),
        (r"^Led\b", "Experience includes leading"),
        (r"^Wrote\b", "Background includes"),
        (r"^Added\b", "Experience includes"),
        (r"^Maintained\b", "Background includes maintaining"),
        (r"^Tutored\b", "Experience includes tutoring"),
        (r"^Helped\b", "Background includes helping"),
        (r"^Coordinated\b", "Experience includes coordinating"),
    )
    for pat, repl in rewrites:
        if re.match(pat, text, flags=re.I):
            rest = re.sub(pat, "", text, count=1, flags=re.I).strip(" ,;")
            words = rest.split()
            if len(words) > 12:
                rest = " ".join(words[:12])
            if rest:
                return f"{repl} {rest}."
    words = text.split()
    clipped = " ".join(words[:12]).rstrip(",;")
    if clipped and clipped[0].isupper():
        clipped = clipped[0].lower() + clipped[1:]
    return f"Background includes {clipped}."


def _evidence_sentence(evidence: str, *, bullet_texts: list[str] | None = None) -> str:
    """Return a summary sentence that does not copy an Experience/Project bullet."""
    text = (evidence or "").strip().rstrip(".")
    if not text:
        return ""
    bullets = bullet_texts or []
    overlaps = any(_token_overlap_ratio(text, b) >= 0.80 for b in bullets)
    # Also treat long evidence phrases as bullet-like even without an explicit list
    if overlaps or len(text.split()) >= 12:
        return _paraphrase_evidence_for_summary(text)
    if text[0].islower():
        text = text[0].upper() + text[1:]
    return text + "."


def dedupe_summary_against_bullets(
    summary: str,
    bullet_texts: list[str],
    *,
    overlap_threshold: float = 0.80,
) -> str:
    """Rephrase any summary sentence that nearly matches an Experience/Project bullet."""
    text = _cleanup_summary_text(summary or "")
    if not text or not bullet_texts:
        return text
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    out: list[str] = []
    for sent in sentences:
        if len(sent.split()) >= 6 and any(
            _token_overlap_ratio(sent, b) >= overlap_threshold for b in bullet_texts
        ):
            rewritten = _paraphrase_evidence_for_summary(sent)
            # Avoid regenerating something still too close
            if rewritten and not any(
                _token_overlap_ratio(rewritten, b) >= overlap_threshold
                for b in bullet_texts
            ):
                out.append(rewritten)
            elif rewritten:
                # Strip to a shorter capability clause
                techs = re.findall(
                    r"\b(?:pytest|python|fastapi|react|aws|sql|typescript|nodejs|go)\b",
                    sent,
                    flags=re.I,
                )
                if techs:
                    out.append(
                        f"Brings practical strengths with {_join(list(dict.fromkeys(techs))[:3])}."
                    )
                # else drop the duplicate sentence entirely
            continue
        out.append(sent if sent.endswith((".", "!", "?")) else sent + ".")
    return _cleanup_summary_text(" ".join(out))


def _collect_plan_bullet_texts(resume_facts: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for role in resume_facts.get("experience_roles") or resume_facts.get("experience") or []:
        if not isinstance(role, dict):
            continue
        for b in role.get("bullets") or []:
            if str(b).strip():
                texts.append(str(b).strip())
    for proj in resume_facts.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        desc = str(proj.get("description") or "").strip()
        if desc:
            texts.append(desc)
        for b in proj.get("bullets") or []:
            if str(b).strip():
                texts.append(str(b).strip())
    return texts


def _compose_english(
    plan: dict[str, Any],
    *,
    resume_text: str = "",
    bullet_texts: list[str] | None = None,
) -> str:
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
    bullets = bullet_texts or []

    # Sentence 1 — specialization + core strengths (why fit)
    lead = _lead_sentence(role_phrase, comps, family)

    sentences = [lead]

    # Sentence 2 — concrete evidence at a high level (never a verbatim bullet)
    if evidence and evidence.lower() not in lead.lower():
        sent = _evidence_sentence(evidence, bullet_texts=bullets)
        if sent and sent.lower().rstrip(".") not in lead.lower():
            sentences.append(sent)
    elif secondary:
        sent = _evidence_sentence(secondary, bullet_texts=bullets)
        if sent and sent.lower().rstrip(".") not in lead.lower():
            sentences.append(sent)

    # Sentence 3 — learning / breadth without filler clichés
    if len(sentences) < 3 and comps:
        extras = [c for c in comps if c.lower() not in lead.lower()][:3]
        joined_sents = " ".join(sentences).lower()
        if extras and secondary and secondary.lower() not in joined_sents:
            bit = _evidence_sentence(secondary, bullet_texts=bullets)
            if bit and bit.lower() not in joined_sents:
                sentences.append(bit)
        elif extras:
            sentences.append(
                f"Comfortable working across {_join(extras)} when the work requires it."
            )

    text = " ".join(sentences)
    text = _cleanup_summary_text(text)
    text = dedupe_summary_against_bullets(text, bullets)
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
    cleaned = [
        _clean_evidence_token(str(i))
        for i in items
        if _clean_evidence_token(str(i))
        and _clean_evidence_token(str(i)).lower() not in _JD_JUNK_TOKENS
    ]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _cleanup_summary_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r",\s*,", ", ", cleaned)
    cleaned = re.sub(r",\s*\.", ".", cleaned)
    cleaned = re.sub(r"\band\s*\.", ".", cleaned)
    cleaned = re.sub(r"\bin\s*\.", ".", cleaned)
    cleaned = re.sub(r"\s+\.", ".", cleaned)
    cleaned = re.sub(r"\bon\s*\.", ".", cleaned)
    cleaned = re.sub(r"\busing\s*\.", ".", cleaned)
    cleaned = re.sub(r"^(contributor|professional)\s+with\s+with\b", r"\1 with", cleaned, flags=re.I)
    cleaned = _collapse_role_synonyms(cleaned)
    return cleaned.strip()


def _trim_words(text: str, maximum: int) -> str:
    words = _cleanup_summary_text(text).split()
    if len(words) <= maximum:
        return _cleanup_summary_text(text)
    trimmed = " ".join(words[:maximum]).rstrip(",;")
    if not trimmed.endswith("."):
        trimmed += "."
    return _cleanup_summary_text(trimmed)


def summary_passes_checks(
    summary: str,
    *,
    resume_text: str,
    bullet_texts: list[str] | None = None,
) -> tuple[bool, list[str]]:
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
    if re.search(
        r"\b(frontend engineer)\s+(frontend developer)\b"
        r"|\b(frontend developer)\s+(frontend engineer)\b"
        r"|\b(backend engineer)\s+(backend developer)\b"
        r"|\b(software engineer)\s+(software developer)\b",
        text,
        flags=re.I,
    ):
        errors.append("duplicate_title_phrase")
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
    # Verbatim / near-verbatim overlap with Experience or Project bullets
    for sent in re.split(r"(?<=[.!?])\s+", text):
        sent = sent.strip()
        if len(sent.split()) < 6:
            continue
        for bullet in bullet_texts or []:
            if _token_overlap_ratio(sent, bullet) >= 0.80:
                errors.append("summary_duplicates_bullet")
                break
        if "summary_duplicates_bullet" in errors:
            break
    errors = list(dict.fromkeys(errors))
    return len(errors) == 0, errors


def build_professional_summary(
    *,
    strategy: dict[str, Any],
    resume_facts: dict[str, Any],
    resume_text: str,
    output_language: str = "en",
    existing_summary: str = "",
    tailored_resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a validated summary, preferring a clean existing one when possible."""
    bullet_texts = _collect_plan_bullet_texts(resume_facts)
    if isinstance(tailored_resume, dict):
        from intelligent_tailoring.canonical_resume import collect_resume_bullet_texts

        for b in collect_resume_bullet_texts(tailored_resume):
            if b not in bullet_texts:
                bullet_texts.append(b)

    existing = _cleanup_summary_text(existing_summary or "")
    if existing:
        existing = dedupe_summary_against_bullets(existing, bullet_texts)
        ok, errs = summary_passes_checks(
            existing, resume_text=resume_text, bullet_texts=bullet_texts
        )
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
        draft = _compose_english(
            plan, resume_text=resume_text, bullet_texts=bullet_texts
        )
    draft = dedupe_summary_against_bullets(draft, bullet_texts)

    ok, errs = summary_passes_checks(
        draft, resume_text=resume_text, bullet_texts=bullet_texts
    )
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
        evidence = _clean_evidence_token(
            str(plan.get("strongest_evidence") or "")
        ).rstrip(".")
        # Never splice a near-verbatim bullet into the fallback
        if evidence and any(
            _token_overlap_ratio(evidence, b) >= 0.80 for b in bullet_texts
        ):
            evidence = ""
        joined = _join(list(comps)[:4])
        if joined:
            minimal = (
                f"{role} focused on {joined}. "
                f"Practical delivery grounded in completed work"
                + (f" involving {evidence.lower()}." if evidence else ".")
            )
        else:
            minimal = (
                f"{role} with practical delivery experience across completed "
                f"roles and projects."
            )
        minimal = dedupe_summary_against_bullets(
            _cleanup_summary_text(minimal), bullet_texts
        )
        if not minimal.endswith("."):
            minimal += "."
        ok2, errs2 = summary_passes_checks(
            minimal, resume_text=resume_text, bullet_texts=bullet_texts
        )
        if ok2:
            return {
                "summary": minimal,
                "repair_method": "minimal_fallback",
                "plan": plan,
                "errors": errs,
            }
        # Guaranteed non-empty safe summary for export gates
        clean_comps = [
            _clean_evidence_token(c)
            for c in comps
            if _clean_evidence_token(c)
            and _clean_evidence_token(c).lower() not in _JD_JUNK_TOKENS
        ][:4]
        if clean_comps:
            safe = (
                f"{role} focused on {_join(clean_comps)}. "
                f"Brings practical strengths drawn from completed roles and projects."
            )
        else:
            safe = (
                f"{role} with practical delivery experience across completed "
                f"roles and projects."
            )
        return {
            "summary": dedupe_summary_against_bullets(
                _cleanup_summary_text(safe), bullet_texts
            ),
            "repair_method": "safe_fallback",
            "plan": plan,
            "errors": errs2,
        }

    evidence = str(plan.get("strongest_evidence") or "").strip()
    if evidence and not any(
        _token_overlap_ratio(evidence, b) >= 0.80 for b in bullet_texts
    ):
        sent = _evidence_sentence(evidence, bullet_texts=bullet_texts)
        safe = (
            f"{role if role.lower() != 'professional' else 'Contributor'} "
            f"with practical delivery experience. {sent}"
        )
        return {
            "summary": dedupe_summary_against_bullets(
                _trim_words(safe, 75), bullet_texts
            ),
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
