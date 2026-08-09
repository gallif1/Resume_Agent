"""Deterministic claim validator for tailored resume statements.

Every generated statement must be traceable to Explicit or Strongly Inferred
evidence. Weakly Inferred and Unsupported claims are rejected here — after any
LLM assist — so the model cannot silently invent experience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from intelligent_tailoring.experience_math import (
    claim_years_supported,
    estimate_years_from_text,
    extract_years_claims,
    has_inflated_years_claim,
    parse_date_range,
    years_from_experience_entries,
)
from intelligent_tailoring.schemas import (
    ALLOWED_INFERENCE_IN_RESUME,
    ChangeLogItem,
    InferredCompetency,
    TailoredResume,
    ValidationWarning,
    normalize_inference_category,
)
from match_tailor_service import SourceEvidence, skill_supported_by_source

_TOKEN_RE = re.compile(r"[a-z0-9#+.]{2,}|[\u0590-\u05FF]{2,}", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_RANGE_FIND_RE = re.compile(
    r"((?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2})"
    r"\s*[-–—to]+\s*"
    r"((?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2}|present|current|now|today|היום|כיום)",
    re.IGNORECASE,
)

# Hard-reject patterns — regression cases that must never reach export.
_HARD_REJECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\bproven\s+ability\s+to\s+lead\s+projects?\s+from\s+inception\s+to\s+"
            r"(?:deployment|delivery|completion|production)\b",
            re.I,
        ),
        "unsupported_professional_leadership",
    ),
    (
        re.compile(
            r"\bled\s+projects?\s+from\s+inception\s+to\s+"
            r"(?:deployment|delivery|completion|production)\b",
            re.I,
        ),
        "unsupported_professional_leadership",
    ),
    (
        re.compile(
            r"\b(?:over|more\s+than|at\s+least)\s+"
            r"(?:three|3|four|4|five|5|\d+)\s*\+?\s*years?\s+"
            r"(?:of\s+)?(?:expertise|experience|professional\s+experience)\b",
            re.I,
        ),
        "inflated_years_phrase",
    ),
    (
        re.compile(
            r"\b(?:full[\s-]?stack\s+(?:engineer|developer))\s+with\s+"
            r"(?:over\s+)?(?:three|3|\d+)\s*\+?\s*years?\b",
            re.I,
        ),
        "inflated_years_with_title",
    ),
    (
        re.compile(
            r"\b(?:managed|led|supervised|oversaw|headed)\s+"
            r"(?:a\s+)?(?:team|group|squad|crew)\s+of\s+"
            r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
            re.I,
        ),
        "unsupported_team_headcount",
    ),
]

# Organization / school suffixes used to detect invented employers in prose.
_ORG_SUFFIX = (
    r"University|College|Institute|School|Ltd|Inc|Corp|Corporation|Labs|"
    r"Laboratory|Technologies|Hospital|Center|Centre|אוניברסיטת|מכללת"
)
_ORG_PHRASE_RE = re.compile(
    rf"\b((?:[A-Z][\w&'’.-]+|[\u0590-\u05FF][\w\u0590-\u05FF&'’.-]*)"
    rf"(?:\s+(?:[A-Z][\w&'’.-]+|[\u0590-\u05FF][\w\u0590-\u05FF&'’.-]*|of|and|&)){{0,6}}"
    rf"\s+(?:{_ORG_SUFFIX}))\b"
)
_ORG_GENERIC_TOKENS = frozenset(
    {
        "university",
        "college",
        "institute",
        "school",
        "ltd",
        "inc",
        "corp",
        "corporation",
        "labs",
        "laboratory",
        "technologies",
        "hospital",
        "center",
        "centre",
        "the",
        "of",
        "and",
        "company",
        "group",
        "אוניברסיטת",
        "מכללת",
    }
)
_TITLE_GENERIC_TOKENS = frozenset(
    {
        "project",
        "lead",
        "senior",
        "junior",
        "engineer",
        "developer",
        "manager",
        "specialist",
        "analyst",
        "intern",
        "assistant",
        "coordinator",
        "tutor",
        "programming",
        "software",
        "backend",
        "frontend",
        "fullstack",
        "full",
        "stack",
        "technical",
        "support",
        "student",
        "research",
        "associate",
        "principal",
        "staff",
        "head",
        "director",
        "consultant",
        "architect",
        "capstone",
        "platform",
        "team",
        "member",
        "members",
    }
)

# Outcome nouns that require explicit source support (not feature intent).
_UNSUPPORTED_OUTCOME_NOUNS = re.compile(
    r"\b("
    r"customer\s+satisfaction|user\s+engagement|system\s+scalability|"
    r"system\s+reliability|team\s+workflows?|streamlin(?:e|ed|ing)\s+delivery|"
    r"production[- ]grade\s+(?:ownership|architecture|applications?)|"
    r"business\s+impact|revenue\s+growth"
    r")\b",
    re.I,
)

# Strong ownership / seniority verbs that need professional evidence.
_STRONG_OWNERSHIP_RE = re.compile(
    r"\b(architected|owned|drove|transformed|spearheaded|"
    r"extensive\s+experience|deep\s+expertise|expert\s+in)\b",
    re.I,
)

# AI coding assistants — only allowed when present in verified candidate data.
_AI_TOOL_RE = re.compile(
    r"\b(cursor|chatgpt|claude|github\s*copilot|copilot)\b",
    re.I,
)

# Words that do not count as entity evidence on their own.
_STOP = frozenset(
    {
        "the", "and", "or", "a", "an", "to", "of", "in", "for", "with", "on", "at",
        "by", "from", "as", "is", "are", "was", "were", "be", "been", "this", "that",
        "using", "used", "use", "via", "into", "over", "under", "about", "across",
        "experience", "experienced", "responsible", "worked", "work", "working",
        "helped", "help", "including", "include", "included", "team", "project",
        "role", "skills", "skill", "years", "year", "strong", "knowledge",
        "professional", "dedicated", "motivated", "results", "driven", "proven",
        "building", "built", "developing", "developed", "creating", "created",
        "supporting", "supported", "implementing", "implemented", "providing",
        "provided", "managing", "managed", "leading", "tools", "technologies",
        "backend", "frontend", "fullstack", "full", "stack", "cloud", "data",
        "candidate", "candidates", "roles", "applications", "services",
        "ניסיון", "עבודה", "אחריות", "צוות", "פרויקט", "תפקיד",
    }
)


@dataclass
class ClaimValidationResult:
    cleaned_resume: TailoredResume
    warnings: list[ValidationWarning] = field(default_factory=list)
    rejected_statements: list[str] = field(default_factory=list)
    change_log: list[ChangeLogItem] = field(default_factory=list)
    inferred_competencies: list[InferredCompetency] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cleaned_resume": self.cleaned_resume.to_dict(),
            "warnings": [w.to_dict() for w in self.warnings],
            "rejected_statements": list(self.rejected_statements),
            "change_log": [c.to_dict() for c in self.change_log],
            "inferred_competencies": [c.to_dict() for c in self.inferred_competencies],
        }


def _tokens(text: str) -> set[str]:
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text or "")
        if t.lower() not in _STOP and len(t) > 1
    }


def _entity_tokens(text: str) -> set[str]:
    """Tokens that look like proper nouns / tech / employers (stricter).

    Sentence-start capitalization of ordinary English verbs/nouns is ignored.
    We keep ALLCAPS acronyms, CamelCase products, tech-like tokens, and
    multi-word Title-Case organization phrases (e.g. Tel Aviv University).
    """
    tokens = set()
    for raw in re.findall(
        r"\b(?:[A-Z]{2,}[a-z0-9+]*)\b|"  # AWS, PostgreSQL-ish acronyms
        r"\b[A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9+#.]*\b|"  # CamelCase / FastAPI
        r"\b(?:[A-Z][a-z]+(?:\.[A-Za-z]+)+)\b",  # Node.js style
        text or "",
    ):
        low = raw.lower()
        if low not in _STOP and len(low) > 1:
            tokens.add(low)
    # C++ / C# — word-boundary after "+" is unreliable, so match explicitly.
    for raw in re.findall(r"\b[cC]\s*(?:\+\+|\#)\b", text or ""):
        tokens.add(re.sub(r"\s+", "", raw.lower()))
    # Multi-word Title Case spans (employers / products), plus each distinctive part.
    for raw in re.findall(
        r"\b(?:[A-Z][a-zA-Z0-9&'’.-]+(?:\s+[A-Z][a-zA-Z0-9&'’.-]+)+)\b",
        text or "",
    ):
        low = raw.lower()
        if low not in _STOP:
            tokens.add(low)
        for part in re.findall(r"[a-z0-9]{2,}", low):
            if part not in _STOP and part not in _ORG_GENERIC_TOKENS:
                tokens.add(part)
    for org in extract_organization_phrases(text or ""):
        tokens.add(org.lower())
        for part in _tokens(org):
            if part not in _ORG_GENERIC_TOKENS:
                tokens.add(part)
    # Hebrew multi-char tokens length>=3 kept as soft entities
    for he in re.findall(r"[\u0590-\u05FF]{3,}", text or ""):
        tokens.add(he.lower())
    return tokens


def _norm_date_text(text: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        (text or "").lower().replace("–", "-").replace("—", "-").replace("−", "-"),
    ).strip()


def extract_organization_phrases(text: str) -> list[str]:
    """Return employer/school-like phrases from free text."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _ORG_PHRASE_RE.finditer(text or ""):
        phrase = re.sub(r"\s+", " ", match.group(1)).strip()
        key = phrase.lower()
        if key and key not in seen:
            seen.add(key)
            found.append(phrase)
    return found


def organization_supported(name: str, source_text: str) -> bool:
    """True when an employer/school identity is grounded in the source resume.

    Shared generic tokens (``university``, ``college``, …) are not enough —
    distinctive tokens such as ``Hai`` vs ``Aviv`` must match.
    """
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        return True
    source = source_text or ""
    if not source.strip():
        return False
    if name.lower() in source.lower():
        return True

    distinctive = {t for t in _tokens(name) if t not in _ORG_GENERIC_TOKENS}
    if not distinctive:
        return name.lower() in source.lower()

    source_l = source.lower()
    # Prefer matching against organization phrases extracted from the source.
    source_orgs = extract_organization_phrases(source)
    if not source_orgs:
        # Fall back to any multi-word Title-Case spans and pipe/meta segments.
        source_orgs = re.findall(
            r"\b(?:[A-Z][a-zA-Z0-9&'’.-]+(?:\s+[A-Z][a-zA-Z0-9&'’.-]+)+)\b",
            source,
        )
        source_orgs += re.findall(
            r"([\u0590-\u05FF][\w\u0590-\u05FF&'’.-]*(?:\s+[\u0590-\u05FF][\w\u0590-\u05FF&'’.-]*)+)",
            source,
        )

    for org in source_orgs:
        org_tokens = {t for t in _tokens(org) if t not in _ORG_GENERIC_TOKENS}
        if distinctive and distinctive == org_tokens:
            return True
        if distinctive and distinctive.issubset(org_tokens) and len(distinctive) >= 2:
            return True
        # Single-token companies ("Acme", "Google") — exact token match in org/source
        if len(distinctive) == 1:
            token = next(iter(distinctive))
            if token in org_tokens or re.search(rf"\b{re.escape(token)}\b", source_l):
                # Avoid matching only the generic half of a different school.
                if token in _ORG_GENERIC_TOKENS:
                    continue
                # Require the token to appear in an org-like context or exact.
                if token in org_tokens or name.lower() in source_l:
                    return True

    # Last resort: every distinctive token appears, and they co-occur near each other.
    if not distinctive.issubset(_tokens(source)):
        return False
    if len(distinctive) == 1:
        token = next(iter(distinctive))
        return bool(re.search(rf"\b{re.escape(token)}\b", source_l))

    # Multi-token invented orgs like "Tel Aviv" must not pass via scattered "Tel"+"University".
    span = re.search(
        rf"\b{re.escape(next(iter(sorted(distinctive))))}\b.{{0,40}}\b",
        source_l,
    )
    if not span:
        return False
    window = source_l[max(0, span.start() - 20) : span.end() + 40]
    return all(re.search(rf"\b{re.escape(t)}\b", window) for t in distinctive)


def dates_supported(dates: str, source_text: str) -> bool:
    """True when a dates field matches an explicit range/year span in the source."""
    dates = re.sub(r"\s+", " ", (dates or "").strip())
    if not dates:
        return True
    source = source_text or ""
    if not source.strip():
        return False
    if _norm_date_text(dates) in _norm_date_text(source):
        return True

    years = _YEAR_RE.findall(dates)
    if not years:
        return True
    source_years = set(_YEAR_RE.findall(source))
    if not set(years).issubset(source_years):
        return False

    start, end = parse_date_range(dates)
    if start is None:
        return set(years).issubset(source_years)

    source_ranges: list[tuple[date | None, date | None]] = []
    for match in _RANGE_FIND_RE.finditer(source):
        source_ranges.append(parse_date_range(match.group(0)))
    # Also accept year-only anchors listed next to roles ("2024 – 2025").
    for match in re.finditer(
        r"(?:19|20)\d{2}\s*[-–—]\s*(?:(?:19|20)\d{2}|present|current)",
        source,
        flags=re.I,
    ):
        source_ranges.append(parse_date_range(match.group(0)))

    for s_start, s_end in source_ranges:
        if s_start is None:
            continue
        if s_start.year != start.year:
            continue
        if end is None or s_end is None:
            return True
        if end.year == s_end.year:
            return True
    return False


def role_title_supported(title: str, source_text: str) -> bool:
    """True when an experience title is grounded (not a near-miss rewrite)."""
    title = re.sub(r"\s+", " ", (title or "").strip())
    if not title:
        return True
    source = source_text or ""
    if title.lower() in source.lower():
        return True
    distinctive = {
        t for t in _tokens(title) if t not in _TITLE_GENERIC_TOKENS and t not in _STOP
    }
    if not distinctive:
        title_tokens = _tokens(title)
        if not title_tokens:
            return True
        shared = title_tokens & _tokens(source)
        return len(shared) / max(len(title_tokens), 1) >= 0.7
    return distinctive.issubset(_tokens(source))


def hard_reject_claim(
    statement: str,
    *,
    source_text: str,
    resume_years: float | None = None,
    professional_years: float | None = None,
) -> tuple[bool, str]:
    """Return (reject?, reason) for absolute regression blocks.

    These checks run before any evidence-map / overlap path so later agents
    cannot resurrect blocked claims via synonym rephrasing alone.
    """
    statement = (statement or "").strip()
    if not statement:
        return False, ""

    for pattern, reason in _HARD_REJECT_PATTERNS:
        match = pattern.search(statement)
        if not match:
            continue
        # Allow only when the exact leadership / headcount phrase already exists
        if reason in {
            "unsupported_professional_leadership",
            "unsupported_team_headcount",
        }:
            if pattern.search(source_text or ""):
                continue
            if match.group(0).lower() in (source_text or "").lower():
                continue
            return True, reason
        if reason.startswith("inflated_years"):
            inflated, detail = has_inflated_years_claim(
                statement,
                resume_years=resume_years,
                professional_years=professional_years,
            )
            if inflated:
                return True, detail or reason
            # Phrase present but years somehow supported — still block
            # "expertise" inflation when professional years are missing.
            if professional_years is None or professional_years < 3.0:
                return True, reason
            continue
        return True, reason

    # Worded/numeric years inflation even without the hard phrase templates
    inflated, detail = has_inflated_years_claim(
        statement,
        resume_years=resume_years,
        professional_years=professional_years,
    )
    if inflated:
        return True, detail

    # Unsupported business/outcome nouns not grounded in source
    for match in _UNSUPPORTED_OUTCOME_NOUNS.finditer(statement):
        phrase = match.group(0).lower()
        if phrase not in (source_text or "").lower():
            return True, f"unsupported_outcome:{phrase}"

    # AI assistant tools only when verified in candidate source data
    for match in _AI_TOOL_RE.finditer(statement):
        tool = match.group(0).lower().replace(" ", "")
        src_l = re.sub(r"\s+", "", (source_text or "").lower())
        # Allow "claude" only as tool mention if present; never inject from JD
        variants = {
            "cursor": ("cursor",),
            "chatgpt": ("chatgpt", "chat gpt"),
            "claude": ("claude",),
            "githubcopilot": ("githubcopilot", "copilot"),
            "copilot": ("copilot", "githubcopilot"),
        }
        allowed = variants.get(tool, (tool,))
        if not any(v.replace(" ", "") in src_l for v in allowed):
            return True, f"unverified_ai_tool:{match.group(0)}"

    # Absolute expertise claims still hard-reject without source support.
    ownership = _STRONG_OWNERSHIP_RE.search(statement)
    if ownership:
        verb = ownership.group(0).lower()
        src_l = (source_text or "").lower()
        if verb not in src_l and (
            verb.startswith("extensive")
            or verb.startswith("deep")
            or verb.startswith("expert")
        ):
            return True, f"unsupported_ownership:{verb}"

    # Invented employers/schools inside free-text claims
    for org in extract_organization_phrases(statement):
        if not organization_supported(org, source_text or ""):
            return True, f"unsupported_organization:{org}"

    return False, ""


def statement_supported_by_evidence(
    statement: str,
    *,
    source_text: str,
    evidence_map: Iterable[dict[str, Any]] | None = None,
    strongly_inferred: Iterable[InferredCompetency] | None = None,
    min_token_overlap: float = 0.45,
    resume_years: float | None = None,
    professional_years: float | None = None,
    rejected_registry: Any | None = None,
) -> tuple[bool, str]:
    """Return (supported?, reason).

    Support paths:
    1. Explicit — statement tokens appear in source resume
    2. Strongly Inferred — statement matches an approved inferred competency
    3. Evidence map entry links the statement to resume evidence
    """
    statement = (statement or "").strip()
    if not statement:
        return True, "empty"

    # Previously rejected claims cannot return
    if rejected_registry is not None and getattr(rejected_registry, "contains", None):
        if rejected_registry.contains(statement):
            return False, "previously_rejected_claim"

    # Hard checks FIRST — evidence-map / inference paths must never bypass them.
    from intelligent_tailoring.scope_validator import has_unsupported_impact

    reject, reason = hard_reject_claim(
        statement,
        source_text=source_text,
        resume_years=resume_years,
        professional_years=professional_years,
    )
    if reject:
        return False, reason

    if has_unsupported_impact(statement, source_text):
        return False, "unsupported_impact_claim"

    evidence = SourceEvidence.build(source_text)
    if not evidence:
        return False, "empty_source_resume"

    novel_entities = _entity_tokens(statement) - _entity_tokens(source_text)
    suspicious = {
        e
        for e in novel_entities
        if e not in _STOP
        and e not in _ORG_GENERIC_TOKENS
        and e not in _TITLE_GENERIC_TOKENS
        and len(e) > 2
        and not skill_supported_by_source(e, source_text)
    }
    if suspicious:
        still = {e for e in suspicious if not evidence.has_word(e)}
        if still:
            return False, f"unsupported_entities:{', '.join(sorted(still)[:5])}"

    # Novel tech lexicon hits (Docker, C++, Kubernetes, …) even when entity
    # capitalization patterns miss them.
    from intelligent_tailoring.scope_validator import extract_tech_mentions

    novel_tech = extract_tech_mentions(statement) - extract_tech_mentions(source_text)
    novel_tech = {
        t for t in novel_tech if t and not skill_supported_by_source(t, source_text)
    }
    if novel_tech:
        return False, f"unsupported_tech:{', '.join(sorted(novel_tech)[:5])}"

    # Path 2: approved strongly-inferred competencies (exact / contained statement only)
    stmt_l = statement.lower()
    for inf in strongly_inferred or []:
        if not inf.statement:
            continue
        if stmt_l == inf.statement.lower() or inf.statement.lower() in stmt_l:
            if inf.inference_category == "Strongly Inferred" and inf.supporting_evidence:
                # Supporting evidence itself must appear in the source resume.
                if not skill_supported_by_source(
                    inf.supporting_evidence[:80], source_text
                ) and not (
                    _tokens(inf.supporting_evidence) & _tokens(source_text)
                ):
                    continue
                return True, f"strongly_inferred:{inf.ontology_rule_id or 'manual'}"

    # Path 3: evidence map — require category + evidence, still subject to hard checks above
    for entry in evidence_map or []:
        generated = str(entry.get("generated_statement") or entry.get("statement") or "")
        category = normalize_inference_category(entry.get("inference_category"))
        map_evidence = str(
            entry.get("supporting_evidence") or entry.get("resume_evidence") or ""
        )
        if not generated:
            continue
        if generated.lower() in stmt_l or stmt_l in generated.lower():
            if category in ALLOWED_INFERENCE_IN_RESUME and map_evidence.strip():
                return True, f"evidence_map:{category}"

    stmt_tokens = _tokens(statement)
    if not stmt_tokens:
        return True, "no_content_tokens"

    # Skill-like short statements / atoms — do not treat generic stem overlap
    # ("learn" ⊂ "learning") as evidence for a different skill (scikit-learn).
    if len(stmt_tokens) <= 4:
        if skill_supported_by_source(statement, source_text):
            return True, "skill_supported"
        # Hyphenated / single-atom tech names must be evidenced as skills, not
        # via fuzzy token overlap with unrelated words.
        compact = re.sub(r"[^a-z0-9+#.]+", "", statement.lower())
        if compact and (
            "-" in statement
            or " " not in statement.strip()
            or len(stmt_tokens) <= 2
        ):
            return False, "unsupported_skill_or_claim"
        overlap = sum(1 for t in stmt_tokens if evidence.has_word(t))
        if overlap / max(len(stmt_tokens), 1) >= 0.5:
            return True, "token_overlap"
        source_tokens = _tokens(source_text)
        shared = stmt_tokens & source_tokens
        if len(shared) >= 2 and len(shared) / max(len(stmt_tokens), 1) >= 0.4:
            return True, f"rephrased_overlap:{len(shared)}"
        return False, "unsupported_skill_or_claim"

    source_tokens = _tokens(source_text)
    overlap = len(stmt_tokens & source_tokens) / max(len(stmt_tokens), 1)
    if overlap >= min_token_overlap:
        return True, f"token_overlap:{overlap:.2f}"
    # Rephrased bullets often introduce synonyms; require enough shared content nouns
    if len(stmt_tokens & source_tokens) >= 2 and overlap >= max(0.28, min_token_overlap - 0.15):
        return True, f"rephrased_overlap:{overlap:.2f}"
    return False, f"insufficient_overlap:{overlap:.2f}"


def _filter_bullet_list(
    bullets: list[Any],
    *,
    source_text: str,
    evidence_map: list[dict[str, Any]],
    strongly_inferred: list[InferredCompetency],
    warnings: list[ValidationWarning],
    rejected: list[str],
    resume_years: float | None = None,
    professional_years: float | None = None,
    rejected_registry: Any | None = None,
) -> list[str]:
    kept: list[str] = []
    for raw in bullets or []:
        text = str(raw).strip()
        if not text:
            continue
        ok, reason = statement_supported_by_evidence(
            text,
            source_text=source_text,
            evidence_map=evidence_map,
            strongly_inferred=strongly_inferred,
            resume_years=resume_years,
            professional_years=professional_years,
            rejected_registry=rejected_registry,
        )
        if ok:
            kept.append(text)
        else:
            rejected.append(text)
            if rejected_registry is not None:
                rejected_registry.add(
                    text, reason=reason, source_agent="claim_validation"
                )
            warnings.append(
                ValidationWarning(
                    statement=text,
                    reason=f"Rejected by claim validator ({reason})",
                    inference_category="Unsupported",
                )
            )
    return kept


def validate_claims(
    *,
    original_resume_text: str,
    tailored_resume: TailoredResume | dict[str, Any],
    evidence_map: list[dict[str, Any]] | None = None,
    change_log: list[ChangeLogItem] | list[dict[str, Any]] | None = None,
    inferred_competencies: list[InferredCompetency] | list[dict[str, Any]] | None = None,
    job_requirements: dict[str, Any] | None = None,  # noqa: ARG001 — reserved for future
    rejected_registry: Any | None = None,
) -> ClaimValidationResult:
    """Strip unsupported statements from the tailored resume and emit warnings."""
    if isinstance(tailored_resume, dict):
        resume = TailoredResume(
            professional_summary=str(
                tailored_resume.get("professional_summary")
                or tailored_resume.get("summary")
                or ""
            ),
            skills=[str(s) for s in (tailored_resume.get("skills") or [])],
            experience=[
                e for e in (tailored_resume.get("experience") or []) if isinstance(e, dict)
            ],
            projects=[
                p for p in (tailored_resume.get("projects") or []) if isinstance(p, dict)
            ],
            education=[
                e for e in (tailored_resume.get("education") or []) if isinstance(e, dict)
            ],
            certifications=list(tailored_resume.get("certifications") or []),
            professional_title=str(tailored_resume.get("professional_title") or ""),
        )
    else:
        resume = TailoredResume(
            professional_summary=tailored_resume.professional_summary,
            skills=list(tailored_resume.skills),
            experience=[dict(e) for e in tailored_resume.experience],
            projects=[dict(p) for p in tailored_resume.projects],
            education=[dict(e) for e in tailored_resume.education],
            certifications=list(tailored_resume.certifications),
            professional_title=tailored_resume.professional_title,
        )

    evidence_map = list(evidence_map or [])
    warnings: list[ValidationWarning] = []
    rejected: list[str] = []

    # Normalize inferred competencies
    strong: list[InferredCompetency] = []
    for raw in inferred_competencies or []:
        if isinstance(raw, InferredCompetency):
            if raw.inference_category == "Strongly Inferred" and raw.supporting_evidence:
                strong.append(raw)
            elif raw.inference_category not in ALLOWED_INFERENCE_IN_RESUME:
                warnings.append(
                    ValidationWarning(
                        statement=raw.statement,
                        reason="Dropped non-strong inference from competencies list",
                        inference_category=raw.inference_category,
                    )
                )
            continue
        if not isinstance(raw, dict):
            continue
        cat = normalize_inference_category(
            raw.get("inference_category") or "Strongly Inferred"
        )
        stmt = str(raw.get("statement") or "").strip()
        evidence = str(raw.get("supporting_evidence") or "").strip()
        reasoning = str(raw.get("reasoning") or raw.get("reason") or "").strip()
        if cat != "Strongly Inferred" or not stmt or not evidence:
            if stmt:
                warnings.append(
                    ValidationWarning(
                        statement=stmt,
                        reason="Dropped weak/unsupported inferred competency",
                        inference_category=cat,
                    )
                )
            continue
        strong.append(
            InferredCompetency(
                statement=stmt,
                supporting_evidence=evidence,
                reasoning=reasoning or "ontology/inference",
                confidence_score=float(raw.get("confidence_score") or 0.0),
                related_requirement=str(
                    raw.get("related_requirement")
                    or raw.get("related_job_requirement")
                    or ""
                ),
                ontology_rule_id=str(raw.get("ontology_rule_id") or ""),
            )
        )

    # Filter change_log: reject Weakly/Unsupported from acceptance into resume
    cleaned_log: list[ChangeLogItem] = []
    for raw in change_log or []:
        if isinstance(raw, ChangeLogItem):
            item = raw
        elif isinstance(raw, dict):
            item = ChangeLogItem(
                original_text=str(raw.get("original_text") or ""),
                new_text=str(raw.get("new_text") or ""),
                reason=str(raw.get("reason") or ""),
                supporting_evidence=str(raw.get("supporting_evidence") or ""),
                related_job_requirement=str(raw.get("related_job_requirement") or ""),
                inference_category=normalize_inference_category(
                    raw.get("inference_category")
                ),
                confidence_score=float(raw.get("confidence_score") or 0.0),
                accepted=raw.get("accepted"),
            )
        else:
            continue
        if item.inference_category not in ALLOWED_INFERENCE_IN_RESUME:
            if item.new_text:
                rejected.append(item.new_text)
                warnings.append(
                    ValidationWarning(
                        statement=item.new_text,
                        reason=(
                            f"change_log entry marked {item.inference_category}; "
                            "not applied to tailored resume"
                        ),
                        inference_category=item.inference_category,
                    )
                )
            # Keep in log for audit but force accepted=False
            item.accepted = False
            cleaned_log.append(item)
            continue
        if item.inference_category == "Strongly Inferred" and not item.supporting_evidence:
            item.inference_category = "Unsupported"
            item.accepted = False
            warnings.append(
                ValidationWarning(
                    statement=item.new_text,
                    reason="Strongly Inferred change lacked supporting_evidence",
                    inference_category="Unsupported",
                )
            )
            cleaned_log.append(item)
            continue
        cleaned_log.append(item)

    source = original_resume_text or ""
    # Professional years = employment entries only (never academic/project span)
    professional_years = years_from_experience_entries(resume.experience)
    resume_years = professional_years
    if resume_years is None:
        resume_years = estimate_years_from_text(source)

    # Summary
    if resume.professional_summary:
        ok, reason = statement_supported_by_evidence(
            resume.professional_summary,
            source_text=source,
            evidence_map=evidence_map,
            strongly_inferred=strong,
            min_token_overlap=0.35,
            resume_years=resume_years,
            professional_years=professional_years,
            rejected_registry=rejected_registry,
        )
        # Years claims inside summary — prefer professional employment years
        for claimed in extract_years_claims(resume.professional_summary):
            if not claim_years_supported(
                claimed, resume_years=professional_years or resume_years
            ):
                ok = False
                reason = f"unsupported_years_claim:{claimed}"
                break
        if not ok:
            warnings.append(
                ValidationWarning(
                    statement=resume.professional_summary,
                    reason=f"Summary rejected ({reason})",
                    inference_category="Unsupported",
                )
            )
            rejected.append(resume.professional_summary)
            if rejected_registry is not None:
                rejected_registry.add(
                    resume.professional_summary,
                    reason=reason,
                    source_agent="claim_validation",
                    section="summary",
                )
            resume.professional_summary = ""

    # Skills — reuse existing unsupported-skill strip semantics
    cleaned_skills: list[str] = []
    for skill in resume.skills:
        text = str(skill).strip()
        if not text:
            continue
        # Grouped "Category: a, b" — validate atoms
        if ":" in text:
            category, rest = text.split(":", 1)
            atoms = [a.strip() for a in rest.split(",") if a.strip()]
            kept_atoms = []
            for atom in atoms:
                if skill_supported_by_source(atom, source) or any(
                    atom.lower() in inf.statement.lower() for inf in strong
                ):
                    kept_atoms.append(atom)
                else:
                    # Grouped skill atoms must be evidenced as skills — never via
                    # prose overlap ("learn" matching "Machine Learning").
                    rejected.append(atom)
                    warnings.append(
                        ValidationWarning(
                            statement=atom,
                            reason="Skill not evidenced in original resume",
                            inference_category="Unsupported",
                        )
                    )
            if kept_atoms:
                cleaned_skills.append(f"{category.strip()}: {', '.join(kept_atoms)}")
            continue
        if skill_supported_by_source(text, source):
            cleaned_skills.append(text)
            continue
        ok, reason = statement_supported_by_evidence(
            text,
            source_text=source,
            evidence_map=evidence_map,
            strongly_inferred=strong,
        )
        if ok:
            cleaned_skills.append(text)
        else:
            rejected.append(text)
            warnings.append(
                ValidationWarning(
                    statement=text,
                    reason=f"Skill not evidenced ({reason})",
                    inference_category="Unsupported",
                )
            )
    resume.skills = cleaned_skills

    # Experience / projects bullets
    cleaned_experience: list[dict[str, Any]] = []
    for entry in resume.experience:
        company = str(entry.get("company") or "").strip()
        title = str(entry.get("title") or "").strip()
        dates = str(entry.get("dates") or entry.get("date") or "").strip()
        identity_rejected = False
        if company and not organization_supported(company, source):
            warnings.append(
                ValidationWarning(
                    statement=company,
                    reason="Experience company not found in original resume",
                    inference_category="Unsupported",
                )
            )
            rejected.append(company)
            entry["company"] = ""
            identity_rejected = True
        if title and not role_title_supported(title, source):
            warnings.append(
                ValidationWarning(
                    statement=title,
                    reason="Experience title not found in original resume",
                    inference_category="Unsupported",
                )
            )
            rejected.append(title)
            entry["title"] = ""
            identity_rejected = True
        if dates and not dates_supported(dates, source):
            warnings.append(
                ValidationWarning(
                    statement=dates,
                    reason="Experience dates not found in original resume",
                    inference_category="Unsupported",
                )
            )
            rejected.append(dates)
            if "dates" in entry:
                entry["dates"] = ""
            if "date" in entry:
                entry["date"] = ""
            identity_rejected = True
        # Drop shells whose employer/title/dates were fabricated — keeping orphan
        # bullets under a blank identity invites re-attribution errors later.
        if identity_rejected and not (
            str(entry.get("company") or "").strip()
            or str(entry.get("title") or "").strip()
        ):
            for bullet in list(entry.get("bullets") or []):
                text = str(bullet).strip()
                if text:
                    rejected.append(text)
            continue
        entry["bullets"] = _filter_bullet_list(
            list(entry.get("bullets") or []),
            source_text=source,
            evidence_map=evidence_map,
            strongly_inferred=strong,
            warnings=warnings,
            rejected=rejected,
            resume_years=resume_years,
            professional_years=professional_years,
            rejected_registry=rejected_registry,
        )
        cleaned_experience.append(entry)
    resume.experience = cleaned_experience

    for entry in resume.projects:
        name = str(entry.get("name") or "").strip()
        if name and name.lower() not in source.lower():
            if not (_tokens(name) & _tokens(source)):
                warnings.append(
                    ValidationWarning(
                        statement=name,
                        reason="Project name not found in original resume",
                        inference_category="Unsupported",
                    )
                )
                rejected.append(name)
                entry["name"] = ""
        desc = str(entry.get("description") or "").strip()
        if desc:
            ok, reason = statement_supported_by_evidence(
                desc,
                source_text=source,
                evidence_map=evidence_map,
                strongly_inferred=strong,
                resume_years=resume_years,
                professional_years=professional_years,
                rejected_registry=rejected_registry,
            )
            if not ok:
                warnings.append(
                    ValidationWarning(
                        statement=desc,
                        reason=f"Project description rejected ({reason})",
                        inference_category="Unsupported",
                    )
                )
                rejected.append(desc)
                if rejected_registry is not None:
                    rejected_registry.add(
                        desc,
                        reason=reason,
                        source_agent="claim_validation",
                        section="projects",
                    )
                entry["description"] = ""
        # Preserve academic context — reject bullets that strip "academic"/"capstone"
        # when the project name indicates academic work.
        name_l = name.lower()
        academic_project = bool(
            re.search(r"\b(capstone|thesis|academic|פרויקט\s*גמר)\b", name_l)
        )
        entry["bullets"] = _filter_bullet_list(
            list(entry.get("bullets") or []),
            source_text=source,
            evidence_map=evidence_map,
            strongly_inferred=strong,
            warnings=warnings,
            rejected=rejected,
            resume_years=resume_years,
            professional_years=professional_years,
            rejected_registry=rejected_registry,
        )
        if academic_project:
            # Tag description so later writers keep academic framing
            if entry.get("description") and "academic" not in str(
                entry.get("description") or ""
            ).lower() and "capstone" not in str(
                entry.get("description") or ""
            ).lower():
                entry["context_type"] = "academic"
            else:
                entry["context_type"] = "academic"

    # Education / certifications — institution identity + dates must exist in source
    cleaned_edu: list[dict[str, Any]] = []
    for entry in resume.education:
        institution = str(entry.get("institution") or "").strip()
        degree = str(entry.get("degree") or "").strip()
        dates = str(entry.get("dates") or entry.get("date") or "").strip()
        blob = f"{institution} {degree}".strip()
        if institution and not organization_supported(institution, source):
            warnings.append(
                ValidationWarning(
                    statement=institution,
                    reason="Education institution not found in original resume",
                    inference_category="Unsupported",
                )
            )
            rejected.append(institution)
            continue
        if blob and blob.lower() not in source.lower():
            # Degree wording may be lightly rephrased; require degree tokens overlap.
            if degree and not (_tokens(degree) & _tokens(source)):
                warnings.append(
                    ValidationWarning(
                        statement=blob,
                        reason="Education entry not found in original resume",
                        inference_category="Unsupported",
                    )
                )
                rejected.append(blob)
                continue
        if dates and not dates_supported(dates, source):
            warnings.append(
                ValidationWarning(
                    statement=dates,
                    reason="Education dates not found in original resume",
                    inference_category="Unsupported",
                )
            )
            rejected.append(dates)
            if "dates" in entry:
                entry["dates"] = ""
            if "date" in entry:
                entry["date"] = ""
        cleaned_edu.append(entry)
    resume.education = cleaned_edu

    cleaned_certs: list[Any] = []
    for cert in resume.certifications:
        text = cert if isinstance(cert, str) else str(
            (cert or {}).get("name") or (cert or {}).get("title") or cert
        )
        text = str(text).strip()
        if not text:
            continue
        if text.lower() in source.lower() or (_tokens(text) & _tokens(source)):
            cleaned_certs.append(cert)
        else:
            warnings.append(
                ValidationWarning(
                    statement=text,
                    reason="Certification not found in original resume",
                    inference_category="Unsupported",
                )
            )
            rejected.append(text)
    resume.certifications = cleaned_certs

    return ClaimValidationResult(
        cleaned_resume=resume,
        warnings=warnings,
        rejected_statements=rejected,
        change_log=cleaned_log,
        inferred_competencies=strong,
    )
