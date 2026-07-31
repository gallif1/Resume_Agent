"""Safe claim-level rewriter — never delete tokens from a sentence.

When a generated claim mentions unsupported entities for its source entry,
rebuild a complete grammatical sentence from supported facts, restore the
closest original bullet, or reject the entire claim.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from intelligent_tailoring.linguistic_integrity import (
    detect_broken_patterns,
    text_hash,
    validate_claim_linguistics,
)
from intelligent_tailoring.scope_validator import (
    extract_tech_mentions,
    has_unsupported_impact,
    technologies_bound_to_entry,
    validate_bullet_tech_scope,
)

logger = logging.getLogger("intelligent_tailoring.safe_claim_rewriter")

_TECH_PHRASE_RE = re.compile(
    r"\b(using|with|via|through|on|in)\s+"
    r"((?:[A-Za-z][A-Za-z0-9.+#/\-]*(?:\s+[A-Za-z][A-Za-z0-9.+#/\-]*){0,2})"
    r"(?:\s*(?:,|and|&)\s*"
    r"(?:[A-Za-z][A-Za-z0-9.+#/\-]*(?:\s+[A-Za-z][A-Za-z0-9.+#/\-]*){0,2})*)"
    r"(?:\s*\([^)]*\))?)",
    re.I,
)

_PAREN_TECH_RE = re.compile(
    r"\b(using|with|via|on)\s*\(([^)]+)\)",
    re.I,
)


@dataclass
class ValidatedClaim:
    id: str
    original_generated_text: str
    final_text: str
    section: str
    source_entry_id: str = ""
    source_fact_ids: list[str] = field(default_factory=list)
    evidence_type: str = "explicit"
    confidence: float = 1.0
    validation_status: str = "accepted"  # accepted|safely_rewritten|rejected
    validation_errors: list[str] = field(default_factory=list)
    unsupported_entities: list[str] = field(default_factory=list)
    repair_method: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "original_generated_text": self.original_generated_text,
            "final_text": self.final_text,
            "section": self.section,
            "source_entry_id": self.source_entry_id,
            "source_fact_ids": list(self.source_fact_ids),
            "evidence_type": self.evidence_type,
            "confidence": self.confidence,
            "validation_status": self.validation_status,
            "validation_errors": list(self.validation_errors),
            "unsupported_entities": list(self.unsupported_entities),
            "repair_method": self.repair_method,
        }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _display_tech(tech: str, canonical: dict[str, str] | None = None) -> str:
    """Pretty-print a normalized tech token."""
    canonical = canonical or {}
    key = _norm(tech)
    if key in canonical:
        return canonical[key]
    # Common casing
    special = {
        "nodejs": "Node.js",
        "node.js": "Node.js",
        "fastapi": "FastAPI",
        "postgresql": "PostgreSQL",
        "postgres": "PostgreSQL",
        "sqlalchemy": "SQLAlchemy",
        "sqlite": "SQLite",
        "mongodb": "MongoDB",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "react native": "React Native",
        "ci/cd": "CI/CD",
        "pytest": "pytest",
        "aws": "AWS",
        "ec2": "EC2",
        "rds": "RDS",
        "s3": "S3",
        "rest api": "REST API",
        "rest apis": "REST APIs",
        "html": "HTML",
        "css": "CSS",
        "ssh": "SSH",
        "ftp": "FTP",
        "http": "HTTP",
        "threadpoolexecutor": "ThreadPoolExecutor",
        "generative ai": "Generative AI",
    }
    if key in special:
        return special[key]
    if tech.isupper() or (tech[:1].isupper() and any(c.isupper() for c in tech[1:])):
        return tech
    return tech.title() if tech.islower() else tech


def _format_tech_list(techs: list[str]) -> str:
    items = [_display_tech(t) for t in techs if t]
    # Dedupe preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            unique.append(item)
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    if len(unique) == 2:
        return f"{unique[0]} and {unique[1]}"
    return ", ".join(unique[:-1]) + f", and {unique[-1]}"


def _stem_token(token: str) -> str:
    t = token.lower()
    for suffix in ("ing", "ed", "es", "s"):
        if len(t) > len(suffix) + 3 and t.endswith(suffix):
            return t[: -len(suffix)]
    return t


def _token_overlap(a: str, b: str) -> float:
    ta = {
        _stem_token(t)
        for t in re.findall(r"[a-z0-9\u0590-\u05ff]{3,}", (a or "").lower())
        if t not in {"the", "and", "with", "using", "for", "from", "through"}
    }
    tb = {
        _stem_token(t)
        for t in re.findall(r"[a-z0-9\u0590-\u05ff]{3,}", (b or "").lower())
        if t not in {"the", "and", "with", "using", "for", "from", "through"}
    }
    if not ta or not tb:
        return 0.0
    # Prefix match for stems (complaint/complaints)
    shared = 0
    for x in ta:
        if x in tb or any(x.startswith(y) or y.startswith(x) for y in tb if min(len(x), len(y)) >= 4):
            shared += 1
    return shared / max(len(ta), 1)


def find_closest_original_bullet(
    generated: str,
    original_bullets: list[str],
) -> str | None:
    candidates = [str(b).strip() for b in (original_bullets or []) if str(b).strip()]
    if not candidates:
        return None
    best = None
    best_score = 0.0
    for text in candidates:
        score = _token_overlap(generated, text)
        if score > best_score:
            best_score = score
            best = text
    if best_score >= 0.2:
        return best
    # Single-bullet entries: always allow restore when generation is unsupported
    if len(candidates) == 1:
        return candidates[0]
    return None


def _canonical_tech_map(
    facts: list[dict[str, Any]] | list[Any],
    entry_source_text: str,
    original_bullets: list[str],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in list(original_bullets) + [entry_source_text]:
        for m in re.finditer(
            r"\b([A-Za-z][A-Za-z0-9.+#/\-]{1,}(?:\s[A-Za-z][A-Za-z0-9.+#/\-]{1,})?)\b",
            raw or "",
        ):
            token = m.group(1)
            mapping.setdefault(_norm(token), token)
    for f in facts or []:
        data = f if isinstance(f, dict) else {}
        for skill in data.get("explicit_skills") or []:
            mapping.setdefault(_norm(str(skill)), str(skill))
        ot = str(data.get("original_text") or "")
        if ot and len(ot.split()) <= 4:
            mapping.setdefault(_norm(ot), ot)
    return mapping


def _supported_techs_for_rewrite(
    *,
    generated: str,
    leaked: set[str],
    bound: set[str],
    entry_source_text: str,
    original_bullets: list[str],
) -> list[str]:
    mentioned = extract_tech_mentions(generated)
    supported_mentioned = sorted(mentioned - leaked)
    if supported_mentioned:
        # Prefer order from original sentence
        ordered: list[str] = []
        for m in re.finditer(
            r"\b([A-Za-z][A-Za-z0-9.+#/\-]{1,}(?:\.[A-Za-z]+)?)\b", generated
        ):
            tok = _norm(m.group(1))
            if tok in mentioned and tok not in leaked and tok not in {_norm(x) for x in ordered}:
                ordered.append(tok)
        if ordered:
            return ordered
        return supported_mentioned

    # Pull techs from closest original bullet
    closest = find_closest_original_bullet(generated, original_bullets)
    if closest:
        from_orig = sorted(extract_tech_mentions(closest) & bound)
        if from_orig:
            return from_orig

    # Fall back to bound techs appearing in entry source (stable order)
    from_entry: list[str] = []
    for m in re.finditer(
        r"\b([A-Za-z][A-Za-z0-9.+#/\-]{1,}(?:\.[A-Za-z]+)?)\b", entry_source_text or ""
    ):
        tok = _norm(m.group(1))
        if tok in bound and tok not in from_entry:
            from_entry.append(tok)
    return from_entry[:6]


def _rebuild_using_phrase(
    generated: str,
    supported_techs: list[str],
    *,
    prefer_aws_parent: bool = False,
) -> str | None:
    """Replace the technology phrase in the sentence with supported techs."""
    if not supported_techs:
        return None
    tech_list = _format_tech_list(supported_techs)
    if not tech_list:
        return None

    # Special case: AWS parent + child services
    children = [t for t in supported_techs if _norm(t) in {"ec2", "rds", "s3"}]
    has_aws = any(_norm(t) == "aws" for t in supported_techs)
    if (has_aws or prefer_aws_parent) and children:
        child_list = _format_tech_list(children)
        # Replace "using (...)" or "using X" deploy phrases
        if _PAREN_TECH_RE.search(generated) or re.search(
            r"\bdeploy", generated, re.I
        ):
            text = _PAREN_TECH_RE.sub(
                f"on AWS using {child_list}", generated, count=1
            )
            if text == generated:
                text = _TECH_PHRASE_RE.sub(
                    f"on AWS using {child_list}", generated, count=1
                )
            # Drop standalone AWS from a duplicate "using AWS on AWS..."
            text = re.sub(r"\bon AWS using AWS\b", "on AWS using", text, flags=re.I)
            if not detect_broken_patterns(text):
                return text.strip()

    match = _TECH_PHRASE_RE.search(generated)
    if match:
        preposition = match.group(1)
        # Prefer "on AWS using …" already handled; otherwise keep preposition
        replacement = f"{preposition} {tech_list}"
        text = (
            generated[: match.start()]
            + replacement
            + generated[match.end() :]
        )
        text = re.sub(r"\s{2,}", " ", text).strip(" ,;")
        if not text.endswith("."):
            # Keep original terminal punctuation style for bullets (often no period)
            pass
        if not detect_broken_patterns(text):
            return text

    # Parenthetical-only form: "using (EC2, RDS, S3)"
    match_p = _PAREN_TECH_RE.search(generated)
    if match_p:
        if has_aws or prefer_aws_parent:
            replacement = f"on AWS using {tech_list if not children else _format_tech_list(children)}"
        else:
            replacement = f"{match_p.group(1)} {tech_list}"
        text = (
            generated[: match_p.start()]
            + replacement
            + generated[match_p.end() :]
        )
        text = re.sub(r"\s{2,}", " ", text).strip()
        if not detect_broken_patterns(text):
            return text

    return None


def rebuild_claim_from_facts(
    *,
    original_claim: str,
    source_entry_id: str,
    facts: list[dict[str, Any]] | list[Any],
    entry_source_text: str,
    original_bullets: list[str] | None = None,
    unsupported_entities: set[str] | None = None,
    section: str = "projects",
    claim_id: str = "claim",
) -> ValidatedClaim:
    """Accept, safely rewrite, or reject a complete claim (never token-delete)."""
    original = (original_claim or "").strip()
    original_bullets = list(original_bullets or [])
    leaked = set(unsupported_entities or set())

    bound = technologies_bound_to_entry(facts, source_entry_id)
    bound |= extract_tech_mentions(entry_source_text)
    for bullet in original_bullets:
        bound |= extract_tech_mentions(bullet)

    if not original:
        return ValidatedClaim(
            id=claim_id,
            original_generated_text=original,
            final_text="",
            section=section,
            source_entry_id=source_entry_id,
            validation_status="rejected",
            validation_errors=["empty_claim"],
            repair_method="reject",
        )

    # Scope check
    ok, reason, detected_leaked = validate_bullet_tech_scope(
        original,
        source_entry_id=source_entry_id,
        facts=facts,
        entry_source_text=entry_source_text,
    )
    leaked |= detected_leaked

    impact_bad = has_unsupported_impact(original, entry_source_text)

    if ok and not impact_bad:
        ling = validate_claim_linguistics(original)
        if ling["passed"]:
            logger.info(
                "claim_stage=accept claim_id=%s entry=%s hash=%s",
                claim_id,
                source_entry_id,
                text_hash(original),
            )
            return ValidatedClaim(
                id=claim_id,
                original_generated_text=original,
                final_text=original,
                section=section,
                source_entry_id=source_entry_id,
                validation_status="accepted",
                repair_method="none",
                confidence=1.0,
            )

    # Prefer restoring the closest original bullet (complete, evidenced).
    # Also used when scope is OK but impact/metrics are unsupported.
    closest = find_closest_original_bullet(original, original_bullets)
    if closest:
        ok_c, _, leaked_c = validate_bullet_tech_scope(
            closest,
            source_entry_id=source_entry_id,
            facts=facts,
            entry_source_text=entry_source_text,
        )
        if ok_c and not has_unsupported_impact(closest, entry_source_text):
            if not detect_broken_patterns(closest):
                logger.info(
                    "claim_stage=restore_original claim_id=%s entry=%s "
                    "before=%s after=%s rejected=%s",
                    claim_id,
                    source_entry_id,
                    text_hash(original),
                    text_hash(closest),
                    sorted(leaked)[:5],
                )
                return ValidatedClaim(
                    id=claim_id,
                    original_generated_text=original,
                    final_text=closest,
                    section=section,
                    source_entry_id=source_entry_id,
                    unsupported_entities=sorted(leaked),
                    validation_status="safely_rewritten",
                    validation_errors=[reason] if not ok else [],
                    repair_method="restore_original_bullet",
                    confidence=0.95,
                )

    # Rebuild technology phrase from supported facts
    supported = _supported_techs_for_rewrite(
        generated=original,
        leaked=leaked,
        bound=bound,
        entry_source_text=entry_source_text,
        original_bullets=original_bullets,
    )
    prefer_aws = any(_norm(t) in {"ec2", "rds", "s3", "aws"} for t in supported)
    rebuilt = _rebuild_using_phrase(
        original, supported, prefer_aws_parent=prefer_aws
    )
    if rebuilt and rebuilt != original:
        ok_r, _, leaked_r = validate_bullet_tech_scope(
            rebuilt,
            source_entry_id=source_entry_id,
            facts=facts,
            entry_source_text=entry_source_text,
        )
        # Strip impact verbs only by full rewrite from original if needed
        if impact_bad or has_unsupported_impact(rebuilt, entry_source_text):
            if closest and not has_unsupported_impact(closest, entry_source_text):
                rebuilt = closest
                ok_r, leaked_r = True, set()
            else:
                rebuilt = None
        if rebuilt and ok_r and not leaked_r and not detect_broken_patterns(rebuilt):
            logger.info(
                "claim_stage=safe_rewrite claim_id=%s entry=%s "
                "before=%s after=%s rejected=%s",
                claim_id,
                source_entry_id,
                text_hash(original),
                text_hash(rebuilt),
                sorted(leaked)[:5],
            )
            return ValidatedClaim(
                id=claim_id,
                original_generated_text=original,
                final_text=rebuilt,
                section=section,
                source_entry_id=source_entry_id,
                unsupported_entities=sorted(leaked),
                validation_status="safely_rewritten",
                validation_errors=[reason] if not ok else ["unsupported_impact"] if impact_bad else [],
                repair_method="rebuild_from_supported_facts",
                confidence=0.9,
            )

    # Impact-only issue with otherwise scoped tech → restore original bullet
    if impact_bad and closest and not has_unsupported_impact(closest, entry_source_text):
        return ValidatedClaim(
            id=claim_id,
            original_generated_text=original,
            final_text=closest,
            section=section,
            source_entry_id=source_entry_id,
            unsupported_entities=sorted(leaked),
            validation_status="safely_rewritten",
            validation_errors=["unsupported_impact"],
            repair_method="restore_original_bullet",
            confidence=0.9,
        )

    # Not enough evidence for a natural sentence → reject whole claim
    logger.info(
        "claim_stage=reject claim_id=%s entry=%s before=%s rejected=%s reason=%s",
        claim_id,
        source_entry_id,
        text_hash(original),
        sorted(leaked)[:5],
        reason,
    )
    return ValidatedClaim(
        id=claim_id,
        original_generated_text=original,
        final_text="",
        section=section,
        source_entry_id=source_entry_id,
        unsupported_entities=sorted(leaked),
        validation_status="rejected",
        validation_errors=[reason or "unsupported_claim", "no_safe_rewrite"],
        repair_method="reject",
        confidence=0.0,
    )


def rewrite_skill_line(
    skill_line: str,
    *,
    allowed_techs: set[str],
) -> tuple[str | None, list[str]]:
    """Return a cleaned skill line or None if the whole line must be dropped.

    Never leaves empty slots like ``Frontend: ,``.
    """
    raw = str(skill_line or "").strip()
    if not raw:
        return None, []
    if ":" in raw:
        category, rest = raw.split(":", 1)
        atoms = [a.strip() for a in rest.split(",") if a.strip()]
    else:
        category, atoms = "", [raw]

    kept: list[str] = []
    rejected: list[str] = []
    for atom in atoms:
        techs = extract_tech_mentions(atom)
        if not techs:
            kept.append(atom)
            continue
        if all(
            t in allowed_techs
            or any(t in a or a in t for a in allowed_techs if len(a) >= 3)
            for t in techs
        ):
            kept.append(atom)
        else:
            rejected.append(atom)
    if not kept:
        return None, rejected
    if category:
        return f"{category.strip()}: {', '.join(kept)}", rejected
    return ", ".join(kept), rejected
