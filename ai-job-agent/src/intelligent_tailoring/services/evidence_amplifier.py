"""Maximize use of existing resume evidence before writing.

Profession-agnostic. Never invents facts — only surfaces, ranks, and
propagates evidence already present in the knowledge base / resume facts.
"""

from __future__ import annotations

import re
from typing import Any


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_entry_evidence(entry: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Pull responsibilities, technologies, achievements from one entry."""
    bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
    desc = str(entry.get("description") or "").strip()
    blob = " ".join([desc] + bullets)
    low = blob.lower()

    tech_cues = re.findall(
        r"\b(?:python|java(?:script|)|typescript|react|angular|vue|node\.?js|"
        r"django|flask|fastapi|spring|rails|\.net|aws|azure|gcp|docker|"
        r"kubernetes|postgres(?:ql)?|mysql|mongodb|redis|sql|rest|graphql|"
        r"salesforce|excel|ehr|emr|sap|tableau|power\s*bi|tensorflow|"
        r"pytorch|kafka|terraform|jenkins|git)\b",
        low,
        flags=re.I,
    )
    architecture = [
        b
        for b in bullets
        if any(
            w in b.lower()
            for w in (
                "architect",
                "design",
                "schema",
                "microservice",
                "pipeline",
                "infrastructure",
                "workflow",
                "system",
            )
        )
    ]
    impact = [
        b
        for b in bullets
        if re.search(
            r"\b(\d+%|\$\d+|reduced|increased|improved|saved|grew|raised|cut)\b",
            b,
            re.I,
        )
    ]
    challenges = [
        b
        for b in bullets
        if any(
            w in b.lower()
            for w in ("debug", "troubleshoot", "resolve", "migrat", "scale", "incident")
        )
    ]

    return {
        "kind": kind,
        "name": str(entry.get("name") or entry.get("company") or entry.get("title") or ""),
        "title": str(entry.get("title") or ""),
        "responsibilities": bullets[:8],
        "technologies": list(dict.fromkeys(t.lower() for t in tech_cues))[:12],
        "architecture": architecture[:4],
        "achievements": impact[:4],
        "challenges": challenges[:4],
        "description": desc,
        "bullet_count": len(bullets),
        "relevance_hint": blob[:240],
    }


def build_evidence_inventory(resume_facts: dict[str, Any]) -> dict[str, Any]:
    """Inventory every experience/project so writers maximize available evidence."""
    experiences = []
    for entry in resume_facts.get("experience_roles") or resume_facts.get("experience") or []:
        if isinstance(entry, dict):
            experiences.append(extract_entry_evidence(entry, kind="experience"))
    projects = []
    for entry in resume_facts.get("projects") or []:
        if isinstance(entry, dict):
            projects.append(extract_entry_evidence(entry, kind="project"))

    thin_projects = [
        p["name"] for p in projects if p["bullet_count"] < 2 and p["name"]
    ]
    rich_projects = [
        p["name"] for p in projects if p["bullet_count"] >= 3 and p["name"]
    ]
    return {
        "experiences": experiences,
        "projects": projects,
        "thin_projects": thin_projects,
        "rich_projects": rich_projects,
        "all_technologies": sorted(
            {
                t
                for block in experiences + projects
                for t in (block.get("technologies") or [])
            }
        ),
    }


def score_requirement_support(
    evidence_map: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify each requirement as Explicit / Strongly / Weakly / Unsupported."""
    out: list[dict[str, Any]] = []
    for entry in evidence_map or []:
        strength = str(
            entry.get("evidence_strength") or entry.get("inference_category") or ""
        )
        status = str(entry.get("candidate_status") or "")
        low = strength.lower()
        if "explicit" in low or (status == "MATCH" and "infer" not in low):
            support = "Explicit"
        elif "strong" in low or status == "PARTIAL":
            support = "Strongly Supported"
        elif "weak" in low:
            support = "Weakly Supported"
        else:
            support = "Unsupported"
        out.append(
            {
                "requirement": str(entry.get("requirement") or ""),
                "support": support,
                "importance": str(entry.get("importance") or "soft"),
                "supporting_evidence": str(entry.get("supporting_evidence") or ""),
                "must_highlight": support in ("Explicit", "Strongly Supported")
                and str(entry.get("importance") or "") in ("hard", "soft"),
            }
        )
    return out


def build_highlight_plan(
    *,
    evidence_map: list[dict[str, Any]],
    skills_to_emphasize: list[str],
) -> dict[str, Any]:
    """Decide which supported requirements must appear across sections.

    Interview-first: identify the strongest evidenced reasons to interview,
    not maximum keyword coverage.
    """
    from intelligent_tailoring.interview_philosophy import select_top_interview_reasons

    support = score_requirement_support(evidence_map)
    must = [
        s
        for s in support
        if s.get("must_highlight") and s.get("importance") == "hard"
    ]
    soft_highlight = [
        s for s in support if s.get("must_highlight") and s.get("importance") == "soft"
    ]
    highlight_terms = []
    for item in must + soft_highlight:
        req = str(item.get("requirement") or "").strip()
        if req and req not in highlight_terms:
            highlight_terms.append(req)
    for skill in skills_to_emphasize:
        if skill and skill not in highlight_terms:
            highlight_terms.append(skill)

    plan = {
        "requirement_support": support,
        "must_highlight": [m["requirement"] for m in must if m.get("requirement")],
        "soft_highlight": [
            m["requirement"] for m in soft_highlight if m.get("requirement")
        ],
        "propagate_terms": highlight_terms[:16],
        "unsupported_hard": [
            s["requirement"]
            for s in support
            if s.get("support") == "Unsupported" and s.get("importance") == "hard"
        ],
    }
    plan["top_interview_reasons"] = select_top_interview_reasons(
        highlight_plan=plan,
        evidence_map=evidence_map,
        strategy={"skills_to_emphasize": skills_to_emphasize},
        limit=3,
    )
    # Lead propagate_terms with the top interview reasons
    lead = list(plan["top_interview_reasons"])
    for term in highlight_terms:
        if term not in lead:
            lead.append(term)
    plan["propagate_terms"] = lead[:16]
    return plan


def expand_thin_projects_from_facts(
    resume_facts: dict[str, Any],
    *,
    kb: Any = None,
    highlight_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Attach unused same-entry KB facts to thin projects — never cross-entry."""
    facts = dict(resume_facts)
    projects = [dict(p) for p in (facts.get("projects") or []) if isinstance(p, dict)]
    if not projects or kb is None or not hasattr(kb, "facts"):
        facts["projects"] = projects
        return facts

    highlight = [_norm(t) for t in (highlight_terms or []) if t]
    existing = {
        _norm(b)
        for p in projects
        for b in (p.get("bullets") or [])
    }

    for idx, project in enumerate(projects):
        bullets = [str(b).strip() for b in (project.get("bullets") or []) if str(b).strip()]
        if len(bullets) >= 3:
            continue
        name = str(project.get("name") or "")
        candidates: list[tuple[int, str]] = []
        for fact in kb.facts:
            section = str(getattr(fact, "source_section", "") or "")
            entry_id = str(getattr(fact, "source_entry_id", "") or "")
            org = str(getattr(fact, "organization", "") or "")
            text = str(getattr(fact, "original_text", "") or "").strip()
            if not text or _norm(text) in existing:
                continue
            same_entry = (
                entry_id == f"project_{idx}"
                or (section == "projects" and org and org == name)
                or (name and name.lower() in text.lower())
            )
            if not same_entry:
                continue
            score = 10
            low = text.lower()
            for term in highlight:
                if term and term in low:
                    score += 20
            # Prefer achievement / architecture-ish bullets
            if any(w in low for w in ("design", "implement", "built", "integrat", "scal")):
                score += 5
            candidates.append((score, text))
        candidates.sort(key=lambda x: -x[0])
        for _, text in candidates:
            if len(bullets) >= 4:
                break
            if _norm(text) in existing:
                continue
            bullets.append(text)
            existing.add(_norm(text))
        project["bullets"] = bullets
        # Strengthen stub descriptions from first bullet when thin
        desc = str(project.get("description") or "").strip()
        if (not desc or len(desc.split()) <= 4) and bullets:
            project["description"] = bullets[0].rstrip(".") + "."
    facts["projects"] = projects
    return facts


def ensure_skill_propagation(
    skills: list[str],
    *,
    propagate_terms: list[str],
    resume_text: str,
) -> list[str]:
    """Ensure highlighted evidenced terms appear in the skills section.

    Only adds terms already present in resume_text. Never invents skills.
    """
    from intelligent_tailoring.skill_taxonomy import normalize_skill_lines

    source = (resume_text or "").lower()
    existing_atoms: set[str] = set()
    flat: list[str] = []
    for line in skills or []:
        text = str(line)
        if ":" in text:
            text = text.split(":", 1)[1]
        for part in text.split(","):
            atom = part.strip()
            if atom:
                flat.append(atom)
                existing_atoms.add(atom.lower())

    for term in propagate_terms or []:
        t = str(term).strip()
        if not t or len(t) < 2:
            continue
        if t.lower() in existing_atoms:
            continue
        if t.lower() not in source:
            continue
        # Avoid stuffing long requirement sentences into skills
        if len(t.split()) > 4:
            continue
        flat.append(t)
        existing_atoms.add(t.lower())

    return normalize_skill_lines(flat, emphasize=list(propagate_terms or []))


def apply_evidence_amplification(
    *,
    resume_facts: dict[str, Any],
    evidence_map: list[dict[str, Any]],
    strategy: dict[str, Any],
    kb: Any = None,
    resume_text: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return updated resume_facts + enrichment payload for strategy/writer."""
    inventory = build_evidence_inventory(resume_facts)
    highlight = build_highlight_plan(
        evidence_map=evidence_map,
        skills_to_emphasize=list(strategy.get("skills_to_emphasize") or []),
    )
    facts = expand_thin_projects_from_facts(
        resume_facts,
        kb=kb,
        highlight_terms=highlight.get("propagate_terms") or [],
    )
    skills = list(facts.get("display_skills") or facts.get("skills") or [])
    facts["skills"] = ensure_skill_propagation(
        [str(s) for s in skills],
        propagate_terms=highlight.get("propagate_terms") or [],
        resume_text=resume_text or str(facts.get("raw_text") or ""),
    )
    facts["display_skills"] = list(facts["skills"])

    enrichment = {
        "evidence_inventory": inventory,
        "highlight_plan": highlight,
        "must_highlight_in_summary": highlight.get("must_highlight") or [],
        "propagate_terms": highlight.get("propagate_terms") or [],
        "top_interview_reasons": highlight.get("top_interview_reasons") or [],
        "thin_projects_expanded": inventory.get("thin_projects") or [],
    }
    return facts, enrichment
