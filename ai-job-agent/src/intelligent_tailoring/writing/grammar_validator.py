"""GrammarValidator — reject broken, duplicated, or stuffed resume prose."""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.linguistic_integrity import (
    detect_broken_patterns,
    has_duplicate_sentence,
    has_repeated_ngram,
    validate_claim_linguistics,
)
from intelligent_tailoring.writing.ai_phrases import (
    AI_CLICHE_PHRASES,
    UNNATURAL_TRANSITIONS,
    WEAK_BULLET_OPENINGS,
)

_WORD_RE = re.compile(r"[A-Za-z\u0590-\u05FF0-9+#./-]{2,}")


def _iter_claims(resume: dict[str, Any]) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    if summary:
        claims.append(("summary", summary))
    for idx, entry in enumerate(resume.get("experience") or []):
        if not isinstance(entry, dict):
            continue
        for b_idx, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet).strip()
            if text:
                claims.append((f"experience_{idx}_b{b_idx}", text))
    for idx, entry in enumerate(resume.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        desc = str(entry.get("description") or "").strip()
        if desc:
            claims.append((f"projects_{idx}_desc", desc))
        for b_idx, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet).strip()
            if text:
                claims.append((f"projects_{idx}_b{b_idx}", text))
    return claims


def _opening_token(text: str) -> str:
    words = _WORD_RE.findall(text or "")
    return words[0].lower() if words else ""


def _keyword_stuffing_score(text: str) -> float:
    """Heuristic: many commas / slash clusters with few verbs → stuffing."""
    sample = text or ""
    words = _WORD_RE.findall(sample)
    if len(words) < 8:
        return 0.0
    commas = sample.count(",")
    slashes = sample.count("/")
    verbs = sum(
        1
        for w in words
        if w.lower().endswith(("ed", "ing"))
        or w.lower()
        in {
            "built",
            "led",
            "ran",
            "wrote",
            "made",
            "kept",
            "is",
            "are",
            "was",
            "were",
            "has",
            "have",
        }
    )
    density = (commas + slashes) / max(len(words), 1)
    if density > 0.35 and verbs <= 1:
        return min(1.0, density)
    if commas >= 8 and verbs <= 2:
        return 0.8
    return 0.0


def validate_grammar(resume: dict[str, Any]) -> dict[str, Any]:
    """Validate grammar / linguistic cleanliness of a tailored resume."""
    issues: list[dict[str, Any]] = []
    openings: list[str] = []
    affected_sections: set[str] = set()

    for claim_id, text in _iter_claims(resume):
        section = claim_id.split("_", 1)[0]
        ling = validate_claim_linguistics(
            text, allow_summary_style=claim_id == "summary"
        )
        patterns = list(ling.get("detected_patterns") or [])
        soft = list(ling.get("soft_patterns") or [])

        if has_duplicate_sentence(text) and "duplicate_sentence" not in patterns:
            patterns.append("duplicate_sentence")
        if has_repeated_ngram(text) and "repeated_ngram" not in patterns:
            patterns.append("repeated_ngram")

        broken = detect_broken_patterns(text)
        for code in broken:
            if code not in patterns:
                patterns.append(code)

        low = text.lower()
        for phrase in AI_CLICHE_PHRASES:
            if phrase in low:
                patterns.append(f"ai_cliche:{phrase}")
                break
        for phrase in UNNATURAL_TRANSITIONS:
            if phrase in low:
                patterns.append(f"unnatural_transition:{phrase.rstrip(',')}")
                break
        for phrase in WEAK_BULLET_OPENINGS:
            if low.startswith(phrase):
                patterns.append(f"weak_opening:{phrase}")
                break

        stuffing = _keyword_stuffing_score(text)
        if stuffing >= 0.5:
            patterns.append("keyword_stuffing")

        # Missing terminal punctuation on long summary sentences is soft only
        if claim_id == "summary":
            sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
            if len(sentences) > 4:
                patterns.append("summary_too_many_sentences")
            words = text.split()
            if len(words) < 40:
                soft.append("summary_short")
            if len(words) > 90:
                patterns.append("summary_too_long")

        if claim_id != "summary":
            openings.append(_opening_token(text))

        hard = [
            p
            for p in patterns
            if not p.startswith("weak_opening")
            and p
            not in {
                "summary_short",
            }
        ]
        if hard:
            issues.append(
                {
                    "claim_id": claim_id,
                    "section": section,
                    "patterns": hard,
                    "text": text[:180],
                }
            )
            affected_sections.add("summary" if section == "summary" else section)

    # Repeated openings across bullets (e.g. five "Implemented")
    if openings:
        from collections import Counter

        counts = Counter(o for o in openings if o)
        for opening, count in counts.items():
            if count >= 3 and opening:
                issues.append(
                    {
                        "claim_id": "bullets",
                        "section": "experience",
                        "patterns": [f"repeated_opening:{opening}"],
                        "text": opening,
                    }
                )
                affected_sections.add("experience")

    score = max(0, 100 - 12 * len(issues))
    return {
        "passed": len(issues) == 0,
        "score": score,
        "issues": issues,
        "affected_sections": sorted(affected_sections),
        "regeneration_required": len(issues) > 0,
        "validator": "GrammarValidator",
    }


class GrammarValidator:
    """Object-style wrapper matching the requested service name."""

    def validate(self, resume: dict[str, Any]) -> dict[str, Any]:
        return validate_grammar(resume)
