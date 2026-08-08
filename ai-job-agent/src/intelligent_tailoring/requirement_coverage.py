"""Requirement-coverage guards for the 4-agent resume pipeline.

Before any shortening / top-N selection, every resume bullet is cross-checked
against stated job requirements. Direct matches are high-priority and must not
be silently dropped. Shared technologies (present in both resume and JD) must
survive. Contact links are never discarded.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

# Specific tech → requirement hubs. Expansion is ONE-WAY from specific tokens
# (pytest → testing) so generic JD words like "testing" do not match unrelated
# "automated alerts" bullets via a shared "automated" synonym.
_SPECIFIC_TO_HUBS: dict[str, frozenset[str]] = {
    "pytest": frozenset(
        {"test", "tests", "testing", "unit", "integration", "automated", "automation"}
    ),
    "jest": frozenset(
        {"test", "tests", "testing", "unit", "integration", "automated", "automation"}
    ),
    "cypress": frozenset({"test", "tests", "testing", "e2e", "automated", "automation"}),
    "selenium": frozenset({"test", "tests", "testing", "e2e", "automated", "automation"}),
    "unittest": frozenset({"test", "tests", "testing", "unit"}),
    "fastapi": frozenset({"api", "apis", "rest", "backend", "python"}),
    "django": frozenset({"api", "apis", "rest", "backend", "python"}),
    "flask": frozenset({"api", "apis", "rest", "backend", "python"}),
    "graphql": frozenset({"api", "apis"}),
    "postgresql": frozenset({"database", "databases", "sql", "postgres"}),
    "postgres": frozenset({"database", "databases", "sql", "postgresql"}),
    "mongodb": frozenset({"database", "databases"}),
    "mysql": frozenset({"database", "databases", "sql"}),
    "sqlite": frozenset({"database", "databases", "sql"}),
    "redis": frozenset({"database", "databases", "cache"}),
    "react": frozenset({"frontend", "front-end", "ui"}),
    "angular": frozenset({"frontend", "front-end", "ui"}),
    "vue": frozenset({"frontend", "front-end", "ui"}),
    "ci/cd": frozenset({"deploy", "deployment", "devops", "pipeline"}),
    "cicd": frozenset({"deploy", "deployment", "devops", "pipeline"}),
    "docker": frozenset({"deploy", "deployment", "devops", "container"}),
    "kubernetes": frozenset({"deploy", "deployment", "devops", "container"}),
    "ec2": frozenset({"aws", "cloud", "deploy", "deployment"}),
    "s3": frozenset({"aws", "cloud"}),
    "rds": frozenset({"aws", "cloud", "database"}),
}

# Multi-word / compound phrases that signal a direct testing match.
_TESTING_PHRASE_RE = re.compile(
    r"\b("
    r"automated tests?|automation testing|unit tests?|integration tests?|"
    r"test coverage|test suite|testing utilities|write tests?|wrote tests?|"
    r"pytest|jest|cypress|selenium|unittest"
    r")\b",
    flags=re.I,
)

_SENIORITY_TOKENS = frozenset(
    {
        "junior",
        "senior",
        "staff",
        "principal",
        "lead",
        "entry-level",
        "entry level",
        "mid-level",
        "mid level",
        "intern",
        "associate",
    }
)

_CONTACT_FIELDS = ("email", "phone", "linkedin", "github", "portfolio", "location", "name")

_STOP = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "from",
        "that",
        "this",
        "your",
        "our",
        "you",
        "are",
        "was",
        "were",
        "have",
        "has",
        "will",
        "can",
        "ability",
        "strong",
        "using",
        "including",
        "experience",
        "knowledge",
        "skills",
        "skill",
        "required",
        "requirements",
        "preferred",
        "responsibilities",
        "ensure",
        "code",
        "quality",
        "create",
        "develop",
        "work",
        "team",
        "etc",
    }
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9+#./-]{2,}", _norm(text))
        if t not in _STOP and not t.isdigit()
    }


def _expand_specific_tech(tokens: set[str]) -> set[str]:
    """Expand specific technologies toward generic requirement hubs only."""
    expanded = set(tokens)
    for token in list(tokens):
        hubs = _SPECIFIC_TO_HUBS.get(token)
        if hubs:
            expanded |= hubs
    return expanded


def _phrases_from_jd_text(jd_text: str) -> list[str]:
    """Lightweight fallback when structured requirement lists are empty."""
    text = str(jd_text or "").strip()
    if not text:
        return []
    phrases: list[str] = []
    # Split on common JD list separators
    for chunk in re.split(r"[\n;•|/]+|(?<=\.)\s+", text):
        chunk = chunk.strip(" -\t\r\n:")
        chunk = re.sub(
            r"^(required|responsibilities|requirements|preferred|qualifications)\s*:?\s*",
            "",
            chunk,
            flags=re.I,
        ).strip()
        if not chunk:
            continue
        # Further split comma lists of skills
        if "," in chunk and len(chunk) < 160:
            parts = [p.strip() for p in chunk.split(",") if p.strip()]
            if 2 <= len(parts) <= 12 and all(len(p.split()) <= 4 for p in parts):
                phrases.extend(parts)
                continue
        if 2 <= len(chunk) <= 120:
            phrases.append(chunk)
    return phrases


def collect_requirement_phrases(
    *,
    strategy: dict[str, Any] | None = None,
    job_requirements: dict[str, Any] | None = None,
    ranked_requirements: list[dict[str, Any]] | None = None,
    evidence_map: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Build an explicit list of job requirement / qualification phrases.

    Prefers structured JD fields and ranked hard/soft requirements. Does NOT
    treat ontology-inferred competencies as stated job requirements — those
    would incorrectly protect the same bullets across unrelated roles.
    """
    phrases: list[str] = []
    strategy = strategy or {}
    requirements = job_requirements or strategy.get("job_requirements") or {}

    structured_keys = (
        "hard_requirements",
        "required_skills",
        "responsibilities",
        "tools_technologies",
        "ats_keywords",
        "preferred_skills",
        "soft_requirements",
        "soft_skills",
        "industry_terminology",
    )
    for key in structured_keys:
        for item in requirements.get(key) or []:
            text = str(item).strip()
            if text:
                phrases.append(text)

    for row in ranked_requirements or strategy.get("ranked_requirements") or []:
        if isinstance(row, dict):
            # Skip inferred-only rows without hard/soft importance
            importance = str(row.get("importance") or "").lower()
            if importance and importance not in ("hard", "soft", "required", "preferred"):
                continue
            text = str(row.get("requirement") or row.get("text") or "").strip()
        else:
            text = str(row).strip()
        if text:
            phrases.append(text)

    # Evidence-map: only explicit hard/soft JD requirements, never inferred ontology
    for row in evidence_map or []:
        if not isinstance(row, dict):
            continue
        importance = str(row.get("importance") or "").lower()
        inference = str(row.get("inference_category") or "").lower()
        if importance not in ("hard", "soft", "required", "preferred"):
            continue
        if inference in ("strongly inferred", "weakly inferred", "inferred"):
            continue
        if row.get("candidate_status") in ("MATCH", "PARTIAL", "MISSING"):
            text = str(row.get("requirement") or "").strip()
            if text:
                phrases.append(text)

    # Fallback: parse raw JD text when structured lists are empty
    if not phrases:
        jd_text = str(
            requirements.get("jd_text")
            or strategy.get("jd_text")
            or ""
        )
        phrases.extend(_phrases_from_jd_text(jd_text))

    # Soft boost terms from strategy only AFTER we have JD-anchored phrases,
    # and only keep short skill-like tokens (not long inferred statements).
    if phrases:
        for key in (
            "keywords_to_insert",
            "must_highlight_in_summary",
            "requirement_terms",
        ):
            for item in strategy.get(key) or []:
                text = str(item).strip()
                if text and len(text.split()) <= 4:
                    phrases.append(text)

    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in phrases:
        key = _norm(p)
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def requirement_term_set(phrases: list[str]) -> set[str]:
    """Flatten requirement phrases into a token set (no over-broad synonym fan-out)."""
    tokens: set[str] = set()
    for phrase in phrases:
        tokens |= _tokens(phrase)
        low = _norm(phrase)
        if 2 <= len(low) <= 40 and " " in low:
            tokens.add(low)
    return tokens


def bullet_matches_requirements(
    bullet: str,
    requirement_terms: set[str],
    *,
    phrases: list[str] | None = None,
) -> dict[str, Any]:
    """Score how directly a bullet matches stated job requirements."""
    text = str(bullet or "").strip()
    low = _norm(text)
    if not text or not requirement_terms:
        return {
            "matches": False,
            "direct": False,
            "overlap_terms": [],
            "matched_phrases": [],
            "score": 0,
        }

    raw_bullet_tokens = _tokens(text)
    # Expand only specific techs on the bullet side toward JD hubs
    bullet_tokens = _expand_specific_tech(raw_bullet_tokens)
    overlap = sorted(t for t in (bullet_tokens & requirement_terms) if len(t) >= 3)
    matched_phrases: list[str] = []
    for phrase in phrases or []:
        phrase_low = _norm(phrase)
        phrase_tokens = _tokens(phrase)
        if not phrase_tokens:
            continue
        distinctive = {t for t in phrase_tokens if len(t) >= 4 and t not in _STOP}
        if not distinctive:
            continue
        hit_ratio = len(distinctive & bullet_tokens) / max(len(distinctive), 1)
        # Prefer concrete tech / multi-word presence over loose token ratios
        concrete_hit = any(
            len(t) >= 5 and t in raw_bullet_tokens for t in distinctive
        ) or any(
            t in raw_bullet_tokens
            for t in distinctive
            if t in _SPECIFIC_TO_HUBS
        )
        if phrase_low and (
            phrase_low in low
            or concrete_hit
            or (hit_ratio >= 0.6 and len(distinctive & bullet_tokens) >= 2)
        ):
            matched_phrases.append(phrase)

    strong_tech = {
        t
        for t in (raw_bullet_tokens & requirement_terms)
        if t in _SPECIFIC_TO_HUBS
        or t
        in {
            "typescript",
            "javascript",
            "python",
            "aws",
            "docker",
            "kubernetes",
            "websockets",
            "salesforce",
            "hubspot",
            "epic",
            "ehr",
        }
    }

    # Explicit testing-language bullet vs testing-oriented requirements
    testing_req = any(
        t in requirement_terms
        for t in (
            "test",
            "tests",
            "testing",
            "pytest",
            "jest",
            "qa",
            "unit",
            "integration",
        )
    ) or any(
        re.search(r"\b(test|testing|pytest|qa)\b", _norm(p)) for p in (phrases or [])
    )
    testing_bullet = bool(_TESTING_PHRASE_RE.search(text))
    testing_direct = testing_req and testing_bullet

    direct = bool(matched_phrases) or bool(strong_tech) or testing_direct
    score = len(overlap) * 6 + len(matched_phrases) * 22 + len(strong_tech) * 18
    if testing_direct:
        score += 50
    if direct:
        score += 35
    # Weak generic overlaps (e.g. "documentation") alone are not enough
    if not direct and score < 28:
        score = min(score, 15)
    return {
        "matches": score >= 20 or direct,
        "direct": direct,
        "overlap_terms": overlap[:12],
        "matched_phrases": matched_phrases[:6],
        "score": score,
    }


def classify_bullets(
    bullets: list[str],
    *,
    requirement_terms: set[str],
    phrases: list[str] | None = None,
) -> dict[str, list[str]]:
    """Split bullets into protected (requirement match) vs low-relevance."""
    protected: list[str] = []
    mid: list[str] = []
    low: list[str] = []
    for bullet in bullets:
        text = str(bullet or "").strip()
        if not text:
            continue
        info = bullet_matches_requirements(
            text, requirement_terms, phrases=phrases
        )
        if info["direct"] or info["score"] >= 35:
            protected.append(text)
        elif info["matches"] or info["score"] >= 16:
            mid.append(text)
        else:
            low.append(text)
    return {"protected": protected, "mid": mid, "low": low}


def select_bullets_with_coverage(
    bullets: list[str],
    *,
    limit: int,
    requirement_terms: set[str],
    phrases: list[str] | None = None,
    score_fn=None,
) -> list[str]:
    """Select up to ``limit`` bullets, never silently dropping direct matches.

    Protected (requirement-matching) bullets are reserved first. Remaining
    slots go to highest-scored mid/low bullets. If protected count exceeds
    limit, keep the highest-scored protected bullets (still prefer matches
    over zero-overlap content).
    """
    cleaned = [str(b).strip() for b in bullets if str(b).strip()]
    if limit <= 0 or not cleaned:
        return []
    if len(cleaned) <= limit:
        return cleaned

    def _score(b: str) -> int:
        base = int(score_fn(b)) if score_fn else 0
        info = bullet_matches_requirements(b, requirement_terms, phrases=phrases)
        return base + int(info["score"])

    groups = classify_bullets(
        cleaned, requirement_terms=requirement_terms, phrases=phrases
    )
    protected = sorted(groups["protected"], key=_score, reverse=True)
    mid = sorted(groups["mid"], key=_score, reverse=True)
    low = sorted(groups["low"], key=_score, reverse=True)

    # Always try to keep every protected bullet; expand limit slightly only when
    # a few protected items would otherwise be cut (caller may re-trim elsewhere).
    kept: list[str] = []
    for b in protected:
        if len(kept) < limit:
            kept.append(b)
    for pool in (mid, low):
        for b in pool:
            if len(kept) >= limit:
                break
            if b not in kept:
                kept.append(b)

    # If we still couldn't fit all protected, replace lowest non-protected with
    # remaining protected (should already be handled by scoring, but be explicit).
    if len(protected) > limit:
        return protected[:limit]

    # Restore any protected that somehow missed the cut by swapping out lows
    missing_protected = [b for b in protected if b not in kept]
    if missing_protected:
        # Drop from the end (lowest relevance among kept) first
        for miss in missing_protected:
            replaceable = [
                b
                for b in reversed(kept)
                if b not in protected
            ]
            if not replaceable:
                break
            victim = replaceable[0]
            kept.remove(victim)
            kept.append(miss)

    # Stable-ish: re-sort kept by score desc so strongest appear first
    kept = sorted(kept, key=_score, reverse=True)
    return kept[:limit]


def extract_skill_atoms(skills: list[Any]) -> list[str]:
    atoms: list[str] = []
    for item in skills or []:
        text = str(item).strip()
        if not text:
            continue
        rest = text.split(":", 1)[1] if ":" in text else text
        for part in re.split(r"\s*[,;/|]\s*", rest):
            atom = part.strip()
            if atom:
                atoms.append(atom)
    return atoms


def shared_technologies(
    resume_skills: list[Any],
    requirement_terms: set[str],
    *,
    resume_text: str = "",
) -> list[str]:
    """Technologies / skills present in both the resume and the job posting.

    Only returns concrete skill atoms from the resume (or known tech tokens from
    the JD that also appear in resume text). Generic words like \"technical\"
    are never treated as shared technologies.
    """
    atoms = extract_skill_atoms(resume_skills)
    blob = _norm(resume_text)
    shared: list[str] = []
    seen: set[str] = set()
    generic_block = _STOP | {
        "technical",
        "troubleshooting",
        "deployment",
        "backend",
        "frontend",
        "cloud",
        "automation",
        "documentation",
        "validation",
        "debugging",
        "quality",
        "development",
        "programming",
        "analysis",
        "scripting",
    }

    for atom in atoms:
        key = _norm(atom)
        if not key or key in seen or key in generic_block:
            continue
        tokens = _expand_specific_tech(_tokens(atom))
        # Atom itself (or its specific tech id) must appear in JD terms
        if key in requirement_terms or any(
            t in requirement_terms for t in _tokens(atom) if t not in generic_block
        ):
            seen.add(key)
            shared.append(atom)
            continue
        # Specific tech on resume whose hubs intersect JD (pytest ↔ testing)
        if any(t in _SPECIFIC_TO_HUBS and t in tokens for t in _tokens(atom)):
            hubs = set()
            for t in _tokens(atom):
                hubs |= _SPECIFIC_TO_HUBS.get(t, frozenset())
            if hubs & requirement_terms:
                seen.add(key)
                shared.append(atom)

    # JD tech tokens evidenced in resume prose (even if missing from skill lines)
    for term in sorted(requirement_terms):
        if (
            len(term) < 3
            or term in seen
            or term in generic_block
            or " " in term
        ):
            continue
        # Only promote known tech / distinctive tokens
        if term not in _SPECIFIC_TO_HUBS and term not in {
            "python",
            "typescript",
            "javascript",
            "aws",
            "docker",
            "kubernetes",
            "websockets",
            "salesforce",
            "hubspot",
            "epic",
            "ehr",
            "pytest",
            "jest",
            "fastapi",
            "django",
            "react",
            "angular",
            "postgresql",
            "mongodb",
        }:
            continue
        if re.search(rf"\b{re.escape(term)}\b", blob):
            display = next(
                (a for a in atoms if _norm(a) == term or term in _norm(a)),
                term,
            )
            key = _norm(display)
            if key not in seen:
                seen.add(key)
                shared.append(display)
    return shared


def prioritize_skill_lines(
    skill_lines: list[str],
    *,
    shared_tech: list[str],
    max_lines: int,
) -> list[str]:
    """Cap skill lines while keeping categories that hold shared technologies."""
    lines = [str(s).strip() for s in skill_lines if str(s).strip()]
    if len(lines) <= max_lines:
        return lines
    shared_norm = {_norm(t) for t in shared_tech}

    def _line_score(line: str) -> int:
        low = _norm(line)
        score = 0
        for term in shared_norm:
            if term and term in low:
                score += 10
        return score

    ranked = sorted(enumerate(lines), key=lambda pair: (-_line_score(pair[1]), pair[0]))
    keep_idx = sorted(i for i, _ in ranked[:max_lines])
    kept = [lines[i] for i in keep_idx]

    # Ensure every shared tech still appears; inject into best-matching kept line
    blob = " ".join(kept).lower()
    missing = [t for t in shared_tech if _norm(t) not in blob]
    if missing and kept:
        # Append missing atoms to the first kept line (or a Testing/Tools line)
        target_idx = 0
        for i, line in enumerate(kept):
            if re.search(r"\b(testing|tools|backend|languages)\b", line, re.I):
                target_idx = i
                break
        base = kept[target_idx]
        if ":" in base:
            cat, rest = base.split(":", 1)
            atoms = [a.strip() for a in rest.split(",") if a.strip()]
            for m in missing:
                if _norm(m) not in {_norm(a) for a in atoms}:
                    atoms.append(m)
            kept[target_idx] = f"{cat.strip()}: {', '.join(atoms)}"
        else:
            kept[target_idx] = f"{base}, {', '.join(missing)}"
    return kept


def collect_source_bullets(resume_facts: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Inventory source experience/project bullets with provenance."""
    facts = resume_facts if isinstance(resume_facts, dict) else {}
    out: list[dict[str, Any]] = []
    for idx, role in enumerate(facts.get("experience_roles") or facts.get("experience") or []):
        if not isinstance(role, dict):
            continue
        for bullet in role.get("bullets") or []:
            text = str(bullet).strip()
            if text:
                out.append(
                    {
                        "section": "experience",
                        "entry_index": idx,
                        "company": str(role.get("company") or ""),
                        "title": str(role.get("title") or ""),
                        "name": "",
                        "text": text,
                    }
                )
    for idx, proj in enumerate(facts.get("projects") or []):
        if not isinstance(proj, dict):
            continue
        for bullet in proj.get("bullets") or proj.get("bullet_points") or []:
            text = str(bullet).strip()
            if text:
                out.append(
                    {
                        "section": "projects",
                        "entry_index": idx,
                        "company": "",
                        "title": "",
                        "name": str(proj.get("name") or ""),
                        "text": text,
                    }
                )
    return out


def _resume_bullet_texts(resume: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for entry in list(resume.get("experience") or []) + list(resume.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        for b in entry.get("bullets") or []:
            t = str(b).strip()
            if t:
                texts.append(t)
        desc = str(entry.get("description") or "").strip()
        if desc:
            texts.append(desc)
    return texts


def _near_present(needle: str, haystack_texts: list[str]) -> bool:
    n = _norm(needle)
    if not n:
        return False
    n_tokens = _tokens(needle)
    for hay in haystack_texts:
        h = _norm(hay)
        if not h:
            continue
        if n == h or (len(n) >= 24 and (n in h or h in n)):
            return True
        h_tokens = _tokens(hay)
        if n_tokens and h_tokens:
            overlap = len(n_tokens & h_tokens) / max(len(n_tokens), 1)
            if overlap >= 0.55:
                return True
    return False


def validate_requirement_coverage(
    *,
    source_facts: dict[str, Any],
    tailored_resume: dict[str, Any],
    requirement_phrases: list[str],
    requirement_terms: set[str] | None = None,
) -> dict[str, Any]:
    """Flag source bullets that match the JD but were dropped from the final resume."""
    terms = requirement_terms or requirement_term_set(requirement_phrases)
    source_bullets = collect_source_bullets(source_facts)
    final_texts = _resume_bullet_texts(tailored_resume)
    summary_skills = " ".join(
        [
            str(tailored_resume.get("professional_summary") or ""),
            " ".join(str(s) for s in (tailored_resume.get("skills") or [])),
        ]
    )
    final_texts_with_summary = final_texts + [summary_skills]

    dropped_matches: list[dict[str, Any]] = []
    for item in source_bullets:
        info = bullet_matches_requirements(
            item["text"], terms, phrases=requirement_phrases
        )
        if not (info["direct"] or info["score"] >= 35):
            continue
        if _near_present(item["text"], final_texts_with_summary):
            continue
        # Also accept if distinctive overlap terms still appear in final resume
        distinctive = [
            t for t in info["overlap_terms"] if len(t) >= 4 and t not in _STOP
        ]
        blob = " ".join(final_texts_with_summary).lower()
        if distinctive and sum(1 for t in distinctive[:6] if t in blob) >= max(
            2, min(3, len(distinctive))
        ):
            continue
        dropped_matches.append(
            {
                **item,
                "overlap_terms": info["overlap_terms"],
                "matched_phrases": info["matched_phrases"],
                "score": info["score"],
                "reason": "requirement_matching_bullet_dropped",
            }
        )

    return {
        "passed": not dropped_matches,
        "dropped_requirement_bullets": dropped_matches[:20],
        "dropped_count": len(dropped_matches),
        "requirement_phrase_count": len(requirement_phrases),
    }


def restore_requirement_matched_bullets(
    tailored: dict[str, Any],
    *,
    source_facts: dict[str, Any],
    requirement_phrases: list[str],
    requirement_terms: set[str] | None = None,
    max_restore: int = 6,
) -> dict[str, Any]:
    """Re-inject dropped requirement-matching bullets, displacing low-relevance ones."""
    report = validate_requirement_coverage(
        source_facts=source_facts,
        tailored_resume=tailored,
        requirement_phrases=requirement_phrases,
        requirement_terms=requirement_terms,
    )
    dropped = list(report.get("dropped_requirement_bullets") or [])
    if not dropped:
        return tailored

    out = deepcopy(tailored)
    terms = requirement_terms or requirement_term_set(requirement_phrases)
    restored = 0

    for item in dropped:
        if restored >= max_restore:
            break
        section = item["section"]
        text = item["text"]
        if section == "experience":
            entries = [e for e in (out.get("experience") or []) if isinstance(e, dict)]
            target = _find_entry(
                entries,
                company=item.get("company") or "",
                title=item.get("title") or "",
            )
            if target is None:
                # Create entry from source if missing entirely
                source_roles = list(
                    source_facts.get("experience_roles")
                    or source_facts.get("experience")
                    or []
                )
                idx = int(item.get("entry_index") or 0)
                if 0 <= idx < len(source_roles) and isinstance(source_roles[idx], dict):
                    role = source_roles[idx]
                    target = {
                        "company": str(role.get("company") or ""),
                        "title": str(role.get("title") or ""),
                        "dates": str(role.get("dates") or ""),
                        "bullets": [],
                    }
                    entries.append(target)
                    out["experience"] = entries
            if target is None:
                continue
            bullets = [str(b).strip() for b in (target.get("bullets") or []) if str(b).strip()]
            if _near_present(text, bullets):
                continue
            bullets = _insert_protected(bullets, text, terms=terms, phrases=requirement_phrases)
            target["bullets"] = bullets
            restored += 1
        else:
            entries = [e for e in (out.get("projects") or []) if isinstance(e, dict)]
            target = _find_entry(entries, name=item.get("name") or "")
            if target is None:
                source_projects = list(source_facts.get("projects") or [])
                idx = int(item.get("entry_index") or 0)
                if 0 <= idx < len(source_projects) and isinstance(source_projects[idx], dict):
                    proj = source_projects[idx]
                    target = {
                        "name": str(proj.get("name") or ""),
                        "description": str(proj.get("description") or ""),
                        "bullets": [],
                        "technologies": list(proj.get("technologies") or []),
                    }
                    entries.append(target)
                    out["projects"] = entries
            if target is None:
                continue
            bullets = [str(b).strip() for b in (target.get("bullets") or []) if str(b).strip()]
            if _near_present(text, bullets):
                continue
            bullets = _insert_protected(bullets, text, terms=terms, phrases=requirement_phrases)
            target["bullets"] = bullets
            restored += 1

    out["_requirement_coverage_restore"] = {
        "restored": restored,
        "flagged": len(dropped),
    }
    return out


def _find_entry(
    entries: list[dict[str, Any]],
    *,
    company: str = "",
    title: str = "",
    name: str = "",
) -> dict[str, Any] | None:
    c = _norm(company)
    t = _norm(title)
    n = _norm(name)
    for entry in entries:
        if n and n in _norm(str(entry.get("name") or "")):
            return entry
        if c and c == _norm(str(entry.get("company") or "")):
            return entry
        if t and t == _norm(str(entry.get("title") or "")):
            return entry
    # Soft name contains
    for entry in entries:
        en = _norm(str(entry.get("name") or ""))
        if n and en and (n in en or en in n):
            return entry
    return entries[0] if entries else None


def _insert_protected(
    bullets: list[str],
    protected: str,
    *,
    terms: set[str],
    phrases: list[str],
    soft_cap: int = 4,
) -> list[str]:
    kept = [protected] + [b for b in bullets if _norm(b) != _norm(protected)]
    if len(kept) <= soft_cap:
        return kept
    # Drop lowest-relevance (non-protected) first
    groups = classify_bullets(kept[1:], requirement_terms=terms, phrases=phrases)
    ordered = [protected] + groups["protected"] + groups["mid"] + groups["low"]
    # Dedupe
    seen: set[str] = set()
    out: list[str] = []
    for b in ordered:
        key = _norm(b)
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
        if len(out) >= soft_cap:
            break
    return out


def preserve_contact(
    tailored: dict[str, Any],
    *,
    source_contact: dict[str, Any] | None,
    resume_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure contact/links from the base resume are present on the tailored dict."""
    out = deepcopy(tailored) if isinstance(tailored, dict) else {}
    contact: dict[str, Any] = {}
    for src in (
        source_contact,
        (resume_facts or {}).get("contact") if isinstance(resume_facts, dict) else None,
        out.get("contact") if isinstance(out.get("contact"), dict) else None,
    ):
        if not isinstance(src, dict):
            continue
        for field in _CONTACT_FIELDS:
            val = str(src.get(field) or "").strip()
            if val and not str(contact.get(field) or "").strip():
                contact[field] = val
    if contact:
        out["contact"] = contact
    return out


def contact_preservation_report(
    *,
    source_contact: dict[str, Any] | None,
    tailored: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flag missing contact/link fields that existed on the base resume."""
    src = source_contact if isinstance(source_contact, dict) else {}
    dst = {}
    if isinstance(tailored, dict):
        dst = tailored.get("contact") if isinstance(tailored.get("contact"), dict) else {}
        # Also accept flat keys some renderers use
        for field in _CONTACT_FIELDS:
            if not dst.get(field) and tailored.get(field):
                dst[field] = tailored.get(field)

    missing: list[str] = []
    for field in ("email", "phone", "linkedin", "github", "portfolio"):
        if str(src.get(field) or "").strip() and not str(dst.get(field) or "").strip():
            missing.append(field)
    return {
        "passed": not missing,
        "missing_fields": missing,
        "source_fields": [
            f for f in ("email", "phone", "linkedin", "github", "portfolio")
            if str(src.get(f) or "").strip()
        ],
    }


def jd_mentions_seniority(job_text: str = "", seniority_level: str = "") -> bool:
    blob = f"{job_text} {seniority_level}".lower()
    if str(seniority_level or "").strip():
        return True
    return any(tok in blob for tok in _SENIORITY_TOKENS)


def sanitize_professional_title(
    title: str,
    *,
    job_title: str = "",
    job_text: str = "",
    seniority_level: str = "",
    base_resume_title: str = "",
) -> str:
    """Avoid inventing seniority labels the JD does not use.

    If the JD has no seniority signal, strip Junior/Senior/etc. from the
    tailored title and prefer the JD's own title (neutral) when available.
    Never fabricate seniority the candidate does not have.
    """
    current = re.sub(r"\s+", " ", (title or "").strip())
    if not current:
        return (job_title or base_resume_title or "").strip()

    if jd_mentions_seniority(job_text, seniority_level):
        return current

    # JD has no seniority — strip unwarranted seniority adjectives
    pattern = re.compile(
        r"\b(junior|senior|staff|principal|lead|entry-level|entry level|"
        r"mid-level|mid level|intern)\b",
        flags=re.I,
    )
    stripped = pattern.sub("", current)
    stripped = re.sub(r"\s{2,}", " ", stripped).strip(" -|,")

    # If stripping emptied the title, fall back to JD title or base
    if not stripped:
        stripped = (job_title or base_resume_title or current).strip()

    # Prefer JD title when tailored title was only a seniority-prefixed JD title
    jd = (job_title or "").strip()
    if jd and pattern.sub("", current).strip(" -|,").lower() == jd.lower():
        return jd
    if jd and not pattern.search(jd) and pattern.search(current):
        # Tailored added seniority the JD never used — use neutral JD title
        return jd
    return stripped or current


def build_coverage_strategy_fields(
    *,
    resume_facts: dict[str, Any],
    strategy: dict[str, Any],
    job_requirements: dict[str, Any] | None = None,
    evidence_map: list[dict[str, Any]] | None = None,
    ranked_requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute must-keep bullets/skills and attach them onto a strategy dict."""
    phrases = collect_requirement_phrases(
        strategy=strategy,
        job_requirements=job_requirements,
        ranked_requirements=ranked_requirements,
        evidence_map=evidence_map,
    )
    terms = requirement_term_set(phrases)
    source_bullets = collect_source_bullets(resume_facts)
    must_keep: list[str] = []
    for item in source_bullets:
        info = bullet_matches_requirements(item["text"], terms, phrases=phrases)
        if info["direct"] or info["score"] >= 35:
            must_keep.append(item["text"])

    skills = list(
        resume_facts.get("display_skills") or resume_facts.get("skills") or []
    )
    resume_blob = " ".join(
        [str(resume_facts.get("raw_text") or "")]
        + [b["text"] for b in source_bullets]
        + [str(s) for s in skills]
    )
    shared = shared_technologies(skills, terms, resume_text=resume_blob)

    updated = dict(strategy)
    updated["requirement_phrases"] = phrases[:60]
    updated["requirement_terms"] = sorted(terms)[:80]
    updated["must_keep_bullets"] = must_keep[:24]
    updated["shared_technologies"] = shared[:24]
    updated["must_keep_skills"] = shared[:24]

    # Promote into preserve/expand/emphasize lists
    preserve = list(updated.get("facts_to_preserve") or [])
    expand = list(updated.get("facts_to_expand") or [])
    strongest = list(updated.get("strongest_evidence") or [])
    emphasize = list(updated.get("skills_to_emphasize") or [])
    propagate = list(updated.get("propagate_terms") or [])

    for text in must_keep:
        if text not in preserve:
            preserve.insert(0, text)
        if text not in expand:
            expand.insert(0, text)
        if text not in strongest:
            strongest.insert(0, text)
    for tech in shared:
        if tech not in emphasize:
            emphasize.insert(0, tech)
        if tech not in propagate:
            propagate.insert(0, tech)

    # Never list must-keep bullets under omit/condense
    omit = [
        t
        for t in (updated.get("facts_to_omit") or [])
        if not _near_present(str(t), must_keep)
        and not any(_norm(str(t)) == _norm(m) for m in must_keep)
    ]
    weaker = [
        t
        for t in (updated.get("weaker_evidence_to_reduce") or [])
        if not any(_norm(str(t)) == _norm(m) for m in must_keep)
    ]

    updated["facts_to_preserve"] = preserve[:40]
    updated["facts_to_expand"] = expand[:30]
    updated["strongest_evidence"] = strongest[:12]
    updated["skills_to_emphasize"] = emphasize[:16]
    updated["propagate_terms"] = propagate[:20]
    updated["facts_to_omit"] = omit[:20]
    updated["weaker_evidence_to_reduce"] = weaker[:12]
    return updated
