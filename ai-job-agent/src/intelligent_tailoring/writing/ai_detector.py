"""Heuristic AI-writing detector for resume prose."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from intelligent_tailoring.writing.ai_phrases import (
    AI_CLICHE_PHRASES,
    UNNATURAL_TRANSITIONS,
    WEAK_BULLET_OPENINGS,
)
from intelligent_tailoring.writing.style_validator import evaluate_writing_quality

_WORD_RE = re.compile(r"[A-Za-z\u0590-\u05FF0-9+#./-]{2,}")

_ROBOTIC_VERBS = (
    "utilized",
    "leveraged",
    "spearheaded",
    "orchestrated",
    "facilitated",
    "demonstrated ability",
    "proven ability",
)


def _all_texts(resume: dict[str, Any]) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    if summary:
        texts.append(("summary", summary))
    for section in ("experience", "projects"):
        for entry in resume.get(section) or []:
            if not isinstance(entry, dict):
                continue
            if section == "projects":
                desc = str(entry.get("description") or "").strip()
                if desc:
                    texts.append((section, desc))
            for bullet in entry.get("bullets") or []:
                text = str(bullet).strip()
                if text:
                    texts.append((section, text))
    return texts


def detect_ai_writing(resume: dict[str, Any]) -> dict[str, Any]:
    """Return AI-likeness signals. ``regeneration_required`` when too AI-like."""
    texts = _all_texts(resume)
    signals: list[str] = []
    affected: set[str] = set()
    blob = " ".join(t for _, t in texts).lower()

    cliches = [p for p in AI_CLICHE_PHRASES if p in blob]
    if cliches:
        signals.append(f"overused_ai_phrases:{len(cliches)}")
        affected.add("summary")
        affected.add("experience")

    transitions = [p for p in UNNATURAL_TRANSITIONS if p in blob]
    if transitions:
        signals.append(f"unnatural_transitions:{len(transitions)}")
        affected.add("summary")

    openings = []
    for section, text in texts:
        if section == "summary":
            continue
        words = _WORD_RE.findall(text)
        if words:
            openings.append(words[0].lower())
        low = text.lower()
        if any(low.startswith(w) for w in WEAK_BULLET_OPENINGS):
            signals.append("weak_bullet_opening")
            affected.add(section)
        if any(v in low[:40] for v in _ROBOTIC_VERBS):
            signals.append("robotic_phrasing")
            affected.add(section)

    counts = Counter(openings)
    for opening, count in counts.items():
        if count >= 3:
            signals.append(f"repeated_sentence_openings:{opening}")
            affected.add("experience")

    # Generic summary: mostly adjectives / buzzwords, little concrete content
    summary = next((t for s, t in texts if s == "summary"), "")
    if summary:
        words = _WORD_RE.findall(summary)
        buzz = sum(1 for p in AI_CLICHE_PHRASES if p in summary.lower())
        if buzz >= 2 or (
            len(words) >= 20
            and not re.search(
                r"\b(built|developed|led|managed|designed|taught|sold|supported|"
                r"treated|operated|coordinated|analyzed)\b",
                summary,
                re.I,
            )
        ):
            # Only flag generic if also buzzwordy or purely adjective-heavy
            if buzz >= 1 or re.search(
                r"\b(passionate|motivated|results-driven|dedicated|dynamic)\b",
                summary,
                re.I,
            ):
                signals.append("generic_summary")
                affected.add("summary")

    # Keyword stuffing: skills echoed as comma soup in summary
    if summary.count(",") >= 6 and len(summary.split()) < 55:
        signals.append("keyword_stuffing")
        affected.add("summary")

    # Buzzword overload
    buzz_count = len(cliches) + sum(1 for v in _ROBOTIC_VERBS if v in blob)
    if buzz_count >= 4:
        signals.append("buzzword_overload")

    quality = evaluate_writing_quality(resume)
    ai_score = int(quality["dimensions"].get("ai_likeness") or 0)
    # Detector "ai_risk" is inverse of human-likeness
    ai_risk = max(0, 100 - ai_score)
    if signals:
        ai_risk = min(100, ai_risk + 8 * len(set(signals)))

    regeneration = ai_risk >= 35 or any(
        s.startswith(
            (
                "overused_ai_phrases",
                "generic_summary",
                "buzzword_overload",
                "repeated_sentence_openings",
                "keyword_stuffing",
            )
        )
        for s in signals
    )

    return {
        "passed": not regeneration,
        "ai_risk": ai_risk,
        "human_score": ai_score,
        "signals": list(dict.fromkeys(signals)),
        "affected_sections": sorted(affected),
        "regeneration_required": regeneration,
        "detector": "AIWritingDetector",
    }


class AIWritingDetector:
    def detect(self, resume: dict[str, Any]) -> dict[str, Any]:
        return detect_ai_writing(resume)
