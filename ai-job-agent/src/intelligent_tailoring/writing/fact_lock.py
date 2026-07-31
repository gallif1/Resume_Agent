"""Fact immutability lock — writing may polish wording, never invent facts.

Compares a validated baseline resume against a polished candidate and
rejects or reverts sections that introduce new entities, metrics, skills,
employers, titles, dates, or technologies.
"""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.scope_validator import extract_tech_mentions

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])(?:\d+(?:\.\d+)?%?|\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?![A-Za-z])"
)
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z0-9+#./-]{1,}\b")
_STOP_PROPER = frozenset(
    {
        "I",
        "A",
        "The",
        "And",
        "Or",
        "With",
        "For",
        "In",
        "On",
        "At",
        "To",
        "Of",
        "By",
        "From",
        "As",
        "An",
        "My",
        "Our",
        "This",
        "That",
        "These",
        "Those",
        "Using",
        "Used",
        "Built",
        "Developed",
        "Created",
        "Managed",
        "Led",
        "Implemented",
        "Implementing",
        "Designed",
        "Supported",
        "Provided",
        "Improved",
        "Delivered",
        "Delivering",
        "Maintained",
        "Configured",
        "Deployed",
        "Integrated",
        "Collaborated",
        "Coordinated",
        "Analyzed",
        "Resolved",
        "Trained",
        "Prepared",
        "Operated",
        "Scheduled",
        "Contributed",
        "Applied",
        "Adopted",
        "Employed",
        "Handled",
        "Oversaw",
        "Owned",
        "Wrote",
        "Worked",
        "Utilized",
        "Leveraged",
        "Helped",
        "Assisted",
        "Participated",
        "Professional",
        "Languages",
        "Frameworks",
        "Tools",
        "Cloud",
        "Backend",
        "Frontend",
        "Databases",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Present",
        "Current",
        "Remote",
        "Hybrid",
        "Senior",
        "Junior",
        "Lead",
        "Principal",
        "Staff",
        "Intern",
        "Associate",
        "Manager",
        "Director",
        "Engineer",
        "Developer",
        "Analyst",
        "Specialist",
        "Coordinator",
        "Consultant",
        "Officer",
        "Representative",
        "Teacher",
        "Nurse",
        "Doctor",
        "Sales",
        "Customer",
        "Service",
        "Project",
        "Team",
        "Company",
        "Inc",
        "Ltd",
        "LLC",
        "Corp",
    }
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _extract_numbers(text: str) -> set[str]:
    return {m.group(0).lower() for m in _NUMBER_RE.finditer(text or "")}


def _extract_proper_nouns(text: str) -> set[str]:
    """Extract likely entities, ignoring sentence-initial action verbs."""
    found: set[str] = set()
    # Split into lines/sentences so bullet openings are not treated as entities.
    chunks = re.split(r"[\n.!?]+", text or "")
    for chunk in chunks:
        matches = list(_PROPER_NOUN_RE.finditer(chunk))
        for idx, match in enumerate(matches):
            token = match.group(0)
            if token in _STOP_PROPER:
                continue
            if len(token) < 2:
                continue
            # Skip the first Capitalized token in a bullet/sentence — nearly always a verb.
            if idx == 0 and match.start() <= 1:
                continue
            found.add(token.lower())
    return found


def _skill_atoms(skills: list[Any]) -> set[str]:
    atoms: set[str] = set()
    for skill in skills or []:
        text = str(skill or "")
        # "Languages: Python, SQL" → atoms from both sides
        if ":" in text:
            _, rhs = text.split(":", 1)
            text = rhs
        for part in re.split(r"[,|/•]", text):
            atom = _norm(part)
            if atom:
                atoms.add(atom)
    return atoms


def _identity_key(entry: dict[str, Any], *, kind: str) -> str:
    if kind == "experience":
        return "|".join(
            [
                _norm(str(entry.get("company") or "")),
                _norm(str(entry.get("title") or "")),
                _norm(str(entry.get("dates") or entry.get("date") or "")),
            ]
        )
    return "|".join(
        [
            _norm(str(entry.get("name") or entry.get("title") or "")),
            _norm(str(entry.get("dates") or entry.get("date") or "")),
        ]
    )


def _entry_text_blob(entry: dict[str, Any]) -> str:
    # Keep bullets on separate lines so sentence-initial action verbs are not
    # mistaken for novel proper nouns during fact locking.
    parts = [
        str(entry.get("company") or ""),
        str(entry.get("title") or ""),
        str(entry.get("name") or ""),
        str(entry.get("description") or ""),
        str(entry.get("dates") or entry.get("date") or ""),
        "\n".join(str(b) for b in (entry.get("bullets") or [])),
        "\n".join(str(t) for t in (entry.get("technologies") or [])),
    ]
    return "\n".join(parts)


def _resume_source_blob(resume: dict[str, Any]) -> str:
    parts = [
        str(resume.get("professional_summary") or resume.get("summary") or ""),
        str(resume.get("professional_title") or ""),
        " ".join(str(s) for s in (resume.get("skills") or [])),
    ]
    for section in ("experience", "projects", "education"):
        for entry in resume.get(section) or []:
            if isinstance(entry, dict):
                parts.append(_entry_text_blob(entry))
    for cert in resume.get("certifications") or []:
        if isinstance(cert, dict):
            parts.append(" ".join(str(v) for v in cert.values()))
        else:
            parts.append(str(cert))
    return "\n".join(parts)


def extract_locked_facts(resume: dict[str, Any]) -> dict[str, Any]:
    """Snapshot immutable facts from a validated resume."""
    blob = _resume_source_blob(resume)
    experience_keys = [
        _identity_key(e, kind="experience")
        for e in (resume.get("experience") or [])
        if isinstance(e, dict)
    ]
    project_keys = [
        _identity_key(e, kind="project")
        for e in (resume.get("projects") or [])
        if isinstance(e, dict)
    ]
    return {
        "skills": _skill_atoms(list(resume.get("skills") or [])),
        "numbers": _extract_numbers(blob),
        "tech": {t.lower() for t in extract_tech_mentions(blob)},
        "proper_nouns": _extract_proper_nouns(blob),
        "experience_keys": experience_keys,
        "project_keys": project_keys,
        "education_count": len(resume.get("education") or []),
        "cert_count": len(resume.get("certifications") or []),
        "experience_count": len(resume.get("experience") or []),
        "project_count": len(resume.get("projects") or []),
        "title": _norm(str(resume.get("professional_title") or "")),
        "blob": _norm(blob),
    }


def _novel_tokens(candidate: set[str], baseline: set[str], *, source_blob: str) -> set[str]:
    novel: set[str] = set()
    source = source_blob or ""
    for token in candidate:
        if not token:
            continue
        if token in baseline:
            continue
        # Allow tokens that already appear in the validated baseline text
        if token in source:
            continue
        novel.add(token)
    return novel


def compare_facts(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Return fact-lock comparison. ``passed`` means no factual inventions."""
    base = extract_locked_facts(baseline)
    cand = extract_locked_facts(candidate)
    violations: list[str] = []

    if cand["experience_count"] != base["experience_count"]:
        violations.append("experience_count_changed")
    if cand["project_count"] != base["project_count"]:
        violations.append("project_count_changed")
    if cand["education_count"] != base["education_count"]:
        violations.append("education_count_changed")
    if cand["cert_count"] != base["cert_count"]:
        violations.append("cert_count_changed")

    if cand["experience_keys"] != base["experience_keys"]:
        violations.append("experience_identity_changed")
    if cand["project_keys"] != base["project_keys"]:
        violations.append("project_identity_changed")

    # Skills may be reordered/regrouped but atoms must not expand.
    novel_skills = cand["skills"] - base["skills"]
    # Allow minor casing/whitespace regroup; reject genuinely new atoms
    for skill in sorted(novel_skills):
        if skill not in base["blob"]:
            violations.append(f"novel_skill:{skill[:60]}")

    novel_numbers = _novel_tokens(cand["numbers"], base["numbers"], source_blob=base["blob"])
    for num in sorted(novel_numbers):
        violations.append(f"novel_metric:{num}")

    novel_tech = _novel_tokens(cand["tech"], base["tech"], source_blob=base["blob"])
    for tech in sorted(novel_tech):
        violations.append(f"novel_tech:{tech[:60]}")

    # Proper nouns that look like employers/products not in baseline
    novel_nouns = _novel_tokens(
        cand["proper_nouns"], base["proper_nouns"], source_blob=base["blob"]
    )
    for noun in sorted(novel_nouns)[:12]:
        violations.append(f"novel_entity:{noun[:60]}")

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "baseline": {
            "skill_count": len(base["skills"]),
            "number_count": len(base["numbers"]),
            "tech_count": len(base["tech"]),
        },
        "candidate": {
            "skill_count": len(cand["skills"]),
            "number_count": len(cand["numbers"]),
            "tech_count": len(cand["tech"]),
        },
    }


def revert_section(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    section: str,
) -> dict[str, Any]:
    """Copy one section from baseline into a shallow-copied candidate."""
    out = dict(candidate)
    if section in ("summary", "professional_summary"):
        summary = str(
            baseline.get("professional_summary") or baseline.get("summary") or ""
        )
        out["professional_summary"] = summary
        out["summary"] = summary
    elif section == "skills":
        out["skills"] = list(baseline.get("skills") or [])
    elif section in ("experience", "projects", "education", "certifications"):
        # Deep-ish copy of list entries
        entries = baseline.get(section) or []
        out[section] = [dict(e) if isinstance(e, dict) else e for e in entries]
    elif section == "professional_title":
        out["professional_title"] = str(baseline.get("professional_title") or "")
    return out


def enforce_fact_lock(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """If facts drifted, revert offending structural fields and re-check.

    Writing polish that only changes wording passes. Structural/factual
    inventions cause a full revert to the validated baseline for safety.
    """
    comparison = compare_facts(baseline, candidate)
    if comparison["passed"]:
        return {
            "resume": candidate,
            "passed": True,
            "reverted": False,
            "violations": [],
            "comparison": comparison,
        }

    # Structural identity issues → full revert of experience/projects/etc.
    structural = {
        "experience_count_changed",
        "project_count_changed",
        "education_count_changed",
        "cert_count_changed",
        "experience_identity_changed",
        "project_identity_changed",
    }
    violations = list(comparison["violations"])
    repaired = dict(candidate)

    if any(v in structural for v in violations):
        repaired = dict(baseline)
        repaired["professional_summary"] = str(
            candidate.get("professional_summary") or candidate.get("summary") or ""
        )
        repaired["summary"] = repaired["professional_summary"]
        # Keep polished summary only if it doesn't invent facts
        summary_probe = {
            **baseline,
            "professional_summary": repaired["professional_summary"],
            "summary": repaired["professional_summary"],
        }
        if not compare_facts(baseline, summary_probe)["passed"]:
            repaired = dict(baseline)
            repaired["summary"] = str(
                baseline.get("professional_summary") or baseline.get("summary") or ""
            )
            repaired["professional_summary"] = repaired["summary"]
    else:
        # Soft factual drift (novel skill/tech/metric in prose) → revert whole resume
        # to validated baseline. Prefer safety over prettier but unsafe text.
        repaired = dict(baseline)
        repaired["summary"] = str(
            baseline.get("professional_summary") or baseline.get("summary") or ""
        )
        repaired["professional_summary"] = repaired["summary"]

    final_cmp = compare_facts(baseline, repaired)
    return {
        "resume": repaired,
        "passed": final_cmp["passed"],
        "reverted": True,
        "violations": violations,
        "comparison": final_cmp,
    }
