"""ResumeRewriter — strategy-driven LLM section rewriting."""

from __future__ import annotations

import json
import logging
from typing import Any

from intelligent_tailoring.llm_utils import (
    call_stage_json,
    call_stage_json_with_content_validation,
)
from intelligent_tailoring.prompts.merged_prompts import (
    AGENT_2_SYSTEM,
    MERGED_AGENT_2_PROMPT_VERSION,
)
from intelligent_tailoring.prompts.stage_prompts import (
    DEEP_TAILOR_REWRITE_SYSTEM,
    build_deep_tailor_rewrite_user_prompt,
)
from intelligent_tailoring.schemas import (
    PIPELINE_VERSION,
    InferredCompetency,
    SchemaValidationError,
    sanitize_change_log_raw,
    validate_change_log_item,
    validate_tailored_resume,
)
from intelligent_tailoring.stages.resume_extraction import resume_facts_for_prompt
from intelligent_tailoring.structured_resume import (
    assign_stable_ids,
    stamp_ids_on_resume,
    structured_to_pipeline_resume,
    to_structured_resume,
)
from intelligent_tailoring.structured_validation import (
    repair_structured_resume,
    validate_structured_resume,
)

logger = logging.getLogger("intelligent_tailoring.resume_rewriter")


def _validate(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise SchemaValidationError("rewrite response must be an object")
    # Tolerate models that nest under alternate keys after composition
    if "tailored_resume" not in data:
        for alt in ("resume", "tailored_cv", "cv"):
            if isinstance(data.get(alt), dict):
                data["tailored_resume"] = data[alt]
                break
    if "tailored_resume" not in data:
        raise SchemaValidationError("missing tailored_resume")
    # Accept structured schema aliases (position/organization/skills dict)
    resume = data["tailored_resume"]
    if isinstance(resume, dict):
        data["tailored_resume"] = _coerce_llm_resume_to_pipeline(resume)
    validate_tailored_resume(data["tailored_resume"])
    data["change_log"] = sanitize_change_log_raw(data.get("change_log"))
    for i, item in enumerate(data["change_log"]):
        validate_change_log_item(item, index=i)


def _coerce_llm_resume_to_pipeline(resume: dict[str, Any]) -> dict[str, Any]:
    """Accept structured-schema fields from the LLM and map to pipeline shape."""
    out = dict(resume)
    # Title aliases
    if not out.get("professional_title") and out.get("title"):
        out["professional_title"] = out.get("title")
    if not out.get("professional_summary") and out.get("summary"):
        out["professional_summary"] = out.get("summary")
    # Skills dict → categorized lines
    if isinstance(out.get("skills"), dict):
        from intelligent_tailoring.structured_resume import skills_dict_to_list

        out["skills"] = skills_dict_to_list(out["skills"])
    # Experience field aliases
    exp = []
    for entry in out.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        fixed = dict(entry)
        if not fixed.get("title"):
            fixed["title"] = fixed.get("position") or ""
        if not fixed.get("company"):
            fixed["company"] = fixed.get("organization") or ""
        if not fixed.get("dates"):
            fixed["dates"] = fixed.get("dateRange") or fixed.get("date_range") or ""
        sid = str(fixed.get("id") or fixed.get("source_entry_id") or "").strip()
        if sid:
            fixed["id"] = sid
            fixed["source_entry_id"] = sid
        exp.append(fixed)
    out["experience"] = exp
    # Projects field aliases
    projects = []
    for entry in out.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        fixed = dict(entry)
        if not fixed.get("name"):
            fixed["name"] = fixed.get("title") or ""
        sid = str(fixed.get("id") or fixed.get("source_entry_id") or "").strip()
        if sid:
            fixed["id"] = sid
            fixed["source_entry_id"] = sid
        projects.append(fixed)
    out["projects"] = projects
    # Education field aliases
    edu = []
    for entry in out.get("education") or []:
        if not isinstance(entry, dict):
            continue
        fixed = dict(entry)
        if not fixed.get("field"):
            fixed["field"] = fixed.get("fieldOfStudy") or fixed.get("field_of_study") or ""
        if not fixed.get("dates"):
            fixed["dates"] = fixed.get("dateRange") or fixed.get("date_range") or ""
        edu.append(fixed)
    if edu:
        out["education"] = edu
    # Ensure required keys exist for validate_tailored_resume
    for key in (
        "professional_summary",
        "skills",
        "experience",
        "projects",
        "education",
        "certifications",
    ):
        if key not in out:
            out[key] = [] if key != "professional_summary" else ""
    return out


def _content_validate_agent2(
    raw: dict[str, Any],
    *,
    source_facts: dict[str, Any],
    jd_text: str = "",
):
    resume = raw.get("tailored_resume") if isinstance(raw, dict) else {}
    jd_blob = (jd_text or "").strip()
    if not jd_blob and isinstance(source_facts, dict):
        jd_blob = str(source_facts.get("jd_text") or "").strip()
    return validate_structured_resume(
        resume if isinstance(resume, dict) else {},
        source_facts=source_facts,
        enforce_fullness=True,
        # Agent 2 may leave summary thin; Agent 3 polishes prose. Still catch
        # broken/competing lead-ins when a summary is present.
        require_summary=False,
        jd_text=jd_blob,
    )


def _fallback_from_rebuilt(
    *,
    rebuilt_resume: dict[str, Any],
    resume_facts: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic structure when the LLM omits tailored_resume."""
    facts = assign_stable_ids(resume_facts)
    resume = stamp_ids_on_resume(dict(rebuilt_resume or {}), source_facts=facts)
    if not resume.get("skills"):
        resume["skills"] = list(
            facts.get("display_skills") or facts.get("skills") or []
        )
    if not resume.get("experience"):
        resume["experience"] = list(facts.get("experience_roles") or [])
    if not resume.get("projects"):
        resume["projects"] = list(facts.get("projects") or [])
    if not resume.get("education"):
        resume["education"] = list(facts.get("education") or [])
    summary = str(
        resume.get("professional_summary")
        or resume.get("summary")
        or (strategy.get("summary_focus") or [""])[0]
        or ""
    ).strip()
    resume["professional_summary"] = summary
    resume["summary"] = summary
    if not resume.get("professional_title"):
        resume["professional_title"] = str(
            strategy.get("target_title")
            or facts.get("professional_title")
            or ""
        )
    resume = repair_structured_resume(resume, source_facts=facts)
    validated = validate_tailored_resume(resume).to_dict()
    if isinstance(resume.get("contact"), dict):
        validated["contact"] = dict(resume["contact"])
    return {
        "tailored_resume": validated,
        "change_log": [],
        "matched_requirements": list(strategy.get("must_highlight_in_summary") or []),
        "missing_requirements": list(strategy.get("genuine_gaps") or []),
        "removed_or_deprioritized_content": list(
            strategy.get("facts_to_condense") or []
        ),
        "ats_keywords_added": [],
        "_from_cache": False,
        "_fallback": "rebuilt_resume",
    }


def rewrite_resume_with_strategy(
    *,
    resume_facts: dict[str, Any],
    rebuilt_resume: dict[str, Any],
    strategy: dict[str, Any],
    scores: dict[str, Any],
    ranked_requirements: list[dict[str, Any]],
    inferred: list[InferredCompetency],
    evidence_map: list[dict[str, Any]],
    triage: dict[str, Any],
    language: str = "en",
    use_cache: bool = True,
    regeneration_attempt: int = 0,
) -> dict[str, Any]:
    """Rewrite summary, bullets, and project descriptions per tailoring strategy.

    Uses the composed Agent 2 prompt (strategy + triage rules + deep tailor).
    Falls back to the legacy deep-tailor prompt, then to rebuilt structure,
    so preview generation cannot hard-fail on a missing tailored_resume key.
    """
    source_facts = assign_stable_ids(resume_facts)
    # Carry JD snapshot for contamination checks / repair (not for LLM claims).
    jd_blob = str(
        (strategy or {}).get("jd_text")
        or (strategy or {}).get("job_description")
        or source_facts.get("jd_text")
        or ""
    ).strip()
    if jd_blob:
        source_facts["jd_text"] = jd_blob
    rebuilt_resume = stamp_ids_on_resume(
        rebuilt_resume if isinstance(rebuilt_resume, dict) else {},
        source_facts=source_facts,
    )
    # Ensure contact from base is visible to the model
    if source_facts.get("contact") and not rebuilt_resume.get("contact"):
        rebuilt_resume["contact"] = dict(source_facts["contact"])

    cache_suffix = f"|regen{regeneration_attempt}" if regeneration_attempt else ""
    # Strategy for the LLM: keep emphasis fields, drop raw JD blob so employer
    # voice cannot be copied into candidate claims (jd_blob stays for validation).
    from intelligent_tailoring.prompts.human_writer_prompts import (
        sanitize_strategy_for_writer,
    )

    strategy_for_prompt = sanitize_strategy_for_writer(strategy or {})
    user_prompt = build_deep_tailor_rewrite_user_prompt(
        resume_facts=resume_facts_for_prompt(source_facts),
        rebuilt_resume_json=json.dumps(rebuilt_resume, ensure_ascii=False, indent=2),
        strategy_json=json.dumps(strategy_for_prompt, ensure_ascii=False, indent=2),
        scores_json=json.dumps(scores, ensure_ascii=False, indent=2),
        ranked_requirements_json=json.dumps(
            ranked_requirements, ensure_ascii=False, indent=2
        ),
        inferred_json=json.dumps(
            [i.to_dict() for i in inferred], ensure_ascii=False, indent=2
        ),
        triage_json=json.dumps(triage, ensure_ascii=False, indent=2),
        evidence_map_json=json.dumps(evidence_map, ensure_ascii=False, indent=2),
        language=language,
        regeneration_attempt=regeneration_attempt,
    )
    cache_payload = (
        f"{language}|{strategy.get('job_family')}|"
        f"{resume_facts_for_prompt(source_facts)[:2500]}{cache_suffix}"
    )

    def _validate_content(payload: dict[str, Any]):
        return _content_validate_agent2(
            payload, source_facts=source_facts, jd_text=jd_blob
        )

    raw: dict[str, Any] | None = None
    try:
        raw = call_stage_json_with_content_validation(
            system_prompt=AGENT_2_SYSTEM,
            user_prompt=user_prompt,
            validate=_validate,
            content_validate=_validate_content,
            use_cache=use_cache and regeneration_attempt == 0,
            cache_namespace=f"{MERGED_AGENT_2_PROMPT_VERSION}_strategy_content",
            cache_payload=cache_payload,
            temperature=0.25 if regeneration_attempt else 0.2,
            count_as_primary="strategy_content_selection",
            max_content_retries=1,
        )
    except SchemaValidationError as composed_error:
        logger.warning(
            "merged Agent 2 schema failed (%s) — falling back to deep-tailor prompt",
            composed_error,
        )
        try:
            raw = call_stage_json_with_content_validation(
                system_prompt=DEEP_TAILOR_REWRITE_SYSTEM,
                user_prompt=user_prompt,
                validate=_validate,
                content_validate=_validate_content,
                use_cache=False,
                cache_namespace=f"{PIPELINE_VERSION}_deep_rewrite_fallback",
                cache_payload=f"fallback|{cache_payload}",
                temperature=0.2,
                # Already counted the composed primary call; do not double-count
                max_content_retries=1,
            )
        except SchemaValidationError as fallback_error:
            logger.warning(
                "deep-tailor fallback also failed (%s) — using rebuilt resume",
                fallback_error,
            )
            return _fallback_from_rebuilt(
                rebuilt_resume=rebuilt_resume,
                resume_facts=source_facts,
                strategy=strategy,
            )

    assert raw is not None
    resume = validate_tailored_resume(raw["tailored_resume"])
    resume_dict = resume.to_dict()
    # Preserve contact from LLM or source
    if isinstance(raw["tailored_resume"], dict) and isinstance(
        raw["tailored_resume"].get("contact"), dict
    ):
        resume_dict["contact"] = dict(raw["tailored_resume"]["contact"])

    # Keep LLM skill selection when present — overwriting with the rebuilder
    # list erased job-specific emphasis (always the same source skill set).
    # Fall back to the rebuilt grouping only when the model omitted skills.
    if not (resume_dict.get("skills") or []):
        if rebuilt_resume.get("skills"):
            resume_dict["skills"] = rebuilt_resume["skills"]
    # Never trust LLM category prefixes (REST/WebSockets under Database, etc.).
    from intelligent_tailoring.skill_taxonomy import normalize_skill_lines

    resume_dict["skills"] = normalize_skill_lines(
        list(resume_dict.get("skills") or []),
        emphasize=list(
            strategy.get("propagate_terms")
            or strategy.get("skills_to_emphasize")
            or []
        ),
        job_family=str(strategy.get("job_family") or ""),
        category_order=list(strategy.get("skill_category_order") or []),
    )
    if rebuilt_resume.get("experience"):
        # Merge: keep LLM bullet text but enforce score-based order per role
        _merge_experience_order(resume_dict, rebuilt_resume)
    if rebuilt_resume.get("projects"):
        _merge_project_order(resume_dict, rebuilt_resume)

    resume_dict = stamp_ids_on_resume(resume_dict, source_facts=source_facts)

    # Deterministic validation + repair after merge (IDs / contact / fullness / JD leak)
    post_report = validate_structured_resume(
        resume_dict,
        source_facts=source_facts,
        enforce_fullness=True,
        require_summary=False,
        jd_text=jd_blob,
    )
    if not post_report.passed:
        logger.warning(
            "Agent 2 post-merge validation failed (%s) — applying deterministic repair",
            post_report.error_codes(),
        )
        resume_dict = repair_structured_resume(resume_dict, source_facts=source_facts)
        # Re-check; keep repaired output even if soft summary issues remain
        post_report = validate_structured_resume(
            resume_dict,
            source_facts=source_facts,
            enforce_fullness=True,
            require_summary=False,
            jd_text=jd_blob,
        )
        if post_report.passed or post_report.structured:
            try:
                resume_dict = structured_to_pipeline_resume(
                    to_structured_resume(resume_dict, source_facts=source_facts)
                )
            except Exception:  # noqa: BLE001
                pass

    from intelligent_tailoring.requirement_coverage import preserve_contact

    resume_dict = preserve_contact(
        resume_dict,
        source_contact=source_facts.get("contact")
        if isinstance(source_facts.get("contact"), dict)
        else {},
        resume_facts=source_facts,
    )
    resume_dict = stamp_ids_on_resume(resume_dict, source_facts=source_facts)

    raw["change_log"] = sanitize_change_log_raw(raw.get("change_log"))
    change_log = [
        validate_change_log_item(item, index=i).to_dict()
        for i, item in enumerate(raw.get("change_log") or [])
    ]
    return {
        "tailored_resume": resume_dict,
        "change_log": change_log,
        "matched_requirements": [
            str(x).strip()
            for x in (raw.get("matched_requirements") or [])
            if str(x).strip()
        ],
        "missing_requirements": [
            str(x).strip()
            for x in (raw.get("missing_requirements") or [])
            if str(x).strip()
        ],
        "removed_or_deprioritized_content": [
            str(x).strip()
            for x in (raw.get("removed_or_deprioritized_content") or [])
            if str(x).strip()
        ],
        "ats_keywords_added": [
            str(x).strip()
            for x in (raw.get("ats_keywords_added") or [])
            if str(x).strip()
        ],
        "_from_cache": bool(raw.get("_from_cache")),
        "_content_validation": raw.get("_content_validation"),
        "_structured_validation": post_report.to_dict(),
    }


def _entry_ids_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when both entries carry the same stable source id."""
    for key in ("source_entry_id", "id"):
        va = str(a.get(key) or "").strip()
        vb = str(b.get(key) or "").strip()
        if va and vb and va == vb and not va.startswith("unmapped_"):
            return True
    return False


def _org_near_match(a: str, b: str) -> bool:
    """True for equal/contained org names or near-typos (Tribe/Trike, Tel Hai drift)."""
    from difflib import SequenceMatcher

    from intelligent_tailoring.structural_integrity import _norm

    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return True
    if na == nb or na in nb or nb in na:
        return True
    # Token overlap (Tel Hai University vs Tel Hai)
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return True
    # Near-typo / hallucination rename (Tribe↔Trike, Yoda↔Tribe short forms)
    ratio = SequenceMatcher(None, na, nb).ratio()
    if ratio >= 0.72:
        return True
    # Shared distinctive token of length >= 4
    shared = {t for t in (ta & tb) if len(t) >= 4}
    if shared:
        return True
    return False


def _role_soft_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two experience entries refer to the same real position.

    Prefer stable source ids. When titles soft-match, allow near-typo company
    drift so invented employers can be overwritten from the rebuilt source.
    """
    from intelligent_tailoring.structural_integrity import _titles_soft_match, _norm

    if _entry_ids_match(a, b):
        return True
    title_a = str(a.get("title") or "")
    title_b = str(b.get("title") or "")
    if not _titles_soft_match(title_a, title_b):
        return False
    if _org_near_match(str(a.get("company") or ""), str(b.get("company") or "")):
        return True
    # Distinctive academic/tutor titles: allow full company rewrite (Tribe→Yoda).
    distinctive = ("capstone", "tutor", "thesis", "coursework", "teaching assistant")
    blob = f"{_norm(title_a)} {_norm(title_b)}"
    return any(token in blob for token in distinctive)


def _project_soft_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    from intelligent_tailoring.structural_integrity import _titles_soft_match

    if _entry_ids_match(a, b):
        return True
    return _titles_soft_match(str(a.get("name") or ""), str(b.get("name") or ""))


def _lock_experience_identity(entry: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Overwrite immutable identity fields from the rebuilt/source entry."""
    out = dict(entry)
    for key in ("company", "title", "dates", "date"):
        src_val = str(source.get(key) or "").strip()
        if src_val:
            out[key] = source.get(key)
    sid = str(source.get("id") or source.get("source_entry_id") or "").strip()
    if sid:
        out["id"] = sid
        out["source_entry_id"] = sid
    return out


def _lock_project_identity(entry: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Overwrite immutable project identity fields from the rebuilt/source entry."""
    out = dict(entry)
    src_name = str(source.get("name") or "").strip()
    if src_name:
        out["name"] = source.get("name")
    for key in ("dates", "date"):
        src_val = str(source.get(key) or "").strip()
        if src_val:
            out[key] = source.get(key)
    sid = str(source.get("id") or source.get("source_entry_id") or "").strip()
    if sid:
        out["id"] = sid
        out["source_entry_id"] = sid
    return out


def _merge_bullet_lists(preferred: list[str], fallback: list[str]) -> list[str]:
    """Keep preferred wording; fill gaps from fallback; near-dedupe."""
    from intelligent_tailoring.structural_integrity import strip_bullet_markers
    from intelligent_tailoring.services.one_page_compressor import _dedupe_similar

    pref = [strip_bullet_markers(b) for b in preferred if str(b).strip()]
    fb = [strip_bullet_markers(b) for b in fallback if str(b).strip()]
    ordered: list[str] = []
    used: set[int] = set()
    for rb_text in fb:
        rb_low = rb_text.lower()
        matched = False
        for i, tb_text in enumerate(pref):
            if i in used:
                continue
            if rb_low in tb_text.lower() or tb_text.lower() in rb_low:
                ordered.append(tb_text)
                used.add(i)
                matched = True
                break
        if not matched:
            ordered.append(rb_text)
    for i, tb_text in enumerate(pref):
        if i not in used:
            ordered.append(tb_text)
    return _dedupe_similar(ordered) if ordered else (pref or fb)


def _merge_experience_order(tailored: dict[str, Any], rebuilt: dict[str, Any]) -> None:
    """Merge LLM experience with rebuilt source by identity, never by index.

    Index-zip previously paired Capstone with Tutor when order drifted, then
    appended the unused Capstone — producing duplicate entries and cross-entry
    bullets. Match on title+company, consolidate duplicates, then append only
    truly omitted rebuilt roles.
    """
    from intelligent_tailoring.structural_integrity import (
        consolidate_experience_entries,
        strip_bullet_markers,
        validate_and_repair_resume_structure,
    )

    rebuilt_exp = [e for e in (rebuilt.get("experience") or []) if isinstance(e, dict)]
    tailored_exp = [e for e in (tailored.get("experience") or []) if isinstance(e, dict)]
    if not rebuilt_exp:
        if tailored_exp:
            # Still consolidate any LLM-side duplicates.
            tailored["experience"] = consolidate_experience_entries(tailored_exp)
        return
    if not tailored_exp:
        tailored["experience"] = consolidate_experience_entries(rebuilt_exp)
        return

    # Collapse LLM duplicates first so Capstone×2 becomes one entry.
    tailored_exp = consolidate_experience_entries(tailored_exp)

    merged: list[dict[str, Any]] = []
    used_rebuilt: set[int] = set()
    used_tailored: set[int] = set()

    # Walk rebuilt order (score-ranked source of truth for sequencing).
    for j, rb in enumerate(rebuilt_exp):
        match_idx = None
        for i, tb in enumerate(tailored_exp):
            if i in used_tailored:
                continue
            if _role_soft_match(tb, rb):
                match_idx = i
                break
        rb_bullets = [
            strip_bullet_markers(str(b))
            for b in (rb.get("bullets") or [])
            if str(b).strip()
        ]
        if match_idx is None:
            if rb_bullets:
                merged.append({**rb, "bullets": rb_bullets})
                used_rebuilt.add(j)
            continue
        used_tailored.add(match_idx)
        used_rebuilt.add(j)
        tb = tailored_exp[match_idx]
        tb_bullets = [
            strip_bullet_markers(str(b))
            for b in (tb.get("bullets") or [])
            if str(b).strip()
        ]
        bullets = _merge_bullet_lists(tb_bullets, rb_bullets)
        if not bullets:
            continue
        entry = _lock_experience_identity(dict(tb), rb)
        entry["bullets"] = bullets
        merged.append(entry)

    # Tailored-only roles the rebuilt list omitted (still with bullets).
    for i, tb in enumerate(tailored_exp):
        if i in used_tailored:
            continue
        bullets = [
            strip_bullet_markers(str(b))
            for b in (tb.get("bullets") or [])
            if str(b).strip()
        ]
        if bullets:
            merged.append({**tb, "bullets": bullets})

    tailored["experience"] = consolidate_experience_entries(merged)
    # Full structural pass strips markers / misplaced headings early.
    repaired = validate_and_repair_resume_structure(
        {"experience": tailored["experience"], "projects": tailored.get("projects") or []}
    )
    tailored["experience"] = repaired["experience"]


def _merge_project_order(tailored: dict[str, Any], rebuilt: dict[str, Any]) -> None:
    """Merge projects by identity (name), consolidating duplicates."""
    from intelligent_tailoring.structural_integrity import (
        consolidate_project_entries,
        strip_bullet_markers,
        validate_and_repair_resume_structure,
    )

    rebuilt_projects = [p for p in (rebuilt.get("projects") or []) if isinstance(p, dict)]
    tailored_projects = [p for p in (tailored.get("projects") or []) if isinstance(p, dict)]
    if not rebuilt_projects:
        if tailored_projects:
            tailored["projects"] = consolidate_project_entries(tailored_projects)
        return
    if not tailored_projects:
        tailored["projects"] = consolidate_project_entries(rebuilt_projects)
        return

    tailored_projects = consolidate_project_entries(tailored_projects)

    ordered: list[dict[str, Any]] = []
    used_tailored: set[int] = set()

    for rb in rebuilt_projects:
        match_idx = None
        for i, tb in enumerate(tailored_projects):
            if i in used_tailored:
                continue
            if _project_soft_match(tb, rb):
                match_idx = i
                break
        rb_bullets = [
            strip_bullet_markers(str(b))
            for b in (rb.get("bullets") or [])
            if str(b).strip()
        ]
        rb_desc = strip_bullet_markers(str(rb.get("description") or ""))
        if match_idx is None:
            if rb_bullets or rb_desc:
                ordered.append({**rb, "bullets": rb_bullets, "description": rb_desc})
            continue
        used_tailored.add(match_idx)
        tb = tailored_projects[match_idx]
        tb_bullets = [
            strip_bullet_markers(str(b))
            for b in (tb.get("bullets") or [])
            if str(b).strip()
        ]
        tb_desc = strip_bullet_markers(str(tb.get("description") or ""))
        entry = _lock_project_identity(dict(tb), rb)
        # Prefer tailored wording; restore from rebuilt when empty.
        if tb_bullets:
            entry["bullets"] = _merge_bullet_lists(tb_bullets, rb_bullets)
        else:
            entry["bullets"] = rb_bullets
        entry["description"] = tb_desc or rb_desc
        if not entry.get("technologies") and rb.get("technologies"):
            entry["technologies"] = list(rb.get("technologies") or [])
        if not entry.get("bullets") and not str(entry.get("description") or "").strip():
            continue
        ordered.append(entry)

    for i, tb in enumerate(tailored_projects):
        if i in used_tailored:
            continue
        bullets = [
            strip_bullet_markers(str(b))
            for b in (tb.get("bullets") or [])
            if str(b).strip()
        ]
        desc = strip_bullet_markers(str(tb.get("description") or ""))
        if bullets or desc:
            ordered.append({**tb, "bullets": bullets, "description": desc})

    tailored["projects"] = consolidate_project_entries(ordered)
    repaired = validate_and_repair_resume_structure(
        {"experience": tailored.get("experience") or [], "projects": tailored["projects"]}
    )
    tailored["projects"] = repaired["projects"]
