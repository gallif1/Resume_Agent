"""Linguistic integrity gate — reject corrupted claim text before export.

Detects empty grammatical slots left by token-level deletion, duplicated
fragments, and other unusable resume prose. Profession-agnostic.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Broken patterns produced by deleting entities from sentences.
_BROKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("using_and", re.compile(r"\busing\s+and\b", re.I)),
    ("using_comma_including", re.compile(r"\busing\s*,\s*including\b", re.I)),
    ("using_period", re.compile(r"\busing\s*\.", re.I)),
    ("using_and_period", re.compile(r"\busing\s+and\s*\.", re.I)),
    ("with_and", re.compile(r"\bwith\s+and\b", re.I)),
    ("with_period", re.compile(r"\bwith\s*\.", re.I)),
    ("to_period", re.compile(r"\bto\s*\.", re.I)),
    ("on_period", re.compile(r"\bon\s*\.", re.I)),
    ("via_period", re.compile(r"\bvia\s*\.", re.I)),
    ("empty_parens", re.compile(r"\(\s*\)")),
    ("using_empty_parens", re.compile(r"\busing\s*\([^)]*\)", re.I)),
    ("dangling_comma", re.compile(r",\s*(,|\.|$)")),
    ("dangling_and", re.compile(r"\band\s*[,.]", re.I)),
    ("double_space", re.compile(r"  +")),
    ("space_before_punct", re.compile(r"\s+[,.;:]")),
    ("unmatched_open_paren", re.compile(r"\([^)]*$")),
    ("unmatched_close_paren", re.compile(r"^[^(]*\)")),
    ("placeholder", re.compile(r"\b(TODO|TBD|PLACEHOLDER|XXX|\[insert\])\b", re.I)),
    ("knowledge_experience", re.compile(r"\bknowledge\s+\w+\s+experience\b", re.I)),
    ("experience_experience", re.compile(r"\bexperience\s+experience\b", re.I)),
    ("professional_with_experience", re.compile(
        r"\bprofessional with (?:experience|knowledge)\b", re.I
    )),
    ("candidate_for_roles", re.compile(r"\bcandidate for\b", re.I)),
    ("strong_understanding_experience", re.compile(
        r"\bstrong understanding\s+experience\b", re.I
    )),
]


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def detect_broken_patterns(text: str) -> list[str]:
    """Return codes for linguistic corruption patterns found in text."""
    found: list[str] = []
    sample = text or ""
    for code, pattern in _BROKEN_PATTERNS:
        if pattern.search(sample):
            found.append(code)
    # Generic: preposition/conjunction immediately before closing punct
    if re.search(r"\b(using|with|via|to|on|for|and|or)\s*[.,;:]", sample, re.I):
        if "dangling_preposition" not in found:
            found.append("dangling_preposition")
    # Empty list slot: "A, , B" or "A, and ."
    if re.search(r",\s*,|,\s+and\s*[.,]", sample):
        if "empty_list_slot" not in found:
            found.append("empty_list_slot")
    return found


def has_duplicate_sentence(text: str) -> bool:
    parts = [p.strip() for p in re.split(r"[.!?]+", text or "") if p.strip()]
    if len(parts) < 2:
        return False
    normalized = [re.sub(r"\s+", " ", p.lower()) for p in parts]
    return len(normalized) != len(set(normalized))


def has_repeated_ngram(text: str, n: int = 4) -> bool:
    """Detect immediate repeated n-grams (duplicated phrase fragments)."""
    tokens = re.findall(r"[A-Za-z\u0590-\u05FF0-9+#.]{2,}", text or "")
    if len(tokens) < n * 2:
        return False
    for i in range(len(tokens) - n):
        window = tokens[i : i + n]
        nxt = tokens[i + n : i + 2 * n]
        if window == nxt:
            return True
    return False


def is_fragment(text: str) -> bool:
    """Heuristic: too short or missing a verb-like token for a bullet/summary."""
    sample = (text or "").strip()
    if not sample:
        return True
    words = re.findall(r"[A-Za-z\u0590-\u05FF]{2,}", sample)
    if len(words) < 3:
        return True
    # Resume bullets usually start with a past-tense / gerund verb
    first = words[0].lower()
    verbish = (
        first.endswith("ed")
        or first.endswith("ing")
        or first in {
            "built", "built", "led", "ran", "wrote", "made", "kept",
            "managed", "handled", "created", "designed", "developed",
            "implemented", "deployed", "supported", "provided", "trained",
            "coordinated", "prepared", "resolved", "operated", "scheduled",
        }
    )
    # Summaries are sentences; fragments often lack verbs entirely
    if len(words) <= 6 and not verbish and not re.search(
        r"\b(is|are|was|were|has|have|with|experience)\b", sample, re.I
    ):
        return True
    return False


# Patterns that must block export (structural corruption from token deletion).
_HARD_CORRUPTION = frozenset(
    {
        "using_and",
        "using_comma_including",
        "using_period",
        "using_and_period",
        "with_and",
        "with_period",
        "to_period",
        "on_period",
        "via_period",
        "empty_parens",
        "using_empty_parens",
        "dangling_comma",
        "dangling_and",
        "dangling_preposition",
        "empty_list_slot",
        "placeholder",
        "knowledge_experience",
        "experience_experience",
        "strong_understanding_experience",
        "repeated_ngram",
        "duplicate_sentence",
        "title_case_soup",
    }
)


def validate_claim_linguistics(text: str, *, allow_summary_style: bool = False) -> dict[str, Any]:
    """Validate a single claim/sentence for linguistic integrity."""
    patterns = detect_broken_patterns(text)
    if has_duplicate_sentence(text):
        patterns.append("duplicate_sentence")
    if has_repeated_ngram(text):
        patterns.append("repeated_ngram")
    # Only treat as fragment when structural corruption is also present —
    # short résumé bullets like "Mentored struggling learners" are valid.
    if not allow_summary_style and is_fragment(text) and patterns:
        patterns.append("fragment")
    hard = [p for p in patterns if p in _HARD_CORRUPTION or p.startswith("using_")]
    passed = len(hard) == 0
    return {
        "passed": passed,
        "detected_patterns": hard,
        "soft_patterns": [p for p in patterns if p not in hard],
        "text_hash": text_hash(text),
    }


def validate_resume_linguistics(resume: dict[str, Any]) -> dict[str, Any]:
    """Scan the full tailored resume for linguistic corruption."""
    invalid: list[dict[str, Any]] = []
    detected: list[str] = []

    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    if summary:
        result = validate_claim_linguistics(summary, allow_summary_style=True)
        patterns = list(result["detected_patterns"])
        if has_duplicate_sentence(summary):
            patterns.append("duplicate_sentence")
        if has_repeated_ngram(summary, n=3):
            patterns.append("repeated_ngram")
        words = summary.split()
        # Title-case soup: many consecutive Capitalized tokens (English only)
        if re.search(r"[A-Za-z]", summary):
            caps = re.findall(r"\b[A-Z][a-zA-Z0-9+#.]+\b", summary)
            if len(caps) >= 8 and len(caps) / max(len(words), 1) > 0.55:
                patterns.append("title_case_soup")
        hard = [p for p in patterns if p in _HARD_CORRUPTION]
        if hard:
            invalid.append(
                {
                    "claim_id": "summary",
                    "section": "summary",
                    "patterns": list(dict.fromkeys(hard)),
                    "text_hash": text_hash(summary),
                }
            )
            detected.extend(hard)

    for section in ("experience", "projects"):
        for idx, entry in enumerate(resume.get(section) or []):
            if not isinstance(entry, dict):
                continue
            for b_idx, bullet in enumerate(entry.get("bullets") or []):
                text = str(bullet).strip()
                if not text:
                    continue
                result = validate_claim_linguistics(text)
                if not result["passed"]:
                    invalid.append(
                        {
                            "claim_id": f"{section}_{idx}_b{b_idx}",
                            "section": section,
                            "patterns": result["detected_patterns"],
                            "text_hash": result["text_hash"],
                        }
                    )
                    detected.extend(result["detected_patterns"])
            if section == "projects":
                desc = str(entry.get("description") or "").strip()
                if desc:
                    result = validate_claim_linguistics(desc)
                    if not result["passed"]:
                        invalid.append(
                            {
                                "claim_id": f"projects_{idx}_desc",
                                "section": "projects",
                                "patterns": result["detected_patterns"],
                                "text_hash": result["text_hash"],
                            }
                        )
                        detected.extend(result["detected_patterns"])

    unique_patterns = list(dict.fromkeys(detected))
    score = max(0, 100 - 15 * len(invalid))
    return {
        "linguistic_integrity_score": score,
        "invalid_claim_ids": [i["claim_id"] for i in invalid],
        "invalid_claims": invalid,
        "detected_patterns": unique_patterns,
        "regeneration_required": len(invalid) > 0,
        "passed": len(invalid) == 0,
    }
