"""Pre-export quality gates for Intelligent Resume Tailoring.

Gates must pass before a resume is persisted for download. Soft warnings are
allowed; unsupported claims, schema failures, and empty summaries are not.
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.claim_validator import statement_supported_by_evidence
from intelligent_tailoring.scope_validator import (
    extract_tech_mentions,
    has_unsupported_impact,
    validate_bullet_tech_scope,
)


def _resume_blob(resume: dict[str, Any]) -> str:
    parts: list[str] = [
        str(resume.get("professional_summary") or resume.get("summary") or ""),
        " ".join(str(s) for s in (resume.get("skills") or [])),
    ]
    for entry in resume.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        parts.append(str(entry.get("company") or ""))
        parts.append(str(entry.get("title") or ""))
        parts.extend(str(b) for b in (entry.get("bullets") or []))
    for entry in resume.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        parts.append(str(entry.get("name") or ""))
        parts.append(str(entry.get("description") or ""))
        parts.extend(str(b) for b in (entry.get("bullets") or []))
        parts.extend(str(t) for t in (entry.get("technologies") or []))
    return "\n".join(parts)


def _iter_claim_texts(resume: dict[str, Any]) -> list[tuple[str, str]]:
    """Yield (section, text) for every user-visible claim."""
    out: list[tuple[str, str]] = []
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    if summary:
        out.append(("summary", summary))
    for skill in resume.get("skills") or []:
        text = str(skill).strip()
        if text:
            out.append(("skills", text))
    for entry in resume.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        for bullet in entry.get("bullets") or []:
            text = str(bullet).strip()
            if text:
                out.append(("experience", text))
    for entry in resume.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        desc = str(entry.get("description") or "").strip()
        if desc:
            out.append(("projects", desc))
        for bullet in entry.get("bullets") or []:
            text = str(bullet).strip()
            if text:
                out.append(("projects", text))
    return out


def change_log_matches_resume(
    change_log: list[dict[str, Any]],
    resume: dict[str, Any],
) -> list[str]:
    """Return failures when change_log advertises text not present in the resume."""
    blob = _resume_blob(resume).lower()
    failures: list[str] = []
    for item in change_log or []:
        if not isinstance(item, dict):
            failures.append("change_log_item_not_object")
            continue
        change_type = str(item.get("change_type") or "").lower()
        if change_type in ("removed", "deprioritized", "reordered"):
            continue
        new_text = str(item.get("new_text") or "").strip()
        if not new_text:
            continue
        # Allow partial match for long rewritten bullets
        probe = new_text[:80].lower() if len(new_text) > 80 else new_text.lower()
        if probe and probe not in blob and new_text.lower() not in blob:
            failures.append(f"change_log_text_missing_from_resume:{new_text[:60]}")
    return failures


def evaluate_quality_gates(
    *,
    tailored_resume: dict[str, Any],
    original_resume_text: str,
    facts: list[dict[str, Any]] | None = None,
    change_log: list[dict[str, Any]] | None = None,
    original_roles: list[dict[str, Any]] | None = None,
    original_projects: list[dict[str, Any]] | None = None,
    require_summary: bool = True,
    rejected_statements: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate hard export gates. ``passed`` must be True to export."""
    failures: list[str] = []
    warnings: list[str] = []
    resume = tailored_resume if isinstance(tailored_resume, dict) else {}
    facts = list(facts or [])
    change_log = list(change_log or [])
    source = original_resume_text or ""

    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    if require_summary and not summary:
        failures.append("missing_professional_summary")

    # Re-check every visible claim for impact + novel entities
    unsupported = 0
    for section, text in _iter_claim_texts(resume):
        if has_unsupported_impact(text, source):
            failures.append(f"unsupported_impact:{section}:{text[:60]}")
            unsupported += 1
            continue
        # Skills are validated via the unknown_skill tech scan below.
        # Category labels like "Tools:" must not be treated as novel entities.
        if section == "skills":
            continue
        ok, reason = statement_supported_by_evidence(
            text,
            source_text=source,
            min_token_overlap=0.35,
        )
        if not ok and "unsupported_entities" in reason:
            failures.append(f"unsupported_entity:{section}:{reason}")
            unsupported += 1

    # Cross-entry tech leakage against KB facts
    orig_projects = list(original_projects or [])
    for idx, proj in enumerate(resume.get("projects") or []):
        if not isinstance(proj, dict):
            continue
        name = str(proj.get("name") or "")
        name_l = name.lower()
        entry_id = str(proj.get("source_entry_id") or f"project_{idx}")
        orig: dict[str, Any] = {}
        for o_idx, op in enumerate(orig_projects):
            if str(op.get("name") or "").lower() == name_l and name_l:
                orig = op
                entry_id = f"project_{o_idx}"
                break
        if not orig and idx < len(orig_projects):
            orig = orig_projects[idx]
        entry_text = " ".join(
            [
                str(orig.get("name") or name),
                str(orig.get("description") or ""),
                " ".join(str(b) for b in (orig.get("bullets") or [])),
                " ".join(str(t) for t in (orig.get("technologies") or [])),
            ]
        )
        for bullet in list(proj.get("bullets") or []) + [
            str(proj.get("description") or "")
        ]:
            text = str(bullet).strip()
            if not text:
                continue
            ok, reason, leaked = validate_bullet_tech_scope(
                text,
                source_entry_id=entry_id,
                facts=facts,
                entry_source_text=entry_text,
            )
            if not ok and leaked:
                failures.append(f"cross_entry_tech:{name}:{','.join(sorted(leaked))}")

    # Skills must not introduce unknown tech
    all_source_tech = set()
    for f in facts:
        all_source_tech |= extract_tech_mentions(str(f.get("original_text") or ""))
        for skill in f.get("explicit_skills") or []:
            all_source_tech |= extract_tech_mentions(str(skill))
    all_source_tech |= extract_tech_mentions(source)
    for skill in resume.get("skills") or []:
        for tech in extract_tech_mentions(str(skill)):
            if tech in all_source_tech:
                continue
            if any(tech in s or s in tech for s in all_source_tech if len(s) >= 3):
                continue
            failures.append(f"unknown_skill:{tech}")

    # Change log must only describe final resume content
    log_failures = change_log_matches_resume(change_log, resume)
    failures.extend(log_failures)

    # Raw LLM prose markers must not appear in the report
    for item in change_log:
        reason = str(item.get("reason") or "")
        if any(
            marker in reason.lower()
            for marker in ("chain of thought", "as an ai", "i will now", "step 1:")
        ):
            failures.append("raw_llm_reasoning_in_change_log")

    if rejected_statements:
        warnings.append(f"{len(rejected_statements)} statements rejected during validation")

    # Deduplicate failures
    unique_failures = list(dict.fromkeys(failures))
    passed = len(unique_failures) == 0
    return {
        "passed": passed,
        "failures": unique_failures,
        "warnings": warnings,
        "unsupported_claim_count": unsupported,
        "gate_version": "quality_gates_v1",
    }


def should_block_export(gates: dict[str, Any] | None) -> bool:
    if not gates:
        return True
    return not bool(gates.get("passed"))
