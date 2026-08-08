"""Maximize use of existing resume evidence before writing.

Profession-agnostic. Never invents facts — only surfaces, ranks, and
propagates evidence already present in the knowledge base / resume facts.
"""

from __future__ import annotations

import re
from typing import Any


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


_SOFT_COMPETENCY_CUES: dict[str, tuple[str, ...]] = {
    "problem_solving": ("problem", "troubleshoot", "debug", "root cause", "diagnos", "resolv"),
    "leadership": ("led", "managed", "mentored", "supervised", "coached", "directed"),
    "ownership": ("owned", "ownership", "accountable", "end-to-end", "drove", "championed"),
    "communication": ("present", "communicat", "wrote", "drafted", "negotiat", "taught"),
    "learning_ability": ("learned", "upskill", "self-taught", "adopted", "studied"),
    "decision_making": ("decided", "chose", "selected", "trade-off", "prioritiz"),
    "architecture": ("architect", "system design", "schema", "microservice", "infrastructure"),
    "debugging": ("debug", "fix", "incident", "defect", "bug"),
    "optimization": ("optimiz", "performance", "latency", "throughput", "efficiency"),
    "customer_interaction": ("customer", "client", "patient", "guest", "account"),
    "teaching": ("train", "teach", "tutor", "instruct", "onboard", "mentor"),
    "automation": ("automat", "script", "ci/cd", "pipeline", "orchestrat"),
    "scalability": ("scalab", "high-traffic", "distributed", "load"),
    "testing": ("test", "qa", "coverage", "regression", "validation"),
    "monitoring": ("monitor", "observability", "alert", "telemetry", "on-call"),
    "documentation": ("document", "runbook", "playbook", "spec", "wiki"),
    "collaboration": ("cross-functional", "collaborat", "stakeholder", "partner"),
    "initiative": ("initiated", "proposed", "volunteered", "pioneered", "proactive"),
}


def extract_entry_evidence(entry: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Pull responsibilities, technologies, soft evidence, achievements from one entry."""
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

    soft_competencies: list[str] = []
    soft_evidence: dict[str, list[str]] = {}
    for label, cues in _SOFT_COMPETENCY_CUES.items():
        matched = [b for b in bullets if any(c in b.lower() for c in cues)]
        if matched or any(c in low for c in cues):
            soft_competencies.append(label)
            if matched:
                soft_evidence[label] = matched[:3]

    return {
        "kind": kind,
        "name": str(entry.get("name") or entry.get("company") or entry.get("title") or ""),
        "title": str(entry.get("title") or ""),
        "responsibilities": bullets[:8],
        "technologies": list(dict.fromkeys(t.lower() for t in tech_cues))[:12],
        "architecture": architecture[:4],
        "achievements": impact[:4],
        "challenges": challenges[:4],
        "soft_competencies": soft_competencies,
        "soft_evidence": soft_evidence,
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
    soft_all = sorted(
        {
            c
            for block in experiences + projects
            for c in (block.get("soft_competencies") or [])
        }
    )
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
        "soft_competencies": soft_all,
        "transferable_strengths": soft_all[:12],
    }


def score_requirement_support(
    evidence_map: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify each requirement into Explicit / Strong Supporting / Transferable / No Evidence."""
    from intelligent_tailoring.hiring_intent import classify_requirement_support_tier

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
        tier = classify_requirement_support_tier(support)
        # Transferable: weak inference that still has supporting text
        if support == "Weakly Supported" and str(entry.get("supporting_evidence") or "").strip():
            tier = "Transferable Evidence"
        out.append(
            {
                "requirement": str(entry.get("requirement") or ""),
                "support": support,
                "support_tier": tier,
                "importance": str(entry.get("importance") or "soft"),
                "supporting_evidence": str(entry.get("supporting_evidence") or ""),
                "must_highlight": support in ("Explicit", "Strongly Supported")
                and str(entry.get("importance") or "") in ("hard", "soft"),
                "surface_if_present": tier
                in (
                    "Explicit Evidence",
                    "Strong Supporting Evidence",
                    "Transferable Evidence",
                ),
            }
        )
    return out


def build_highlight_plan(
    *,
    evidence_map: list[dict[str, Any]],
    skills_to_emphasize: list[str],
    soft_competencies: list[str] | None = None,
    hiring_priorities: list[str] | None = None,
) -> dict[str, Any]:
    """Decide which supported requirements must appear across sections.

    Interview-first: identify the strongest evidenced reasons to interview,
    not maximum keyword coverage. Surfaces transferable soft evidence when
    it aligns with hiring priorities — never invents.
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
    transferable = [
        s
        for s in support
        if s.get("support_tier") == "Transferable Evidence" and s.get("requirement")
    ]
    def _clean_term(value: str) -> str:
        text = re.sub(r"\s+", " ", (value or "").strip()).strip(" \t\r\n,;:.-")
        text = re.sub(
            r"^(required|responsibilities|requirements|preferred|qualifications)\s*:?\s*",
            "",
            text,
            flags=re.I,
        ).strip(" \t\r\n,;:.-")
        return text

    highlight_terms = []
    for item in must + soft_highlight:
        req = _clean_term(str(item.get("requirement") or ""))
        if req and req not in highlight_terms and len(req) > 2:
            highlight_terms.append(req)
    for skill in skills_to_emphasize:
        cleaned = _clean_term(str(skill))
        if cleaned and cleaned not in highlight_terms and len(cleaned) > 2:
            highlight_terms.append(cleaned)
    # Fold evidenced soft competencies that match hiring priorities / soft reqs
    priority_blob = " ".join(
        str(x).lower()
        for x in list(hiring_priorities or [])
        + [s.get("requirement") or "" for s in soft_highlight + transferable]
    )
    for comp in soft_competencies or []:
        label = str(comp).strip()
        if not label or label in highlight_terms:
            continue
        token = label.lower().split()[0] if label else ""
        if token and len(token) > 3 and token in priority_blob:
            highlight_terms.append(label)
        elif not priority_blob and label not in highlight_terms:
            # Still surface top soft competencies as secondary propagate terms
            if len(highlight_terms) < 14:
                highlight_terms.append(label)

    plan = {
        "requirement_support": support,
        "must_highlight": [m["requirement"] for m in must if m.get("requirement")],
        "soft_highlight": [
            m["requirement"] for m in soft_highlight if m.get("requirement")
        ],
        "transferable_highlight": [
            m["requirement"] for m in transferable if m.get("requirement")
        ][:8],
        "propagate_terms": highlight_terms[:16],
        "unsupported_hard": [
            s["requirement"]
            for s in support
            if s.get("support") == "Unsupported" and s.get("importance") == "hard"
        ],
        "soft_competencies": list(soft_competencies or [])[:12],
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
        # Never mirror bullet[0] into description — the renderer prints both,
        # which produces visible duplicate lines in the Projects section.
        desc = str(project.get("description") or "").strip()
        if desc and bullets:
            from intelligent_tailoring.services.one_page_compressor import (
                texts_are_near_duplicates,
            )

            if any(texts_are_near_duplicates(desc, b) for b in bullets):
                project["description"] = ""
        elif desc and len(desc.split()) <= 4 and bullets:
            # Drop ultra-thin stubs when real bullets already exist.
            project["description"] = ""
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
    soft_comps = list(
        resume_facts.get("soft_competencies")
        or inventory.get("soft_competencies")
        or []
    )
    highlight = build_highlight_plan(
        evidence_map=evidence_map,
        skills_to_emphasize=list(strategy.get("skills_to_emphasize") or []),
        soft_competencies=soft_comps,
        hiring_priorities=list(
            strategy.get("hiring_priorities")
            or strategy.get("narrative_themes")
            or []
        ),
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
        "transferable_evidence": highlight.get("transferable_highlight") or [],
        "soft_competencies": soft_comps[:12],
        "thin_projects_expanded": inventory.get("thin_projects") or [],
    }
    return facts, enrichment
