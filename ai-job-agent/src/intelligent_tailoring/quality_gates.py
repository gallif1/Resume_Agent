"""Pre-export quality gates for Intelligent Resume Tailoring.

Gates classify into critical (block download) vs warning (preview OK).
Unsupported claims, schema failures, and empty summaries are critical.
Preview must remain available even when critical gates fail (review mode).
"""

from __future__ import annotations

from typing import Any

from intelligent_tailoring.claim_validator import statement_supported_by_evidence
from intelligent_tailoring.gate_severity import (
    classify_quality_gates,
    should_block_download,
)
from intelligent_tailoring.scope_validator import (
    _resolve_project_entry_id,
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
    require_one_page: bool = False,
    pdf_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Evaluate export gates and classify severity for preview vs download."""
    failures: list[str] = []
    warnings: list[str] = []
    resume = tailored_resume if isinstance(tailored_resume, dict) else {}
    facts = list(facts or [])
    change_log = list(change_log or [])
    source = original_resume_text or ""
    _ = original_roles  # reserved for future role-scope gates

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
        # Summaries are validated by summary_builder / linguistic integrity —
        # entity token checks falsely flag role words (e.g. DevOps) from titles.
        if section == "summary":
            continue
        ok, reason = statement_supported_by_evidence(
            text,
            source_text=source,
            min_token_overlap=0.35,
        )
        if not ok and "unsupported_entities" in reason:
            failures.append(f"unsupported_entity:{section}:{reason}")
            unsupported += 1

    # Cross-entry tech leakage against KB facts — use shared project resolver
    # so renamed titles (Restaurant Menu Ordering App) map to Restaurant App.
    orig_projects = list(original_projects or [])
    for idx, proj in enumerate(resume.get("projects") or []):
        if not isinstance(proj, dict):
            continue
        name = str(proj.get("name") or "")
        entry_id, _orig, entry_text = _resolve_project_entry_id(
            proj, idx, orig_projects, facts
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
        # Also bind normalized_value technology facts
        nv = str(f.get("normalized_value") or "")
        if nv:
            all_source_tech |= extract_tech_mentions(nv)
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

    # One-page default — hard gate unless caller disabled it
    page_meta: dict[str, Any] = {}
    if require_one_page:
        from intelligent_tailoring.services.page_count import assert_one_page
        from intelligent_tailoring.services.one_page_compressor import (
            estimate_page_pressure,
        )

        ok_page, page_reason = assert_one_page(
            pdf_bytes=pdf_bytes, resume=resume, allow_multi_page=False
        )
        page_meta = {
            "ok": ok_page,
            "reason": page_reason,
            "pressure": estimate_page_pressure(resume),
        }
        if not ok_page:
            failures.append(
                page_reason
                if str(page_reason).startswith("page_count:")
                else f"page_count:{page_reason}"
            )

    # Deduplicate failures
    unique_failures = list(dict.fromkeys(failures))
    result = {
        "passed": len(unique_failures) == 0,
        "failures": unique_failures,
        "warnings": warnings,
        "unsupported_claim_count": unsupported,
        "gate_version": "quality_gates_v3",
        "one_page": page_meta,
    }
    return classify_quality_gates(result)


def should_block_export(gates: dict[str, Any] | None) -> bool:
    """Block download/export on critical failures only (preview stays allowed)."""
    return should_block_download(gates)
