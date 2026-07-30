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


def has_unsupported_impact(statement: str, source_text: str) -> bool:
    """True when impact verb is used but source has no supporting metric/result phrasing."""
    if not _IMPACT_VERBS.search(statement or ""):
        return False
    src = source_text or ""
    # If the source itself contains the same impact phrasing or a metric, allow.
    if _METRIC_RE.search(src) and _IMPACT_VERBS.search(src):
        # Only allow if the specific metric appears in the statement OR source shares impact context
        stmt_metrics = {m.group(0).lower() for m in _METRIC_RE.finditer(statement or "")}
        src_metrics = {m.group(0).lower() for m in _METRIC_RE.finditer(src)}
        if stmt_metrics and stmt_metrics & src_metrics:
            return False
        if stmt_metrics and not (stmt_metrics & src_metrics):
            return True  # invented metric
        # Impact verb in source without metric — still weak; require metric for generated impact
        return True
    if _METRIC_RE.search(statement or "") and not _METRIC_RE.search(src):
        return True  # invented metric
    if _IMPACT_VERBS.search(statement or "") and not _IMPACT_VERBS.search(src):
        return True
    return bool(_IMPACT_VERBS.search(statement or ""))


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

    return _IMPACT_VERBS.sub(_repl, text)


def strip_leaked_tech_from_bullet(
    bullet: str,
    leaked: set[str],
    *,
    replacement_tech: str | None = None,
) -> str:
    """Remove or replace leaked technology mentions from a bullet."""
    text = bullet
    for tech in sorted(leaked, key=len, reverse=True):
        pattern = re.compile(re.escape(tech), re.I)
        if replacement_tech:
            text = pattern.sub(replacement_tech, text)
        else:
            text = pattern.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ",", text)
    return text.strip(" ,;-")


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
    """Validate all experience/project bullets for tech scope + impact.

    Returns cleaned resume + violations list.
    """
    resume = {
        **tailored_resume,
        "experience": [dict(e) for e in (tailored_resume.get("experience") or []) if isinstance(e, dict)],
        "projects": [dict(p) for p in (tailored_resume.get("projects") or []) if isinstance(p, dict)],
        "skills": list(tailored_resume.get("skills") or []),
    }
    violations: list[dict[str, str]] = []

    # Build original entry text lookup by approximate index / name
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
        entry_text = " ".join(
            [
                str(orig.get("company") or entry.get("company") or ""),
                str(orig.get("title") or entry.get("title") or ""),
                " ".join(str(b) for b in (orig.get("bullets") or [])),
            ]
        )
        cleaned_bullets: list[str] = []
        for bullet in entry.get("bullets") or []:
            text = str(bullet).strip()
            if not text:
                continue
            ok, reason, leaked = validate_bullet_tech_scope(
                text,
                source_entry_id=entry_id,
                facts=facts,
                entry_source_text=entry_text,
            )
            if not ok:
                violations.append({"section": "experience", "text": text, "reason": reason})
                # Strip leaked tech only — never invent a replacement technology
                text = strip_leaked_tech_from_bullet(text, leaked, replacement_tech=None)
                if not text:
                    continue
                ok2, _, leaked2 = validate_bullet_tech_scope(
                    text,
                    source_entry_id=entry_id,
                    facts=facts,
                    entry_source_text=entry_text,
                )
                if not ok2 and leaked2:
                    continue  # drop bullet if still contaminated
            if has_unsupported_impact(text, entry_text):
                violations.append(
                    {"section": "experience", "text": text, "reason": "unsupported_impact"}
                )
                text = neutralize_unsupported_impact(text)
            cleaned_bullets.append(text)
        entry["bullets"] = cleaned_bullets
        entry["source_entry_id"] = entry_id

    for idx, proj in enumerate(resume["projects"]):
        entry_id, orig, entry_text = _resolve_project_entry_id(
            proj, idx, orig_projects, facts
        )
        # Also bind technologies field as facts for this entry
        for tech in orig.get("technologies") or []:
            entry_text += f" {tech}"

        cleaned_bullets = []
        for bullet in proj.get("bullets") or []:
            text = str(bullet).strip()
            if not text:
                continue
            ok, reason, leaked = validate_bullet_tech_scope(
                text,
                source_entry_id=entry_id,
                facts=facts,
                entry_source_text=entry_text,
            )
            if not ok:
                violations.append({"section": "projects", "text": text, "reason": reason})
                text = strip_leaked_tech_from_bullet(text, leaked, replacement_tech=None)
                if not text:
                    continue
                # Re-check
                ok2, _, leaked2 = validate_bullet_tech_scope(
                    text,
                    source_entry_id=entry_id,
                    facts=facts,
                    entry_source_text=entry_text,
                )
                if not ok2 and leaked2:
                    continue
            if has_unsupported_impact(text, entry_text):
                violations.append(
                    {"section": "projects", "text": text, "reason": "unsupported_impact"}
                )
                text = neutralize_unsupported_impact(text)
            cleaned_bullets.append(text)

        # Description scope check
        desc = str(proj.get("description") or "").strip()
        if desc:
            ok, reason, leaked = validate_bullet_tech_scope(
                desc,
                source_entry_id=entry_id,
                facts=facts,
                entry_source_text=entry_text,
            )
            if not ok:
                violations.append({"section": "projects", "text": desc, "reason": reason})
                desc = strip_leaked_tech_from_bullet(desc, leaked, replacement_tech=None)
                ok2, _, leaked2 = validate_bullet_tech_scope(
                    desc,
                    source_entry_id=entry_id,
                    facts=facts,
                    entry_source_text=entry_text,
                )
                if not ok2 and leaked2:
                    desc = ""
            if desc and has_unsupported_impact(desc, entry_text):
                desc = neutralize_unsupported_impact(desc)
            proj["description"] = desc
        proj["bullets"] = cleaned_bullets
        project_name = str(proj.get("name") or orig.get("name") or entry_id)
        # If validation wiped every bullet, restore original evidenced bullets
        if not cleaned_bullets:
            restored = [
                str(b).strip()
                for b in (orig.get("bullets") or [])
                if str(b).strip()
            ]
            if restored:
                proj["bullets"] = restored[:6]
                violations.append(
                    {
                        "section": "projects",
                        "text": project_name,
                        "reason": "restored_source_bullets_after_unsafe_generation",
                    }
                )
        # Restore description from source when wiped
        if not str(proj.get("description") or "").strip():
            src_desc = str(orig.get("description") or "").strip()
            if src_desc and not has_unsupported_impact(src_desc, entry_text):
                proj["description"] = src_desc
        proj["source_entry_id"] = entry_id
        # Technologies list: keep only those bound to this entry
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

    # Skills: drop any skill not evidenced anywhere in facts / source
    all_source_tech = set()
    for f in facts or []:
        data = f if isinstance(f, dict) else (f.to_dict() if hasattr(f, "to_dict") else {})
        all_source_tech |= extract_tech_mentions(str(data.get("original_text") or ""))
    # Also keep non-tech skill strings that appear as fact original_text
    fact_texts = {
        _norm(str((f if isinstance(f, dict) else f.to_dict()).get("original_text") or ""))
        for f in (facts or [])
    }
    cleaned_skills = []
    for skill in resume.get("skills") or []:
        raw = str(skill)
        techs = extract_tech_mentions(raw)
        if not techs:
            # Non-tech skill line — keep if text appears in facts or is short category
            cleaned_skills.append(raw)
            continue
        leaked_in_line = set()
        kept_techs = set()
        for t in techs:
            evidenced = t in all_source_tech or any(
                t in s or s in t for s in all_source_tech if len(s) >= 3
            )
            if evidenced:
                kept_techs.add(t)
            else:
                leaked_in_line.add(t)
        if leaked_in_line:
            violations.append(
                {
                    "section": "skills",
                    "text": raw,
                    "reason": f"novel_skill:{','.join(sorted(leaked_in_line))}",
                }
            )
            raw = strip_leaked_tech_from_bullet(raw, leaked_in_line)
            # Clean empty category leftovers like "Frontend: ,"
            raw = re.sub(r":\s*,", ":", raw)
            raw = re.sub(r",\s*$", "", raw)
            raw = re.sub(r":\s*$", "", raw).strip(" ,;-")
            if not raw or not extract_tech_mentions(raw):
                # If only category label remains without tech, drop
                if ":" in raw and not extract_tech_mentions(raw):
                    continue
                if not raw:
                    continue
        if not kept_techs and techs:
            continue
        cleaned_skills.append(raw)
    resume["skills"] = cleaned_skills

    # Summary impact / novel tech
    summary = str(resume.get("professional_summary") or resume.get("summary") or "")
    full_source = " ".join(
        str((f if isinstance(f, dict) else f.to_dict()).get("original_text") or "")
        for f in (facts or [])
    )
    if summary and has_unsupported_impact(summary, full_source):
        violations.append({"section": "summary", "text": summary, "reason": "unsupported_impact"})
        summary = neutralize_unsupported_impact(summary)
    # Novel tech in summary
    ok, reason, leaked = validate_bullet_tech_scope(
        summary,
        source_entry_id="",  # empty → only general+all facts via entry_source_text
        facts=facts,
        entry_source_text=full_source,
    )
    if not ok:
        violations.append({"section": "summary", "text": summary, "reason": reason})
        summary = strip_leaked_tech_from_bullet(summary, leaked)
    resume["professional_summary"] = summary
    resume["summary"] = summary

    return {
        "cleaned_resume": resume,
        "violations": violations,
        "passed": len(violations) == 0,
    }
