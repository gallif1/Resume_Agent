"""Strict scope and impact validators — prevent cross-entry tech leakage and invented impact.

Profession-agnostic rules:
- A technology may appear in a project/experience bullet only if a ResumeFact
  binds that technology to the SAME source_entry_id.
- General skills do NOT prove project-specific usage.
- Impact verbs (improved/enhanced/increased/…) require an explicit source metric
  or result; otherwise rewrite to factual verbs or reject.
"""

from __future__ import annotations

import re
from typing import Any

_IMPACT_VERBS = re.compile(
    r"\b("
    r"improv(?:e|ed|ing|es)|enhanc(?:e|ed|ing|es)|increas(?:e|ed|ing|es)|"
    r"reduc(?:e|ed|ing|es)|boost(?:ed|ing|s)?|optimiz(?:e|ed|ing|es)|"
    r"accelerat(?:e|ed|ing|es)|maximiz(?:e|ed|ing|es)|minimiz(?:e|ed|ing|es)|"
    r"ensur(?:e|ed|ing|es)|drove|driving|grew|growing|raised|raising|lowered|lowering|"
    r"שיפר|שיפור|הגדיל|הגדלה|הפחית|שיפור"
    r")\b",
    re.I,
)

_METRIC_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?\s*%|\d+\+?\s*(?:x|times|people|users|customers|"
    r"students|patients|clients|ms|seconds|minutes|hours|days|"
    r"\$[\d,]+)|[\d,]+\s*(?:%|percent))\b",
    re.I,
)

_SAFE_FACTUAL_REPLACEMENTS = {
    "improving": "supporting",
    "improved": "supported",
    "improve": "support",
    "enhancing": "supporting",
    "enhanced": "supported",
    "enhance": "support",
    "increasing": "supporting",
    "increased": "supported",
    "increase": "support",
    "reducing": "addressing",
    "reduced": "addressed",
    "reduce": "address",
    "boosting": "supporting",
    "boosted": "supported",
    "boost": "support",
    "optimizing": "implementing",
    "optimized": "implemented",
    "optimize": "implement",
    "ensuring": "supporting",
    "ensured": "supported",
    "ensure": "support",
}

# Common tech tokens used for leakage detection (not an allowlist of skills).
_TECH_TOKEN_RE = re.compile(
    r"\b("
    r"vue\.?js|vue|react(?:\s*native)?|angular|fastapi|django|flask|express|"
    r"node\.?js|nodejs|nestjs|spring|laravel|rails|postgresql|postgres|mysql|"
    r"mongodb|sqlite|redis|firebase|sqlalchemy|prisma|hibernate|"
    r"aws|azure|gcp|docker|kubernetes|k8s|terraform|jenkins|pytest|jest|"
    r"selenium|websocket|websockets|graphql|kafka|rabbitmq|nginx|"
    r"typescript|javascript|python|java\b|kotlin|swift|golang|rust|"
    r"salesforce|hubspot|quickbooks|sap|excel|tableau|power\s*bi|"
    r"figma|photoshop|illustrator|"
    r"ci/?cd|threadpoolexecutor|openai|llm|generative\s*ai"
    r")\b",
    re.I,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_tech_mentions(text: str) -> set[str]:
    return {_norm(m.group(0)) for m in _TECH_TOKEN_RE.finditer(text or "")}


def facts_for_entry(
    facts: list[dict[str, Any]] | list[Any],
    source_entry_id: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in facts or []:
        if isinstance(f, dict):
            if str(f.get("source_entry_id") or "") == source_entry_id:
                out.append(f)
        else:
            if getattr(f, "source_entry_id", "") == source_entry_id:
                out.append(f.to_dict() if hasattr(f, "to_dict") else {"original_text": str(f)})
    return out


def technologies_bound_to_entry(
    facts: list[dict[str, Any]] | list[Any],
    source_entry_id: str,
    *,
    also_general_skills: bool = False,
) -> set[str]:
    """Return tech tokens that are evidenced on this specific entry.

    General skill facts (source_section == 'skills') are excluded unless
    also_general_skills=True (never use for project bullet rewriting).
    """
    bound: set[str] = set()
    for f in facts or []:
        data = f if isinstance(f, dict) else (f.to_dict() if hasattr(f, "to_dict") else {})
        section = str(data.get("source_section") or "")
        entry = str(data.get("source_entry_id") or "")
        text = str(data.get("original_text") or data.get("normalized_value") or "")
        if entry == source_entry_id:
            bound |= extract_tech_mentions(text)
            for skill in data.get("explicit_skills") or []:
                bound |= extract_tech_mentions(str(skill))
        elif also_general_skills and section == "skills":
            bound |= extract_tech_mentions(text)
    return bound


def general_skill_technologies(facts: list[dict[str, Any]] | list[Any]) -> set[str]:
    techs: set[str] = set()
    for f in facts or []:
        data = f if isinstance(f, dict) else (f.to_dict() if hasattr(f, "to_dict") else {})
        if str(data.get("source_section") or "") != "skills":
            continue
        techs |= extract_tech_mentions(str(data.get("original_text") or ""))
        for skill in data.get("explicit_skills") or []:
            techs |= extract_tech_mentions(str(skill))
    return techs


def validate_bullet_tech_scope(
    bullet: str,
    *,
    source_entry_id: str,
    facts: list[dict[str, Any]] | list[Any],
    entry_source_text: str = "",
) -> tuple[bool, str, set[str]]:
    """Reject bullets that mention tech not bound to this entry.

    Returns (ok, reason, leaked_techs).
    """
    mentioned = extract_tech_mentions(bullet)
    if not mentioned:
        return True, "no_tech", set()

    bound = technologies_bound_to_entry(facts, source_entry_id)
    # Also allow tech that appears in the original entry source text itself
    bound |= extract_tech_mentions(entry_source_text)

    general = general_skill_technologies(facts)
    leaked = set()
    for tech in mentioned:
        if tech in bound:
            continue
        # Exact/substring match against bound tokens
        if any(tech in b or b in tech for b in bound if len(b) >= 3):
            continue
        # Present only as a general skill → LEAK
        if tech in general or any(tech in g or g in tech for g in general if len(g) >= 3):
            leaked.add(tech)
            continue
        # Completely novel tech
        leaked.add(tech)

    if leaked:
        return False, f"cross_entry_or_novel_tech:{','.join(sorted(leaked))}", leaked
    return True, "scoped_ok", set()


_OUTCOME_NOUNS = re.compile(
    r"\b("
    r"customer\s+satisfaction|user\s+engagement|system\s+scalability|"
    r"system\s+reliability|team\s+workflows?|streamlin(?:e|ed|ing)\s+delivery|"
    r"production[- ]grade\s+(?:ownership|architecture|applications?)"
    r")\b",
    re.I,
)


def has_unsupported_impact(statement: str, source_text: str) -> bool:
    """True when the statement invents quantified/result impact not grounded in source.

    Rules:
    - Invented metrics (numbers/% in statement but not in source) → unsupported
    - Impact verbs in the statement when the source has no impact verbs → unsupported
    - Impact verbs that already appear in the source are allowed (factual paraphrase)
    - Unsupported outcome nouns (customer satisfaction, scalability, …) without source → unsupported
    - Bare descriptive bullets without impact verbs → supported
    """
    statement = statement or ""
    src = source_text or ""

    # Outcome nouns require explicit source support even without impact verbs
    for match in _OUTCOME_NOUNS.finditer(statement):
        phrase = match.group(0).lower()
        if phrase not in src.lower():
            return True

    if not _IMPACT_VERBS.search(statement):
        return False

    stmt_metrics = {m.group(0).lower() for m in _METRIC_RE.finditer(statement)}
    src_metrics = {m.group(0).lower() for m in _METRIC_RE.finditer(src)}
    if stmt_metrics and not (stmt_metrics & src_metrics):
        return True  # invented / ungrounded metric

    # Novel impact language not present in the candidate's source material
    if not _IMPACT_VERBS.search(src):
        return True

    # Source already uses impact language — allow paraphrase without requiring metrics
    return False


def neutralize_unsupported_impact(statement: str) -> str:
    """Replace unsupported impact verbs with factual wording."""
    text = statement or ""

    def _repl(match: re.Match[str]) -> str:
        word = match.group(0)
        low = word.lower()
        replacement = _SAFE_FACTUAL_REPLACEMENTS.get(low)
        if not replacement:
            # Fallback stem strip
            replacement = "supported" if low.endswith("ed") else "supporting"
        # Preserve capitalization
        if word[0].isupper():
            return replacement.capitalize()
        return replacement

    text = _IMPACT_VERBS.sub(_repl, text)
    # Drop common AI filler tails left after neutralization
    text = re.sub(
        r",?\s*supporting\s+(?:data\s+)?"
        r"(?:quality|reliability|scalability|performance|integrity|efficiency)"
        r"(?:\s+and\s+\w+)?\b\.?",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\s+([,.;])", r"\1", text)
    if text and text[-1] not in ".!?":
        text += "."
    return text


def sanitize_resume_unsupported_impact(
    resume: dict[str, Any],
    *,
    source_text: str,
) -> tuple[dict[str, Any], list[str]]:
    """Neutralize unsupported impact wording across visible resume claims.

    Returns (cleaned_resume, list_of_changed_snippets).
    """
    out = dict(resume or {})
    changed: list[str] = []

    def _fix(text: str) -> str:
        original = str(text or "").strip()
        if not original or not has_unsupported_impact(original, source_text):
            return original
        fixed = neutralize_unsupported_impact(original)
        if fixed != original:
            changed.append(original[:80])
        return fixed

    summary = str(out.get("professional_summary") or out.get("summary") or "")
    if summary:
        fixed = _fix(summary)
        out["professional_summary"] = fixed
        out["summary"] = fixed

    experience: list[dict[str, Any]] = []
    for role in list(out.get("experience") or []):
        if not isinstance(role, dict):
            continue
        entry = dict(role)
        entry["bullets"] = [_fix(str(b)) for b in (entry.get("bullets") or []) if str(b).strip()]
        experience.append(entry)
    out["experience"] = experience

    projects: list[dict[str, Any]] = []
    for proj in list(out.get("projects") or []):
        if not isinstance(proj, dict):
            continue
        entry = dict(proj)
        if entry.get("description"):
            entry["description"] = _fix(str(entry.get("description") or ""))
        entry["bullets"] = [_fix(str(b)) for b in (entry.get("bullets") or []) if str(b).strip()]
        projects.append(entry)
    out["projects"] = projects

    return out, changed


def strip_leaked_tech_from_bullet(
    bullet: str,
    leaked: set[str],
    *,
    replacement_tech: str | None = None,
) -> str:
    """DEPRECATED — token-level deletion corrupts grammar.

    Kept as a no-op wrapper that returns the original text unchanged so any
    residual callers cannot produce ``using and .`` fragments. Use
    :func:`intelligent_tailoring.safe_claim_rewriter.rebuild_claim_from_facts`
    instead.
    """
    # Intentionally ignore leaked/replacement — never mutate substrings.
    _ = leaked, replacement_tech
    return (bullet or "").strip()


def _resolve_project_entry_id(
    proj: dict[str, Any],
    idx: int,
    orig_projects: list[dict[str, Any]],
    facts: list[dict[str, Any]] | list[Any],
) -> tuple[str, dict[str, Any], str]:
    """Map a tailored project to its source entry id by name, not list index."""
    name = str(proj.get("name") or "").strip()
    name_l = name.lower()
    orig: dict[str, Any] = {}
    entry_id = f"project_{idx}"

    for o_idx, op in enumerate(orig_projects):
        if str(op.get("name") or "").strip().lower() == name_l and name_l:
            orig = op
            entry_id = f"project_{o_idx}"
            break
    else:
        if idx < len(orig_projects):
            orig = orig_projects[idx]

    # Prefer fact-backed entry id when available
    for f in facts or []:
        data = f if isinstance(f, dict) else (f.to_dict() if hasattr(f, "to_dict") else {})
        if str(data.get("source_section") or "") != "projects":
            continue
        sid = str(data.get("source_entry_id") or "")
        ctx = str(data.get("context") or data.get("organization") or "").lower()
        text = str(data.get("original_text") or "").lower()
        if name_l and (name_l == ctx or name_l in text or name_l in ctx):
            if sid.startswith("project_"):
                entry_id = sid
                break

    entry_text = " ".join(
        [
            str(orig.get("name") or name),
            str(orig.get("description") or ""),
            " ".join(str(b) for b in (orig.get("bullets") or [])),
            " ".join(str(t) for t in (orig.get("technologies") or [])),
        ]
    )
    return entry_id, orig, entry_text


def validate_resume_tech_scope(
    tailored_resume: dict[str, Any],
    *,
    facts: list[dict[str, Any]] | list[Any],
    original_roles: list[dict[str, Any]] | None = None,
    original_projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate experience/project claims at complete-sentence level.

    Never deletes individual tokens from a sentence. Unsupported claims are
    safely rewritten from source facts or rejected entirely.
    """
    from intelligent_tailoring.safe_claim_rewriter import (
        rebuild_claim_from_facts,
        rewrite_skill_line,
    )
    from intelligent_tailoring.skill_taxonomy import normalize_skill_lines

    resume = {
        **tailored_resume,
        "experience": [
            dict(e) for e in (tailored_resume.get("experience") or []) if isinstance(e, dict)
        ],
        "projects": [
            dict(p) for p in (tailored_resume.get("projects") or []) if isinstance(p, dict)
        ],
        "skills": list(tailored_resume.get("skills") or []),
    }
    violations: list[dict[str, str]] = []
    claim_results: list[dict[str, Any]] = []

    orig_roles = list(original_roles or [])
    orig_projects = list(original_projects or [])

    for idx, entry in enumerate(resume["experience"]):
        company = str(entry.get("company") or "").strip()
        entry_id = f"role_{idx}"
        orig = orig_roles[idx] if idx < len(orig_roles) else {}
        for o_idx, orole in enumerate(orig_roles):
            if company and str(orole.get("company") or "").strip().lower() == company.lower():
                orig = orole
                entry_id = f"role_{o_idx}"
                break
        orig_bullets = [str(b).strip() for b in (orig.get("bullets") or []) if str(b).strip()]
        entry_text = " ".join(
            [
                str(orig.get("company") or entry.get("company") or ""),
                str(orig.get("title") or entry.get("title") or ""),
                " ".join(orig_bullets),
            ]
        )
        cleaned_bullets: list[str] = []
        for b_idx, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet).strip()
            if not text:
                continue
            claim = rebuild_claim_from_facts(
                original_claim=text,
                source_entry_id=entry_id,
                facts=facts,
                entry_source_text=entry_text,
                original_bullets=orig_bullets,
                section="experience",
                claim_id=f"experience_{idx}_b{b_idx}",
            )
            claim_results.append(claim.to_dict())
            if claim.validation_status == "rejected":
                violations.append(
                    {
                        "section": "experience",
                        "text": text,
                        "reason": ",".join(claim.validation_errors) or "rejected",
                    }
                )
                continue
            if claim.validation_status == "safely_rewritten":
                violations.append(
                    {
                        "section": "experience",
                        "text": text,
                        "reason": f"safely_rewritten:{claim.repair_method}",
                    }
                )
            cleaned_bullets.append(claim.final_text)
        if not cleaned_bullets and orig_bullets:
            cleaned_bullets = orig_bullets[:6]
            violations.append(
                {
                    "section": "experience",
                    "text": company or entry_id,
                    "reason": "restored_source_bullets_after_unsafe_generation",
                }
            )
        entry["bullets"] = cleaned_bullets
        entry["source_entry_id"] = entry_id

    for idx, proj in enumerate(resume["projects"]):
        entry_id, orig, entry_text = _resolve_project_entry_id(
            proj, idx, orig_projects, facts
        )
        for tech in orig.get("technologies") or []:
            entry_text += f" {tech}"
        orig_bullets = [str(b).strip() for b in (orig.get("bullets") or []) if str(b).strip()]

        cleaned_bullets = []
        for b_idx, bullet in enumerate(proj.get("bullets") or []):
            text = str(bullet).strip()
            if not text:
                continue
            claim = rebuild_claim_from_facts(
                original_claim=text,
                source_entry_id=entry_id,
                facts=facts,
                entry_source_text=entry_text,
                original_bullets=orig_bullets,
                section="projects",
                claim_id=f"projects_{idx}_b{b_idx}",
            )
            claim_results.append(claim.to_dict())
            if claim.validation_status == "rejected":
                violations.append(
                    {
                        "section": "projects",
                        "text": text,
                        "reason": ",".join(claim.validation_errors) or "rejected",
                    }
                )
                continue
            if claim.validation_status == "safely_rewritten":
                violations.append(
                    {
                        "section": "projects",
                        "text": text,
                        "reason": f"safely_rewritten:{claim.repair_method}",
                    }
                )
            cleaned_bullets.append(claim.final_text)

        desc = str(proj.get("description") or "").strip()
        if desc:
            claim = rebuild_claim_from_facts(
                original_claim=desc,
                source_entry_id=entry_id,
                facts=facts,
                entry_source_text=entry_text,
                original_bullets=orig_bullets
                + ([str(orig.get("description") or "")] if orig.get("description") else []),
                section="projects",
                claim_id=f"projects_{idx}_desc",
            )
            claim_results.append(claim.to_dict())
            if claim.validation_status == "rejected":
                violations.append(
                    {
                        "section": "projects",
                        "text": desc,
                        "reason": ",".join(claim.validation_errors) or "rejected",
                    }
                )
                src_desc = str(orig.get("description") or "").strip()
                proj["description"] = src_desc
            else:
                if claim.validation_status == "safely_rewritten":
                    violations.append(
                        {
                            "section": "projects",
                            "text": desc,
                            "reason": f"safely_rewritten:{claim.repair_method}",
                        }
                    )
                proj["description"] = claim.final_text

        if not cleaned_bullets and orig_bullets:
            cleaned_bullets = orig_bullets[:6]
            violations.append(
                {
                    "section": "projects",
                    "text": str(proj.get("name") or entry_id),
                    "reason": "restored_source_bullets_after_unsafe_generation",
                }
            )
        if not str(proj.get("description") or "").strip():
            src_desc = str(orig.get("description") or "").strip()
            if src_desc:
                proj["description"] = src_desc
        proj["bullets"] = cleaned_bullets
        proj["source_entry_id"] = entry_id
        bound = technologies_bound_to_entry(facts, entry_id) | extract_tech_mentions(entry_text)
        if not proj.get("technologies") and orig.get("technologies"):
            proj["technologies"] = list(orig.get("technologies") or [])
        if proj.get("technologies"):
            kept_techs = []
            for t in proj.get("technologies") or []:
                tn = _norm(str(t))
                if tn in bound or any(tn in b or b in tn for b in bound if len(b) >= 3):
                    kept_techs.append(t)
                else:
                    violations.append(
                        {
                            "section": "projects",
                            "text": str(t),
                            "reason": f"cross_entry_or_novel_tech:{tn}",
                        }
                    )
            proj["technologies"] = kept_techs

    # Skills: drop whole unsupported atoms — never leave empty category slots
    all_source_tech = set()
    for f in facts or []:
        data = f if isinstance(f, dict) else (f.to_dict() if hasattr(f, "to_dict") else {})
        all_source_tech |= extract_tech_mentions(str(data.get("original_text") or ""))
        for skill in data.get("explicit_skills") or []:
            all_source_tech |= extract_tech_mentions(str(skill))

    cleaned_skills = []
    for skill in resume.get("skills") or []:
        rewritten, rejected = rewrite_skill_line(str(skill), allowed_techs=all_source_tech)
        for r in rejected:
            violations.append(
                {"section": "skills", "text": r, "reason": "novel_skill"}
            )
        if rewritten:
            cleaned_skills.append(rewritten)
    # Deterministic category normalization
    resume["skills"] = normalize_skill_lines(cleaned_skills)

    # Summary: never token-strip. Blank invalid summaries for the structured builder.
    summary = str(resume.get("professional_summary") or resume.get("summary") or "")
    full_source = " ".join(
        str((f if isinstance(f, dict) else f.to_dict()).get("original_text") or "")
        for f in (facts or [])
    )
    if summary:
        ok, reason, leaked = validate_bullet_tech_scope(
            summary,
            source_entry_id="",
            facts=facts,
            entry_source_text=full_source,
        )
        if (not ok and leaked) or has_unsupported_impact(summary, full_source):
            violations.append(
                {
                    "section": "summary",
                    "text": summary,
                    "reason": reason if not ok else "unsupported_impact",
                }
            )
            summary = ""  # force structured rebuild upstream
    resume["professional_summary"] = summary
    resume["summary"] = summary

    return {
        "cleaned_resume": resume,
        "violations": violations,
        "claim_results": claim_results,
        "passed": not any(
            v.get("reason", "").startswith("cross_entry")
            or v.get("reason", "").startswith("novel")
            or "unsupported" in (v.get("reason") or "")
            for v in violations
            if "safely_rewritten" not in (v.get("reason") or "")
            and "restored_source" not in (v.get("reason") or "")
        ),
    }
