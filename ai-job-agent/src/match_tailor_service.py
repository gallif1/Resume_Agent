"""Requirement-extraction / gap-analysis / tailoring engine on top of GPT-4o.

The model runs the three-phase contract in ``match_tailor_prompt``; this module
owns everything the backend must not delegate to a language model:

* the rubric score is re-computed from the per-requirement statuses, so a model
  that "feels" generous cannot inflate ``realistic_match_score``;
* the Hard Cap Rule is re-applied from the job title's core tokens, catching
  prompt drift where a missing core requirement is scored like a soft gap;
* skills with no support in the source resume are stripped from the tailored CV;
* the response is coerced to the full schema so callers never see missing keys.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ai_client import (
    OpenAIAPIError,
    call_openai_json,
    clamp_score,
    is_ai_available,
    truncate_text,
)
from config import (
    OPENAI_CV_MAX_CHARS,
    OPENAI_JOB_MAX_CHARS,
    OPENAI_TAILOR_MODEL,
)
from job_analyzer import JobProfile, parse_stored_job_profile
from match_tailor_prompt import (
    CANONICAL_SKILL_CATEGORIES,
    CORE_GAP_SCORE_CAP,
    HARD_REQUIREMENT_WEIGHT,
    HARD_STATUS_WEIGHTS,
    HONEST_TITLE_HARD_SCORE_THRESHOLD,
    JSON_RETRY_NOTE,
    MATCH_TAILOR_PROMPT_VERSION,
    MATCH_TAILOR_SYSTEM_PROMPT,
    MATCH_TAILOR_TEMPERATURE,
    MULTI_CORE_GAP_SCORE_CAP,
    REQUIRED_TOP_LEVEL_KEYS,
    SOFT_REQUIREMENT_WEIGHT,
    SOFT_STATUS_WEIGHTS,
    VALID_RECOMMENDATIONS,
    build_match_tailor_user_prompt,
)
from multilingual_normalizer import expand_synonyms, to_canonical
from skill_normalizer import normalize_skill

logger = logging.getLogger("match_tailor")

# Recommendation ceilings per score band — the backend may downgrade an
# over-optimistic recommendation but never upgrades one.
_RECOMMENDATION_RANK = {
    "DO_NOT_RECOMMEND": 0,
    "STRETCH_APPLY_LOW_ODDS": 1,
    "APPLY_WITH_HONEST_FRAMING": 2,
    "STRONG_APPLY": 3,
}
_RECOMMENDATION_BY_RANK = {rank: name for name, rank in _RECOMMENDATION_RANK.items()}

# Words that describe the shape of a job rather than its subject matter. Only the
# remaining tokens identify what the role is actually about ("Salesforce", "Apex").
_GENERIC_TITLE_WORDS = frozenset(
    {
        "a", "an", "and", "or", "the", "of", "for", "with", "to", "in", "at", "on",
        "senior", "junior", "mid", "midlevel", "lead", "principal", "staff", "head",
        "entry", "level", "intern", "internship", "student", "graduate", "trainee",
        "associate", "assistant", "deputy", "chief", "director", "vp", "manager",
        "management", "engineer", "engineering", "developer", "development", "dev",
        "programmer", "specialist", "analyst", "consultant", "coordinator",
        "administrator", "admin", "architect", "expert", "professional", "officer",
        "team", "position", "role", "job", "opening", "opportunity", "career",
        "fulltime", "full", "part", "time", "temporary", "permanent", "contract",
        "remote", "hybrid", "onsite", "office", "israel", "telaviv", "tlv",
        "i", "ii", "iii", "iv", "sr", "jr", "m", "f", "x",
        # Hebrew equivalents (job boards in this pipeline are Hebrew-first).
        "מפתח", "מפתחת", "מהנדס", "מהנדסת", "בכיר", "בכירה", "זוטר", "מנהל",
        "מנהלת", "אחראי", "אחראית", "עובד", "עובדת", "משרה", "דרוש", "דרושה",
        "מלאה", "חלקית", "צוות", "תפקיד", "מומחה", "מומחית", "רכז", "רכזת",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9#+.]+|[\u0590-\u05FF]+")

# Alias spellings the model invents → exact canonical category label.
_SKILL_CATEGORY_ALIASES: dict[str, str] = {
    "language": "Languages",
    "languages": "Languages",
    "programming languages": "Languages",
    "programming": "Languages",
    "backend": "Backend & Frameworks",
    "backend & frameworks": "Backend & Frameworks",
    "backend frameworks": "Backend & Frameworks",
    "frameworks": "Backend & Frameworks",
    "frameworks & libraries": "Backend & Frameworks",
    "libraries": "Backend & Frameworks",
    "frontend": "Frontend",
    "front end": "Frontend",
    "front-end": "Frontend",
    "ui": "Frontend",
    "mobile": "Mobile",
    "mobile development": "Mobile",
    "databases": "Databases",
    "database": "Databases",
    "databases & caching": "Databases",
    "data stores": "Databases",
    "cloud": "Cloud & DevOps",
    "devops": "Cloud & DevOps",
    "cloud & devops": "Cloud & DevOps",
    "cloud & devops tools": "Cloud & DevOps",
    "cloud & tools": "Cloud & DevOps",
    "cloud devops": "Cloud & DevOps",
    "cloud/devops": "Cloud & DevOps",
    "infrastructure": "Cloud & DevOps",
    "ai & data": "AI & Data",
    "ai": "AI & Data",
    "data": "AI & Data",
    "data & ai": "AI & Data",
    "machine learning": "AI & Data",
    "ml": "AI & Data",
    "tools": "Tools & Version Control",
    "tools & version control": "Tools & Version Control",
    "version control": "Tools & Version Control",
    "devtools": "Tools & Version Control",
    "soft skills": "Soft Skills",
    "soft": "Soft Skills",
    "other": "Other",
    "misc": "Other",
    "miscellaneous": "Other",
}

# Generic phrases that do not count as a real gap-analysis reason.
_GENERIC_GAP_REASONS = frozenset(
    {
        "missing",
        "not on resume",
        "not on the resume",
        "absent",
        "no evidence",
        "not found",
        "n/a",
        "none",
        "gap",
        "lacking",
        "does not have",
        "candidate lacks",
    }
)


class MatchTailorError(RuntimeError):
    """Raised when a match/tailor evaluation cannot be produced."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class MatchTailorSchemaError(MatchTailorError):
    """Raised when the model response does not match the required schema."""

    def __init__(self, message: str):
        super().__init__(message, status_code=502)


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #


def normalize_status(value: Any) -> str:
    """Coerce a candidate_status label to MATCH / PARTIAL / MISSING.

    Unknown or empty labels resolve to MISSING: an unreadable status must never
    quietly earn a candidate credit for a requirement.
    """
    text = re.sub(r"[^A-Z ]+", " ", str(value or "").upper()).strip()
    if not text:
        return "MISSING"
    if "MISSING" in text or "NO MATCH" in text or "NONE" in text:
        return "MISSING"
    if "PARTIAL" in text or "TRANSFERABLE" in text or "ADJACENT" in text:
        return "PARTIAL"
    if "MATCH" in text or "DIRECT" in text:
        return "MATCH"
    return "MISSING"


_BULLET_PREFIX_RE = re.compile(r"^\s*[-*•]\s+")


def _flatten_item(item: Any) -> str:
    """Coerce one list entry to display text, even when the model nests it.

    GPT-4o sometimes returns skills as ``{"category": "Databases", "skills":
    [...]}`` or as nested lists instead of the flat strings the schema asks for.
    Rendering ``str(dict)`` would put Python syntax on the resume, so grouped
    shapes are flattened into the ``"Category: a, b"`` row form the renderer
    already understands.
    """
    if isinstance(item, dict):
        label = next(
            (
                str(item[key]).strip()
                for key in ("category", "group", "name", "label", "title")
                if isinstance(item.get(key), str) and item.get(key, "").strip()
            ),
            "",
        )
        values: list[str] = []
        for key, value in item.items():
            if isinstance(value, str):
                if value.strip() and value.strip() != label:
                    values.append(value.strip())
            elif isinstance(value, (list, tuple)):
                values.extend(_flatten_item(entry) for entry in value)
        body = ", ".join(v for v in values if v)
        if label and body:
            return f"{label}: {body}"
        return body or label
    if isinstance(item, (list, tuple)):
        return ", ".join(text for text in (_flatten_item(entry) for entry in item) if text)
    return _BULLET_PREFIX_RE.sub("", str(item)).strip()


def _string_list(value: Any, *, max_items: int = 20) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _flatten_item(item)
        if text and text not in items:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _normalize_requirements(value: Any, *, max_items: int = 30) -> list[dict[str, str]]:
    """Coerce a requirement bucket into ``{requirement, candidate_status, evidence_or_gap}``."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for entry in value[:max_items]:
        if isinstance(entry, str):
            entry = {"requirement": entry}
        if not isinstance(entry, dict):
            continue
        requirement = str(
            entry.get("requirement") or entry.get("name") or ""
        ).strip()
        if not requirement:
            continue
        out.append(
            {
                "requirement": requirement,
                "candidate_status": normalize_status(entry.get("candidate_status")),
                "evidence_or_gap": str(entry.get("evidence_or_gap") or "").strip(),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Rubric scoring (Phase 2, re-computed server-side)
# --------------------------------------------------------------------------- #


def _bucket_score(
    requirements: list[dict[str, str]], weights: dict[str, float]
) -> float | None:
    if not requirements:
        return None
    total = sum(weights.get(r["candidate_status"], 0.0) for r in requirements)
    return total / len(requirements)


def compute_rubric_scores(
    hard_requirements: list[dict[str, str]],
    soft_requirements: list[dict[str, str]],
) -> dict[str, Any]:
    """Compute HARD_SCORE, SOFT_SCORE and the weighted composite from statuses."""
    hard_score = _bucket_score(hard_requirements, HARD_STATUS_WEIGHTS)
    soft_score = _bucket_score(soft_requirements, SOFT_STATUS_WEIGHTS)

    if hard_score is None and soft_score is None:
        composite = 0.0
    elif hard_score is None:
        # No hard requirements extracted: the soft bucket carries the whole score
        # rather than a 0.75 weight against an empty numerator.
        composite = float(soft_score or 0.0)
    else:
        effective_soft = 1.0 if soft_score is None else soft_score
        composite = (
            hard_score * HARD_REQUIREMENT_WEIGHT
            + effective_soft * SOFT_REQUIREMENT_WEIGHT
        )

    return {
        "hard_score_pct": None if hard_score is None else int(round(hard_score * 100)),
        "soft_score_pct": None if soft_score is None else int(round(soft_score * 100)),
        "composite_score": clamp_score(int(round(composite * 100))),
        "hard_requirement_count": len(hard_requirements),
        "soft_requirement_count": len(soft_requirements),
    }


def core_title_tokens(job_title: str) -> list[str]:
    """Subject-matter tokens of a job title (its core nouns/platforms).

    "Salesforce Developer (Apex, LWC)" -> ["salesforce", "apex", "lwc"].
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(str(job_title or "").lower()):
        token = raw.strip(".")
        if len(token) < 2 or token in _GENERIC_TITLE_WORDS or token.isdigit():
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def unmet_core_requirements(
    job_title: str, hard_requirements: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Core title subjects whose hard requirements are all MISSING.

    This is the backend safety net for the Hard Cap Rule described in the system
    prompt: if the job title's core noun ("Salesforce") only appears in hard
    requirements the candidate has zero evidence for, the role's primary function
    is unmet no matter how well the rest of the resume scores.
    """
    unmet: list[dict[str, Any]] = []
    for token in core_title_tokens(job_title):
        mentions = [
            r for r in hard_requirements if token in r["requirement"].lower()
        ]
        if not mentions:
            continue
        if all(r["candidate_status"] == "MISSING" for r in mentions):
            unmet.append(
                {
                    "core_token": token,
                    "requirements": [r["requirement"] for r in mentions],
                }
            )
    return unmet


def cap_for_unmet_core_count(count: int) -> int | None:
    if count >= 2:
        return MULTI_CORE_GAP_SCORE_CAP
    if count == 1:
        return CORE_GAP_SCORE_CAP
    return None


def align_recommendation(recommendation: Any, score: int) -> str:
    """Clamp a recommendation to the strongest option the score can justify."""
    text = str(recommendation or "").strip().upper().replace(" ", "_")
    rank = _RECOMMENDATION_RANK.get(text)

    if score >= 75:
        ceiling = _RECOMMENDATION_RANK["STRONG_APPLY"]
    elif score >= 55:
        ceiling = _RECOMMENDATION_RANK["APPLY_WITH_HONEST_FRAMING"]
    elif score >= 35:
        ceiling = _RECOMMENDATION_RANK["STRETCH_APPLY_LOW_ODDS"]
    else:
        ceiling = _RECOMMENDATION_RANK["DO_NOT_RECOMMEND"]

    if rank is None:
        return _RECOMMENDATION_BY_RANK[ceiling]
    return _RECOMMENDATION_BY_RANK[min(rank, ceiling)]


# --------------------------------------------------------------------------- #
# Anti-fabrication net
# --------------------------------------------------------------------------- #


def _skill_atoms(entry: str) -> list[str]:
    """Split a skills entry into checkable atoms ("Languages: Python, SQL")."""
    body = entry.split(":", 1)[1] if ":" in entry else entry
    atoms = [part.strip(" .;·|") for part in re.split(r"[,/|]|\band\b", body)]
    return [atom for atom in atoms if len(atom) >= 2]


# Words that carry no evidence on their own, so they must not decide whether a
# multi-word skill is supported ("Stakeholder communication" hinges on the two
# real words, not on "and").
_SKILL_STOPWORDS = frozenset(
    {"and", "or", "of", "the", "with", "in", "for", "a", "an", "to", "on", "using"}
)
_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "er", "ies", "ed", "es", "s")
# Shortest word stem allowed to match on a shared prefix alone.
_PREFIX_MATCH_MIN = 6


def _stem(word: str) -> str:
    """Crude suffix stripper — applied to both sides, so it only needs to be consistent."""
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _squash(text: str) -> str:
    """Drop everything but letters/digits so "Node.js" matches "NodeJS"."""
    return re.sub(r"[^a-z0-9\u0590-\u05FF]+", "", text.lower())


@dataclass(frozen=True)
class SourceEvidence:
    """Pre-indexed source resume text used to verify claimed skills."""

    text: str
    squashed: str
    stems: frozenset[str]

    @classmethod
    def build(cls, source_text: str) -> SourceEvidence:
        lowered = (source_text or "").lower()
        words = _TOKEN_RE.findall(lowered)
        return cls(
            text=lowered,
            squashed=_squash(lowered),
            stems=frozenset(_stem(w.strip(".")) for w in words if len(w) >= 2),
        )

    def __bool__(self) -> bool:
        return bool(self.text)

    def has_word(self, token: str) -> bool:
        """True when the source contains this word in any inflected form.

        Suffix stripping alone cannot bridge every pair ("communication" vs
        "communicated"), so a long shared prefix also counts as the same word.
        """
        if token in self.stems:
            return True
        if len(token) < _PREFIX_MATCH_MIN:
            return False
        return any(
            len(stem) >= _PREFIX_MATCH_MIN
            and (stem.startswith(token) or token.startswith(stem))
            for stem in self.stems
        )


def _alias_forms(skill: str) -> set[str]:
    forms = {skill.strip().lower()}
    canonical = to_canonical(skill) or normalize_skill(skill)
    if canonical:
        forms.add(canonical.lower())
        forms.update(v.lower() for v in expand_synonyms(canonical) if v)
    forms.update(v.lower() for v in expand_synonyms(skill) if v)
    return {re.sub(r"\s+", " ", f).strip() for f in forms if len(f.strip()) >= 2}


def _supported(skill: str, evidence: SourceEvidence) -> bool:
    if not skill or not evidence:
        return False

    forms = _alias_forms(skill)
    for form in forms:
        if form in evidence.text:
            return True
    # Punctuation/spacing variants: "CI/CD" vs "CICD", "Node.js" vs "Node JS".
    for form in forms:
        squashed = _squash(form)
        if len(squashed) >= 3 and squashed in evidence.squashed:
            return True

    # Word-level fallback: a multi-word skill counts as supported when every
    # meaningful word appears somewhere in the source, in any order or form
    # ("Stakeholder communication" <- "communicated with stakeholders").
    tokens = [
        _stem(token.strip("."))
        for token in _TOKEN_RE.findall(skill.lower())
        if len(token) >= 2 and token not in _SKILL_STOPWORDS
    ]
    tokens = [t for t in tokens if t]
    if not tokens:
        return False
    return all(evidence.has_word(token) for token in tokens)


def skill_supported_by_source(skill: str, source_text: str) -> bool:
    """True when a skill is evidenced in the source resume text.

    Matching is normalized rather than literal: canonical synonyms ("Postgres" /
    "PostgreSQL"), punctuation variants ("CI/CD" / "CICD") and word-order or
    inflection differences all count as support. Only a skill with no trace at
    all in the source is treated as unsupported.
    """
    return _supported(skill, SourceEvidence.build(source_text))


def find_unsupported_skills(skills: list[str], source_text: str) -> list[str]:
    """Skill atoms claimed in the tailored CV with no support in the source resume."""
    evidence = SourceEvidence.build(source_text)
    if not evidence:
        return []
    unsupported: list[str] = []
    for entry in skills:
        for atom in _skill_atoms(entry):
            if atom in unsupported:
                continue
            if not _supported(atom, evidence):
                unsupported.append(atom)
    return unsupported


def _strip_unsupported_skills(
    skills: list[str], source_text: str
) -> tuple[list[str], list[str]]:
    """Drop skills entries whose every atom is unsupported by the source resume."""
    evidence = SourceEvidence.build(source_text)
    if not evidence:
        return skills, []
    kept: list[str] = []
    dropped: list[str] = []
    for entry in skills:
        atoms = _skill_atoms(entry)
        if atoms and not any(_supported(a, evidence) for a in atoms):
            dropped.append(entry)
            continue
        kept.append(entry)
    return kept, dropped


# --------------------------------------------------------------------------- #
# Fixed skill taxonomy
# --------------------------------------------------------------------------- #


def _category_lookup_key(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 &/+-]+", " ", label.lower())).strip()


def canonicalize_skill_category(label: str) -> str:
    """Map a free-form category label onto the fixed taxonomy.

    Exact matches win first, then known aliases, then a light fuzzy contains
    check against canonical names. Truly unmappable labels fall back to Other.
    """
    raw = (label or "").strip().rstrip(":")
    if not raw:
        return "Other"
    if raw in CANONICAL_SKILL_CATEGORIES:
        return raw

    key = _category_lookup_key(raw)
    if key in _SKILL_CATEGORY_ALIASES:
        return _SKILL_CATEGORY_ALIASES[key]

    # Fuzzy: canonical name contained in the invented label, or vice versa.
    for canonical in CANONICAL_SKILL_CATEGORIES:
        canon_key = _category_lookup_key(canonical)
        if canon_key == key:
            return canonical
        if len(key) >= 4 and (key in canon_key or canon_key in key):
            return canonical
        # Token overlap for near-misses like "Cloud DevOps Tools" vs "Cloud & DevOps".
        key_tokens = {t for t in re.split(r"[\s&/+-]+", key) if len(t) >= 3}
        canon_tokens = {t for t in re.split(r"[\s&/+-]+", canon_key) if len(t) >= 3}
        if key_tokens and canon_tokens and key_tokens <= canon_tokens | {"tools"}:
            if key_tokens & canon_tokens:
                return canonical

    return "Other"


def normalize_skill_category_rows(skills: list[str]) -> tuple[list[str], list[str]]:
    """Rewrite grouped skill rows onto canonical category labels.

    Returns ``(normalized_rows, rewritten_labels)`` where ``rewritten_labels``
    lists original labels that were mapped away from their invented spelling.
    Plain (ungrouped) skill names are left untouched.
    """
    normalized: list[str] = []
    rewritten: list[str] = []
    for entry in skills:
        if ":" not in entry:
            normalized.append(entry)
            continue
        label, body = entry.split(":", 1)
        body = body.strip()
        if not body:
            normalized.append(entry)
            continue
        canonical = canonicalize_skill_category(label)
        original = label.strip().rstrip(":")
        if original != canonical:
            rewritten.append(original)
        normalized.append(f"{canonical}: {body}")
    return normalized, rewritten


# --------------------------------------------------------------------------- #
# Title / summary overclaim guard
# --------------------------------------------------------------------------- #


def _role_claim_patterns(job_title: str) -> list[re.Pattern[str]]:
    """Regexes that catch 'I am a <JD title>' style claims in title/summary text."""
    title = re.sub(r"\s+", " ", (job_title or "").strip())
    if len(title) < 3:
        return []
    escaped = re.escape(title)
    # Also try without parenthetical specializations: "DevOps Engineer (AWS)" -> "DevOps Engineer"
    bare = re.sub(r"\([^)]*\)", "", title).strip(" -–,/")
    patterns = [
        re.compile(rf"\b{escaped}\b", re.IGNORECASE),
    ]
    if bare and bare.lower() != title.lower() and len(bare) >= 3:
        patterns.append(re.compile(rf"\b{re.escape(bare)}\b", re.IGNORECASE))

    tokens = core_title_tokens(job_title)
    # Specialization nouns alone ("devops", "salesforce") as a role claim opener.
    for token in tokens:
        if len(token) < 4:
            continue
        patterns.append(
            re.compile(
                rf"\b{re.escape(token)}\s+(engineer|developer|specialist|architect|"
                rf"analyst|manager|consultant)\b",
                re.IGNORECASE,
            )
        )
    return patterns


def text_overclaims_job_title(text: str, job_title: str) -> bool:
    """True when ``text`` presents the candidate as already holding the JD role."""
    haystack = (text or "").strip()
    if not haystack or not (job_title or "").strip():
        return False
    return any(pattern.search(haystack) for pattern in _role_claim_patterns(job_title))


def build_honest_professional_title(
    job_title: str,
    hard_requirements: list[dict[str, str]],
) -> str:
    """Fallback title when the model overclaims a role the hard score does not support."""
    matched = [
        r["requirement"]
        for r in hard_requirements
        if r.get("candidate_status") == "MATCH"
    ]
    partial = [
        r["requirement"]
        for r in hard_requirements
        if r.get("candidate_status") == "PARTIAL"
    ]
    # Prefer a short matched skill noun for the honest framing.
    highlight = ""
    for candidate in matched + partial:
        words = [
            w for w in _TOKEN_RE.findall(candidate.lower())
            if len(w) >= 3 and w not in _GENERIC_TITLE_WORDS
        ]
        if words:
            highlight = " ".join(words[:3]).title()
            break

    core = core_title_tokens(job_title)
    pursuing = " ".join(t.title() for t in core[:2]) if core else (job_title or "the role").strip()

    if highlight:
        return f"Software Engineer with {highlight} Experience"
    if pursuing:
        return f"Software Engineer pursuing {pursuing}"
    return "Software Engineer"


def enforce_honest_title_summary(
    *,
    professional_title: str,
    summary: str,
    job_title: str,
    hard_score_pct: int | None,
    hard_requirements: list[dict[str, str]],
    threshold: int = HONEST_TITLE_HARD_SCORE_THRESHOLD,
) -> tuple[str, str, list[str]]:
    """Rewrite title/summary that overclaim the JD role when hard coverage is weak.

    Returns ``(title, summary, flags)`` — ``flags`` lists what was corrected.
    """
    flags: list[str] = []
    title = (professional_title or "").strip()
    summary_text = (summary or "").strip()
    score = 100 if hard_score_pct is None else int(hard_score_pct)

    if score >= threshold:
        return title, summary_text, flags

    honest = build_honest_professional_title(job_title, hard_requirements)
    if text_overclaims_job_title(title, job_title) or not title:
        if title != honest:
            flags.append("professional_title")
        title = honest

    if text_overclaims_job_title(summary_text, job_title):
        flags.append("summary")
        # Replace an overclaiming opening clause with the honest title framing.
        sentences = re.split(r"(?<=[.!?])\s+", summary_text)
        if sentences:
            first = sentences[0]
            if text_overclaims_job_title(first, job_title):
                rest = " ".join(sentences[1:]).strip()
                summary_text = (
                    f"{honest}. {rest}".strip()
                    if rest
                    else f"{honest}."
                )
            else:
                # Overclaim appears mid-summary — prepend honest framing and keep body.
                summary_text = f"{honest}. {summary_text}"
        else:
            summary_text = f"{honest}."

    return title, summary_text, flags


# --------------------------------------------------------------------------- #
# Gap-analysis list normalization
# --------------------------------------------------------------------------- #


def _is_generic_gap_reason(reason: str) -> bool:
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", (reason or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return True
    if cleaned in _GENERIC_GAP_REASONS:
        return True
    # Very short reasons without a profile-section cue are treated as generic.
    if len(cleaned) < 20 and not any(
        cue in cleaned
        for cue in ("experience", "project", "education", "bullet", "resume", "profile")
    ):
        return True
    return False


def normalize_missing_critical_skills(value: Any) -> list[str]:
    """Coerce missing_critical_skills to ``'skill — reason'`` display strings.

    Accepts plain strings (legacy) and ``{{skill, reason}}`` objects. Generic
    reasons are expanded with a profile-scoped default so downstream consumers
    always see a concrete gap statement.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    out: list[str] = []
    for entry in value[:20]:
        skill = ""
        reason = ""
        if isinstance(entry, dict):
            skill = str(
                entry.get("skill") or entry.get("name") or entry.get("requirement") or ""
            ).strip()
            reason = str(entry.get("reason") or entry.get("evidence_or_gap") or "").strip()
        else:
            text = _flatten_item(entry)
            if " — " in text:
                skill, reason = text.split(" — ", 1)
            elif " - " in text and len(text) > 40:
                skill, reason = text.split(" - ", 1)
            else:
                skill, reason = text, ""
            skill = skill.strip()
            reason = reason.strip()

        if not skill:
            continue
        if _is_generic_gap_reason(reason):
            reason = (
                "no supporting evidence found across Experience, Projects, "
                "or Education in the full candidate profile"
            )
        formatted = f"{skill} — {reason}"
        if formatted not in out:
            out.append(formatted)
    return out


def normalize_key_matching_points(value: Any) -> list[str]:
    """Keep key matching points as strings; drop empties."""
    return _string_list(value, max_items=20)


# --------------------------------------------------------------------------- #
# Schema validation / normalization
# --------------------------------------------------------------------------- #


def validate_schema_keys(payload: Any) -> None:
    """Raise MatchTailorSchemaError when required top-level keys are absent."""
    if not isinstance(payload, dict):
        raise MatchTailorSchemaError("Model response is not a JSON object")
    missing = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in payload]
    if missing:
        raise MatchTailorSchemaError(
            "Model response is missing required keys: " + ", ".join(missing)
        )
    extraction = payload.get("requirement_extraction")
    if not isinstance(extraction, dict):
        raise MatchTailorSchemaError("requirement_extraction must be an object")
    if not isinstance(payload.get("scoring"), dict):
        raise MatchTailorSchemaError("scoring must be an object")
    if not isinstance(payload.get("tailored_cv"), dict):
        raise MatchTailorSchemaError("tailored_cv must be an object")


def _normalize_tailored_cv(
    value: Any, *, source_text: str
) -> tuple[dict[str, Any], list[str], list[str]]:
    raw = value if isinstance(value, dict) else {}

    experience: list[dict[str, Any]] = []
    for entry in raw.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        experience.append(
            {
                "company": str(entry.get("company") or "").strip(),
                "title": str(entry.get("title") or "").strip(),
                "dates": str(entry.get("dates") or "").strip(),
                "bullets": _string_list(entry.get("bullets"), max_items=8),
            }
        )

    projects: list[dict[str, Any]] = []
    for entry in raw.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        projects.append(
            {
                "name": str(entry.get("name") or "").strip(),
                "description": str(entry.get("description") or "").strip(),
                "bullets": _string_list(entry.get("bullets"), max_items=8),
            }
        )

    education: list[dict[str, Any]] = []
    for entry in raw.get("education") or []:
        if not isinstance(entry, dict):
            continue
        education.append(
            {
                "institution": str(entry.get("institution") or "").strip(),
                "degree": str(entry.get("degree") or "").strip(),
                "dates": str(entry.get("dates") or "").strip(),
            }
        )

    skills, dropped = _strip_unsupported_skills(
        _string_list(raw.get("skills"), max_items=40), source_text
    )
    skills, rewritten_categories = normalize_skill_category_rows(skills)

    professional_title = str(
        raw.get("professional_title") or raw.get("title") or ""
    ).strip()

    return (
        {
            "professional_title": professional_title,
            "summary": str(raw.get("summary") or "").strip(),
            "skills": skills,
            "experience": experience,
            "projects": projects,
            "education": education,
        },
        dropped,
        rewritten_categories,
    )


def _normalize_transferable_framing(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for entry in value[:20]:
        if not isinstance(entry, dict):
            continue
        gap = str(entry.get("gap") or "").strip()
        framing = str(
            entry.get("how_to_honestly_frame_existing_experience")
            or entry.get("framing")
            or ""
        ).strip()
        if gap or framing:
            out.append(
                {
                    "gap": gap,
                    "how_to_honestly_frame_existing_experience": framing,
                }
            )
    return out


def normalize_match_tailor_result(
    raw: dict[str, Any],
    *,
    job_title: str = "",
    source_resume_text: str = "",
) -> dict[str, Any]:
    """Coerce a model response to the schema and enforce the scoring guardrails."""
    validate_schema_keys(raw)

    extraction = raw.get("requirement_extraction") or {}
    hard = _normalize_requirements(extraction.get("hard_requirements"))
    soft = _normalize_requirements(extraction.get("soft_requirements"))

    rubric = compute_rubric_scores(hard, soft)
    composite = rubric["composite_score"]

    scoring_in = raw.get("scoring") or {}
    model_score = clamp_score(scoring_in.get("realistic_match_score"))
    model_cap_flag = bool(scoring_in.get("hard_cap_applied"))

    unmet_core = unmet_core_requirements(job_title, hard)
    cap = cap_for_unmet_core_count(len(unmet_core))
    if cap is None and model_cap_flag:
        # The model saw a core gap the title tokens do not expose (e.g. "3+ years
        # of paid social" under a "Marketing Manager" title). Trust it downward.
        cap = CORE_GAP_SCORE_CAP

    final_score = composite if cap is None else min(composite, cap)
    final_score = clamp_score(final_score)

    rationale = str(scoring_in.get("score_rationale") or "").strip()
    if cap is not None and composite > cap:
        gap_names = ", ".join(
            item["requirements"][0] for item in unmet_core
        ) or "a core hard requirement"
        rationale = (
            f"{rationale} Hard Cap Rule applied (cap {cap}): {gap_names} "
            f"has no evidence in the resume."
        ).strip()

    recommendation = align_recommendation(raw.get("recommendation"), final_score)

    missing_critical = normalize_missing_critical_skills(
        raw.get("missing_critical_skills")
    )
    for item in unmet_core:
        for requirement in item["requirements"]:
            if not any(
                requirement.lower() in existing.lower()
                or existing.lower().split(" — ", 1)[0] in requirement.lower()
                for existing in missing_critical
            ):
                missing_critical.append(
                    f"{requirement} — no supporting evidence found across "
                    "Experience, Projects, or Education in the full candidate profile"
                )

    key_points = normalize_key_matching_points(raw.get("key_matching_points"))
    if not key_points:
        key_points = [
            r["requirement"]
            for r in hard + soft
            if r["candidate_status"] == "MATCH"
        ][:8]

    tailored_cv, dropped_skills, rewritten_categories = _normalize_tailored_cv(
        raw.get("tailored_cv"), source_text=source_resume_text
    )

    hard_score_pct = rubric["hard_score_pct"]
    title, summary, overclaim_flags = enforce_honest_title_summary(
        professional_title=tailored_cv.get("professional_title") or "",
        summary=tailored_cv.get("summary") or "",
        job_title=job_title,
        hard_score_pct=hard_score_pct,
        hard_requirements=hard,
    )
    tailored_cv["professional_title"] = title
    tailored_cv["summary"] = summary

    return {
        "requirement_extraction": {
            "hard_requirements": hard,
            "soft_requirements": soft,
        },
        "scoring": {
            "hard_score_pct": hard_score_pct or 0,
            "soft_score_pct": rubric["soft_score_pct"] or 0,
            "hard_cap_applied": cap is not None,
            "realistic_match_score": final_score,
            "score_rationale": rationale,
        },
        "key_matching_points": key_points,
        "missing_critical_skills": missing_critical,
        "transferable_skills_framing": _normalize_transferable_framing(
            raw.get("transferable_skills_framing")
        ),
        "tailored_cv": tailored_cv,
        "recommendation": recommendation,
        "score_validation": {
            "model_reported_score": model_score,
            "recomputed_composite_score": composite,
            "score_overridden": model_score != final_score,
            "cap": cap,
            "unmet_core_requirements": unmet_core,
            "model_hard_cap_flag": model_cap_flag,
            "hard_requirement_count": rubric["hard_requirement_count"],
            "soft_requirement_count": rubric["soft_requirement_count"],
            "dropped_unsupported_skills": dropped_skills,
            "rewritten_skill_categories": rewritten_categories,
            "overclaim_corrections": overclaim_flags,
        },
    }


def experience_bullet_fingerprint(tailored_cv: dict[str, Any]) -> list[tuple[str, int, str]]:
    """Compact signature of Experience bullet order and depth for differentiation checks.

    Each tuple is ``(role_title, bullet_length, first_40_chars_lower)`` so two
    tailored resumes can be compared for substantive (not cosmetic) differences.
    """
    fingerprint: list[tuple[str, int, str]] = []
    for entry in tailored_cv.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("title") or entry.get("company") or "").strip().lower()
        for bullet in entry.get("bullets") or []:
            text = str(bullet or "").strip()
            if not text:
                continue
            fingerprint.append((role, len(text), text[:40].lower()))
    return fingerprint


def bullets_differ_substantively(
    cv_a: dict[str, Any], cv_b: dict[str, Any], *, min_order_or_depth_deltas: int = 2
) -> bool:
    """True when two tailored CVs differ in bullet order and/or depth, not just wording.

    Counts positions where the same role has a different first-bullet prefix or a
    length delta of at least 40 characters — the bar for "substantive" per-JD shift.
    """
    fp_a = experience_bullet_fingerprint(cv_a)
    fp_b = experience_bullet_fingerprint(cv_b)
    if not fp_a or not fp_b:
        return fp_a != fp_b

    deltas = 0
    # Compare by role: first-bullet identity and per-position length.
    roles_a: dict[str, list[tuple[int, str]]] = {}
    roles_b: dict[str, list[tuple[int, str]]] = {}
    for role, length, prefix in fp_a:
        roles_a.setdefault(role, []).append((length, prefix))
    for role, length, prefix in fp_b:
        roles_b.setdefault(role, []).append((length, prefix))

    for role in set(roles_a) | set(roles_b):
        bullets_a = roles_a.get(role) or []
        bullets_b = roles_b.get(role) or []
        if not bullets_a or not bullets_b:
            deltas += 1
            continue
        if bullets_a[0][1] != bullets_b[0][1]:
            deltas += 1  # different lead bullet
        for (len_a, _), (len_b, _) in zip(bullets_a, bullets_b):
            if abs(len_a - len_b) >= 40:
                deltas += 1
        # Extra / missing bullets also count as ordering/depth shifts.
        deltas += abs(len(bullets_a) - len(bullets_b))

    return deltas >= min_order_or_depth_deltas


# --------------------------------------------------------------------------- #
# Prompt payloads
# --------------------------------------------------------------------------- #


def build_candidate_payload(
    cv_profile: dict[str, Any], source_documents: str | None = None
) -> str:
    """Build the parsed-resume payload for `candidate_parsed_resume_json_or_text`.

    ``source_documents`` carries the candidate's original uploaded CV text. It is
    included so the completeness rules can mine skills that only ever appear in an
    experience bullet, and so honest tailoring works from full history rather than
    from whatever survived parsing.
    """
    parts: list[str] = []
    raw = str(cv_profile.get("raw_text") or "").strip()
    if raw:
        parts.append("=== RAW RESUME TEXT ===")
        parts.append(truncate_text(raw, OPENAI_CV_MAX_CHARS))

    for key in (
        "contact",
        "experience",
        "education",
        "skills",
        "projects",
        "certifications",
        "sections",
        "master_profile",
    ):
        value = cv_profile.get(key)
        if value:
            parts.append(f"=== {key.upper()} ===")
            parts.append(json.dumps(value, ensure_ascii=False, indent=2)[:8000])

    extra = (source_documents or "").strip()
    if extra:
        parts.append("=== ORIGINAL SOURCE DOCUMENTS ===")
        parts.append(truncate_text(extra, OPENAI_CV_MAX_CHARS))

    return "\n\n".join(parts).strip()


def build_job_payload(job: dict[str, Any], job_profile: JobProfile | None = None) -> str:
    """Build the `job_description_raw_text` payload, plus structured JD facts."""
    description = job.get("full_description") or job.get("description") or ""
    parts = [truncate_text(str(description), OPENAI_JOB_MAX_CHARS)]
    location = str(job.get("location") or "").strip()
    if location:
        parts.append(f"Location: {location}")
    if job_profile is not None:
        parts.append("Structured JobProfile JSON (extracted earlier by the pipeline):")
        parts.append(json.dumps(job_profile.to_dict(), ensure_ascii=False, indent=2))
    return "\n\n".join(p for p in parts if p).strip()


def source_resume_text(
    cv_profile: dict[str, Any], source_documents: str | None = None
) -> str:
    """Flatten every source of truth into one blob for the anti-fabrication check."""
    profile_blob = json.dumps(cv_profile, ensure_ascii=False)
    return f"{profile_blob}\n{source_documents or ''}".lower()


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _call_model(
    system_prompt: str,
    user_prompt: str,
    *,
    use_cache: bool,
    cache_namespace: str,
    cache_payload: str,
) -> dict[str, Any]:
    raw = call_openai_json(
        system_prompt,
        user_prompt,
        temperature=MATCH_TAILOR_TEMPERATURE,
        model=OPENAI_TAILOR_MODEL,
        use_cache=use_cache,
        cache_namespace=cache_namespace,
        cache_payload=cache_payload,
    )
    validate_schema_keys(raw)
    return raw


def evaluate_candidate_for_job(
    *,
    cv_profile: dict[str, Any],
    job: dict[str, Any],
    use_cache: bool = True,
    source_documents: str | None = None,
) -> dict[str, Any]:
    """Run the three-phase match + tailor evaluation for one candidate/job pair.

    Returns the full schema from ``match_tailor_prompt`` plus a
    ``score_validation`` block describing every server-side adjustment.
    """
    if not is_ai_available():
        raise MatchTailorError(
            "OPENAI_API_KEY is not configured — cannot evaluate this job",
            status_code=503,
        )

    job_title = str(job.get("title") or "")
    company = str(job.get("company") or "")
    job_profile = parse_stored_job_profile(job.get("job_profile"))

    candidate_payload = build_candidate_payload(cv_profile, source_documents)
    job_payload = build_job_payload(job, job_profile)
    user_prompt = build_match_tailor_user_prompt(
        candidate_resume=candidate_payload,
        job_title=job_title,
        company_name=company,
        job_description=job_payload,
    )
    cache_namespace = f"match_tailor_{MATCH_TAILOR_PROMPT_VERSION}"
    cache_payload = (
        f"{MATCH_TAILOR_PROMPT_VERSION}|{OPENAI_TAILOR_MODEL}|{job.get('id')}|"
        f"{job_title}|{job_payload[:2000]}|{candidate_payload[:4000]}"
    )

    try:
        raw = _call_model(
            MATCH_TAILOR_SYSTEM_PROMPT,
            user_prompt,
            use_cache=use_cache,
            cache_namespace=cache_namespace,
            cache_payload=cache_payload,
        )
    except (OpenAIAPIError, MatchTailorSchemaError) as first_error:
        # One retry with an explicit schema reminder, per the integration contract.
        logger.warning(
            "match_tailor: invalid response for job %s (%s) — retrying once",
            job.get("id"),
            first_error,
        )
        try:
            raw = _call_model(
                f"{MATCH_TAILOR_SYSTEM_PROMPT}\n\n{JSON_RETRY_NOTE}",
                user_prompt,
                use_cache=False,
                cache_namespace=cache_namespace,
                cache_payload=f"retry|{cache_payload}",
            )
        except (OpenAIAPIError, MatchTailorSchemaError) as retry_error:
            raise MatchTailorError(
                f"Match evaluation failed: {retry_error}", status_code=502
            ) from retry_error

    result = normalize_match_tailor_result(
        raw,
        job_title=job_title,
        source_resume_text=source_resume_text(cv_profile, source_documents),
    )
    result["from_cache"] = bool(raw.get("_from_cache"))
    _log_evaluation(job, result)
    return result


def _log_evaluation(job: dict[str, Any], result: dict[str, Any]) -> None:
    """Log the extraction + rationale — the audit trail for "why only 45%?"."""
    scoring = result["scoring"]
    validation = result["score_validation"]
    logger.info(
        "match_tailor job=%s title=%r score=%s (hard=%s soft=%s cap=%s "
        "model_said=%s overridden=%s) recommendation=%s rationale=%r",
        job.get("id"),
        job.get("title"),
        scoring["realistic_match_score"],
        scoring["hard_score_pct"],
        scoring["soft_score_pct"],
        validation["cap"],
        validation["model_reported_score"],
        validation["score_overridden"],
        result["recommendation"],
        scoring["score_rationale"],
    )
    logger.info(
        "match_tailor job=%s requirement_extraction=%s",
        job.get("id"),
        json.dumps(result["requirement_extraction"], ensure_ascii=False),
    )
    if validation["dropped_unsupported_skills"]:
        logger.warning(
            "match_tailor job=%s dropped unsupported skills from tailored CV: %s",
            job.get("id"),
            ", ".join(validation["dropped_unsupported_skills"]),
        )
    if validation.get("rewritten_skill_categories"):
        logger.info(
            "match_tailor job=%s normalized skill categories: %s",
            job.get("id"),
            ", ".join(validation["rewritten_skill_categories"]),
        )
    if validation.get("overclaim_corrections"):
        logger.warning(
            "match_tailor job=%s corrected overclaiming title/summary fields: %s",
            job.get("id"),
            ", ".join(validation["overclaim_corrections"]),
        )


__all__ = [
    "MatchTailorError",
    "MatchTailorSchemaError",
    "VALID_RECOMMENDATIONS",
    "align_recommendation",
    "build_candidate_payload",
    "build_honest_professional_title",
    "build_job_payload",
    "canonicalize_skill_category",
    "cap_for_unmet_core_count",
    "compute_rubric_scores",
    "core_title_tokens",
    "enforce_honest_title_summary",
    "evaluate_candidate_for_job",
    "experience_bullet_fingerprint",
    "bullets_differ_substantively",
    "find_unsupported_skills",
    "normalize_key_matching_points",
    "normalize_match_tailor_result",
    "normalize_missing_critical_skills",
    "normalize_skill_category_rows",
    "normalize_status",
    "skill_supported_by_source",
    "source_resume_text",
    "text_overclaims_job_title",
    "unmet_core_requirements",
    "validate_schema_keys",
]
