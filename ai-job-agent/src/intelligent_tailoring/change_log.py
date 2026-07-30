"""Deterministic change log — compare source vs final validated resume.

Never trust the LLM to describe what changed. Diff the canonical source
against the final TailoredResume after claim/scope validation.
"""

from __future__ import annotations

from typing import Any

_REASON_TEMPLATES = {
    ("summary", "rewritten"): "Summary rewritten from source evidence for the target job",
    ("summary", "added"): "Summary added from source evidence for the target job",
    ("skills", "reordered"): "Skills reordered/grouped by job relevance",
    ("skills", "deprioritized"): "Skill deprioritized for this target job",
    ("skills", "added"): "Skill emphasized for this target job",
    ("experience", "rewritten"): "Experience bullet rephrased from source evidence",
    ("experience", "added"): "Experience bullet selected from source evidence",
    ("experience", "deprioritized"): "Bullet deprioritized for this target job",
    ("projects", "rewritten"): "Project bullet rephrased from source evidence",
    ("projects", "added"): "Project bullet selected from source evidence",
    ("projects", "deprioritized"): "Project detail deprioritized for this target job",
    ("projects", "reordered"): "Projects reordered by relevance to target job",
    ("education", "preserved"): "Education preserved from source resume",
}


def _reason(section: str, change_type: str) -> str:
    return _REASON_TEMPLATES.get(
        (section, change_type),
        f"{section.title()} {change_type} based on source evidence",
    )


def _item(
    *,
    section: str,
    change_type: str,
    original_text: str = "",
    new_text: str = "",
    supporting_evidence: str = "",
    related_job_requirement: str = "",
    source_fact_ids: list[str] | None = None,
) -> dict[str, Any]:
    evidence = supporting_evidence or original_text or (new_text[:120] if new_text else "")
    return {
        "section": section,
        "change_type": change_type,
        "original_text": original_text,
        "new_text": new_text,
        "reason": _reason(section, change_type),
        "supporting_evidence": evidence,
        "related_job_requirement": related_job_requirement,
        "source_fact_ids": list(source_fact_ids or []),
        "evidence_type": "Explicit",
        "inference_category": "Explicit",
        "confidence_score": 1.0,
        "confidence": 1.0,
    }


def build_deterministic_change_log(
    *,
    baseline_resume: dict[str, Any],
    final_resume: dict[str, Any],
    prior_llm_change_log: list[dict[str, Any]] | None = None,
    evidence_map: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Produce structured change_log items that match the actual final resume.

    ``prior_llm_change_log`` is ignored for content authority — kept only as a
    deprecated parameter for call-site compatibility.
    """
    _ = prior_llm_change_log  # intentionally unused
    changes: list[dict[str, Any]] = []

    def _related_for(text: str) -> str:
        needle = (text or "").lower()
        for entry in evidence_map or []:
            req = str(entry.get("requirement") or "").strip()
            if not req:
                continue
            status = str(entry.get("candidate_status") or "")
            if status not in ("MATCH", "PARTIAL"):
                continue
            if req.lower() in needle or any(
                tok and tok in needle
                for tok in req.lower().replace("/", " ").split()
                if len(tok) > 3
            ):
                return req
        return ""

    # Summary
    base_summary = str(
        baseline_resume.get("professional_summary") or baseline_resume.get("summary") or ""
    ).strip()
    final_summary = str(
        final_resume.get("professional_summary") or final_resume.get("summary") or ""
    ).strip()
    if final_summary and final_summary != base_summary:
        changes.append(
            _item(
                section="summary",
                change_type="rewritten" if base_summary else "added",
                original_text=base_summary,
                new_text=final_summary,
                supporting_evidence=base_summary or final_summary[:120],
                related_job_requirement=_related_for(final_summary),
            )
        )

    # Skills reorder / rewrite
    base_skills = [
        str(s).strip() for s in (baseline_resume.get("skills") or []) if str(s).strip()
    ]
    final_skills = [
        str(s).strip() for s in (final_resume.get("skills") or []) if str(s).strip()
    ]
    if final_skills and final_skills != base_skills:
        changes.append(
            _item(
                section="skills",
                change_type="reordered",
                original_text=" · ".join(base_skills[:8]),
                new_text=" · ".join(final_skills[:8]),
                supporting_evidence=" · ".join(base_skills[:8]),
            )
        )
        removed = [s for s in base_skills if s not in final_skills]
        for s in removed[:6]:
            changes.append(
                _item(
                    section="skills",
                    change_type="deprioritized",
                    original_text=s,
                    new_text="",
                    supporting_evidence=s,
                )
            )
        added = [s for s in final_skills if s not in base_skills]
        for s in added[:6]:
            changes.append(
                _item(
                    section="skills",
                    change_type="added",
                    original_text="",
                    new_text=s,
                    supporting_evidence=s,
                    related_job_requirement=_related_for(s),
                )
            )

    # Experience bullets
    base_exp = [
        e for e in (baseline_resume.get("experience") or []) if isinstance(e, dict)
    ]
    final_exp = [
        e for e in (final_resume.get("experience") or []) if isinstance(e, dict)
    ]
    for i, final_entry in enumerate(final_exp):
        base_entry = base_exp[i] if i < len(base_exp) else {}
        base_bullets = [str(b).strip() for b in (base_entry.get("bullets") or [])]
        final_bullets = [str(b).strip() for b in (final_entry.get("bullets") or [])]
        for nb in final_bullets:
            if nb in base_bullets:
                continue
            original = ""
            for bb in base_bullets:
                if bb and (bb.lower() in nb.lower() or nb.lower() in bb.lower()):
                    original = bb
                    break
            changes.append(
                _item(
                    section="experience",
                    change_type="rewritten" if original else "added",
                    original_text=original,
                    new_text=nb,
                    supporting_evidence=original or nb[:120],
                    related_job_requirement=_related_for(nb),
                )
            )
        for bb in base_bullets:
            if bb and bb not in final_bullets and not any(
                bb.lower() in nb.lower() for nb in final_bullets
            ):
                changes.append(
                    _item(
                        section="experience",
                        change_type="deprioritized",
                        original_text=bb,
                        new_text="",
                        supporting_evidence=bb,
                    )
                )

    # Projects
    base_proj = [
        p for p in (baseline_resume.get("projects") or []) if isinstance(p, dict)
    ]
    final_proj = [
        p for p in (final_resume.get("projects") or []) if isinstance(p, dict)
    ]
    base_names = [str(p.get("name") or "") for p in base_proj]
    final_names = [str(p.get("name") or "") for p in final_proj]
    if final_names and final_names != base_names:
        changes.append(
            _item(
                section="projects",
                change_type="reordered",
                original_text=" → ".join(base_names),
                new_text=" → ".join(final_names),
                supporting_evidence=" → ".join(base_names),
            )
        )

    base_by_name = {str(p.get("name") or "").lower(): p for p in base_proj}
    for proj in final_proj:
        name = str(proj.get("name") or "")
        base_entry = base_by_name.get(name.lower(), {})
        base_bullets = [str(b).strip() for b in (base_entry.get("bullets") or [])]
        final_bullets = [str(b).strip() for b in (proj.get("bullets") or [])]
        for nb in final_bullets:
            if nb in base_bullets:
                continue
            original = ""
            for bb in base_bullets:
                if bb and (bb.lower() in nb.lower() or nb.lower() in bb.lower()):
                    original = bb
                    break
            changes.append(
                _item(
                    section="projects",
                    change_type="rewritten" if original else "added",
                    original_text=original,
                    new_text=nb,
                    supporting_evidence=original or nb[:120],
                    related_job_requirement=_related_for(nb),
                )
            )
        for bb in base_bullets:
            if bb and bb not in final_bullets and not any(
                bb.lower() in nb.lower() for nb in final_bullets
            ):
                changes.append(
                    _item(
                        section="projects",
                        change_type="deprioritized",
                        original_text=bb,
                        new_text="",
                        supporting_evidence=bb,
                    )
                )

        base_desc = str(base_entry.get("description") or "").strip()
        final_desc = str(proj.get("description") or "").strip()
        if final_desc and final_desc != base_desc:
            changes.append(
                _item(
                    section="projects",
                    change_type="rewritten" if base_desc else "added",
                    original_text=base_desc,
                    new_text=final_desc,
                    supporting_evidence=base_desc or final_desc[:120],
                    related_job_requirement=_related_for(final_desc),
                )
            )

    # Education — only note if missing from final
    base_edu = [
        e for e in (baseline_resume.get("education") or []) if isinstance(e, dict)
    ]
    final_edu = [
        e for e in (final_resume.get("education") or []) if isinstance(e, dict)
    ]
    if base_edu and not final_edu:
        for entry in base_edu[:3]:
            label = " — ".join(
                x
                for x in (
                    str(entry.get("degree") or "").strip(),
                    str(entry.get("institution") or "").strip(),
                )
                if x
            )
            if label:
                changes.append(
                    _item(
                        section="education",
                        change_type="deprioritized",
                        original_text=label,
                        new_text="",
                        supporting_evidence=label,
                    )
                )

    return changes
