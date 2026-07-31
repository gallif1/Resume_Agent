"""WritingQualityValidator / StyleValidator — multi-dimension writing scores."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from intelligent_tailoring.linguistic_integrity import (
    has_duplicate_sentence,
    has_repeated_ngram,
    validate_resume_linguistics,
)
from intelligent_tailoring.writing.ai_phrases import (
    AI_CLICHE_PHRASES,
    UNNATURAL_TRANSITIONS,
    WEAK_BULLET_OPENINGS,
)
from intelligent_tailoring.writing.grammar_validator import validate_grammar

DEFAULT_THRESHOLD = 70

_WORD_RE = re.compile(r"[A-Za-z\u0590-\u05FF0-9+#./-]{2,}")
_ACTION_VERBS = frozenset(
    {
        "built",
        "developed",
        "designed",
        "implemented",
        "led",
        "managed",
        "created",
        "delivered",
        "improved",
        "reduced",
        "increased",
        "launched",
        "owned",
        "drove",
        "coordinated",
        "analyzed",
        "resolved",
        "supported",
        "trained",
        "automated",
        "configured",
        "deployed",
        "integrated",
        "optimized",
        "streamlined",
        "established",
        "negotiated",
        "closed",
        "advised",
        "taught",
        "facilitated",
        "diagnosed",
        "treated",
        "processed",
        "scheduled",
        "operated",
        "maintained",
        "documented",
        "migrated",
        "refactored",
        "monitored",
        "secured",
        "tested",
        "validated",
        "presented",
        "partnered",
        "collaborated",
        "mentored",
        "hired",
        "planned",
        "executed",
        "produced",
        "wrote",
        "authored",
        "researched",
        "evaluated",
        "audited",
        "reconciled",
        "forecasted",
        "budgeted",
        "served",
        "assisted",
        "guided",
        "coached",
        "organized",
        "supervised",
        "directed",
        "oversaw",
        "prepared",
        "compiled",
        "tracked",
        "reported",
        "communicated",
        "translated",
        "customized",
        "scaled",
        "shipped",
        "prototyped",
        "architected",
    }
)


def _claims(resume: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    if summary:
        out.append(("summary", summary))
    for section in ("experience", "projects"):
        for entry in resume.get(section) or []:
            if not isinstance(entry, dict):
                continue
            if section == "projects":
                desc = str(entry.get("description") or "").strip()
                if desc:
                    out.append((section, desc))
            for bullet in entry.get("bullets") or []:
                text = str(bullet).strip()
                if text:
                    out.append((section, text))
    return out


def _clamp(score: float) -> int:
    return max(0, min(100, int(round(score))))


def _score_grammar(resume: dict[str, Any]) -> tuple[int, list[str]]:
    result = validate_grammar(resume)
    return int(result["score"]), [
        f"{i['claim_id']}:{','.join(i['patterns'][:2])}" for i in result["issues"][:8]
    ]


def _score_readability(claims: list[tuple[str, str]]) -> tuple[int, list[str]]:
    if not claims:
        return 50, ["empty_resume"]
    lengths = [len(text.split()) for _, text in claims]
    avg = sum(lengths) / max(len(lengths), 1)
    flags: list[str] = []
    score = 90.0
    if avg > 35:
        score -= 20
        flags.append("bullets_too_long")
    if avg < 5:
        score -= 15
        flags.append("bullets_too_short")
    # Wall of text in summary
    for section, text in claims:
        if section == "summary" and len(text.split()) > 95:
            score -= 25
            flags.append("summary_wall")
        if len(text) > 280 and section != "summary":
            score -= 10
            flags.append("long_bullet")
    return _clamp(score), flags


def _score_naturalness(claims: list[tuple[str, str]]) -> tuple[int, list[str]]:
    if not claims:
        return 50, ["empty"]
    flags: list[str] = []
    score = 92.0
    blob = " ".join(t for _, t in claims).lower()
    cliche_hits = sum(1 for p in AI_CLICHE_PHRASES if p in blob)
    if cliche_hits:
        score -= min(40, cliche_hits * 12)
        flags.append(f"ai_cliches:{cliche_hits}")
    transition_hits = sum(1 for p in UNNATURAL_TRANSITIONS if p in blob)
    if transition_hits:
        score -= min(20, transition_hits * 8)
        flags.append(f"unnatural_transitions:{transition_hits}")
    weak = sum(1 for _, t in claims if any(t.lower().startswith(w) for w in WEAK_BULLET_OPENINGS))
    if weak:
        score -= min(25, weak * 8)
        flags.append(f"weak_openings:{weak}")
    return _clamp(score), flags


def _score_professional_tone(claims: list[tuple[str, str]]) -> tuple[int, list[str]]:
    flags: list[str] = []
    score = 88.0
    for _, text in claims:
        if re.search(r"[!]{2,}|\b(awesome|amazing|super|guys)\b", text, re.I):
            score -= 15
            flags.append("casual_tone")
        if re.search(r"\b(I|me|my)\b", text) and not text.lower().startswith("i "):
            # First person is uncommon on modern resumes; soft penalty
            if re.search(r"\b(I|my)\b", text):
                score -= 4
                flags.append("first_person")
    return _clamp(score), list(dict.fromkeys(flags))


def _score_flow_and_variety(claims: list[tuple[str, str]]) -> tuple[int, int, list[str]]:
    bullets = [t for s, t in claims if s != "summary"]
    flags: list[str] = []
    if not bullets:
        return 75, 75, flags
    openings = []
    for text in bullets:
        words = _WORD_RE.findall(text)
        openings.append(words[0].lower() if words else "")
    counts = Counter(openings)
    max_rep = max(counts.values()) if counts else 1
    variety = 100 - min(60, max(0, max_rep - 1) * 18)
    if max_rep >= 3:
        flags.append(f"repeated_structure:{max_rep}")
    # Flow: avoid every bullet same length band
    lengths = [len(t.split()) for t in bullets]
    if lengths:
        spread = max(lengths) - min(lengths)
        flow = 70 + min(25, spread * 3)
        if spread == 0 and len(lengths) >= 3:
            flow -= 15
            flags.append("uniform_bullet_length")
    else:
        flow = 70
    return _clamp(flow), _clamp(variety), flags


def _score_action_verbs(claims: list[tuple[str, str]]) -> tuple[int, list[str]]:
    bullets = [t for s, t in claims if s != "summary"]
    if not bullets:
        return 70, []
    strong = 0
    for text in bullets:
        words = _WORD_RE.findall(text)
        if words and words[0].lower() in _ACTION_VERBS:
            strong += 1
    ratio = strong / max(len(bullets), 1)
    score = 40 + ratio * 60
    flags = []
    if ratio < 0.45:
        flags.append("weak_action_verb_ratio")
    return _clamp(score), flags


def _score_repetition(claims: list[tuple[str, str]]) -> tuple[int, list[str]]:
    flags: list[str] = []
    score = 90.0
    for _, text in claims:
        if has_duplicate_sentence(text) or has_repeated_ngram(text):
            score -= 20
            flags.append("internal_repetition")
    # Cross-bullet near-duplicates
    norms = [re.sub(r"\s+", " ", t.lower())[:80] for _, t in claims]
    if len(norms) != len(set(norms)):
        score -= 25
        flags.append("duplicate_claims")
    return _clamp(score), flags


def _score_conciseness(claims: list[tuple[str, str]]) -> tuple[int, list[str]]:
    flags: list[str] = []
    score = 88.0
    filler = ("in order to", "the process of", "a variety of", "as well as", "due to the fact")
    for _, text in claims:
        low = text.lower()
        hits = sum(1 for f in filler if f in low)
        if hits:
            score -= hits * 8
            flags.append("filler_phrases")
        if len(text.split()) > 32 and "," in text and text.count(",") >= 4:
            score -= 6
            flags.append("dense_bullet")
    return _clamp(score), flags


def _score_scanning(resume: dict[str, Any], claims: list[tuple[str, str]]) -> tuple[int, list[str]]:
    flags: list[str] = []
    score = 85.0
    summary = str(
        resume.get("professional_summary") or resume.get("summary") or ""
    ).strip()
    if summary:
        words = len(summary.split())
        if 40 <= words <= 80:
            score += 8
        elif words > 100:
            score -= 20
            flags.append("summary_hard_to_scan")
        elif words < 25:
            score -= 10
            flags.append("summary_too_thin")
    for entry in resume.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        bullets = [str(b) for b in (entry.get("bullets") or []) if str(b).strip()]
        if len(bullets) > 6:
            score -= 10
            flags.append("too_many_bullets")
        if any(len(b.split()) > 40 for b in bullets):
            score -= 8
            flags.append("bullet_wall")
    for entry in resume.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        desc = str(entry.get("description") or "").strip()
        if desc and len(desc.split()) > 45:
            score -= 8
            flags.append("project_intro_long")
    return _clamp(score), flags


def _score_ai_likeness(claims: list[tuple[str, str]]) -> tuple[int, list[str]]:
    """Higher is better (more human)."""
    natural, flags = _score_naturalness(claims)
    # Invert cliché pressure into human-likeness
    human = natural
    blob = " ".join(t for _, t in claims)
    # Robotic parallel structures: many bullets with identical "X and Y" shape
    pattern_hits = 0
    for _, text in claims:
        if re.match(r"^(Utilized|Leveraged|Spearheaded|Orchestrated)\b", text):
            pattern_hits += 1
    if pattern_hits >= 2:
        human -= 20
        flags.append("robotic_verb_cluster")
    if has_duplicate_sentence(blob):
        human -= 15
        flags.append("duplicate_sentence_global")
    return _clamp(human), flags


def evaluate_writing_quality(
    resume: dict[str, Any],
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Score writing quality dimensions. Request section rewrites below threshold."""
    claims = _claims(resume)
    ling = validate_resume_linguistics(resume)

    grammar, g_flags = _score_grammar(resume)
    readability, r_flags = _score_readability(claims)
    naturalness, n_flags = _score_naturalness(claims)
    tone, t_flags = _score_professional_tone(claims)
    flow, variety, fv_flags = _score_flow_and_variety(claims)
    action, a_flags = _score_action_verbs(claims)
    repetition, rep_flags = _score_repetition(claims)
    conciseness, c_flags = _score_conciseness(claims)
    scanning, s_flags = _score_scanning(resume, claims)
    ai_human, ai_flags = _score_ai_likeness(claims)

    dimensions = {
        "grammar": grammar,
        "readability": readability,
        "naturalness": naturalness,
        "professional_tone": tone,
        "flow": flow,
        "sentence_variety": variety,
        "action_verbs": action,
        "repetition": repetition,
        "conciseness": conciseness,
        "scanning": scanning,
        "ai_likeness": ai_human,  # higher = more human
    }

    flags = list(
        dict.fromkeys(
            g_flags
            + r_flags
            + n_flags
            + t_flags
            + fv_flags
            + a_flags
            + rep_flags
            + c_flags
            + s_flags
            + ai_flags
            + (ling.get("detected_patterns") or [])
        )
    )

    weak = {name: score for name, score in dimensions.items() if score < threshold}
    affected: set[str] = set()
    if weak:
        # Map weak dimensions to sections to rewrite
        if any(
            k in weak
            for k in ("naturalness", "ai_likeness", "professional_tone", "scanning")
        ):
            affected.add("summary")
        if any(
            k in weak
            for k in (
                "action_verbs",
                "sentence_variety",
                "flow",
                "repetition",
                "conciseness",
                "readability",
                "grammar",
            )
        ):
            affected.add("experience")
            affected.add("projects")
        if "grammar" in weak:
            affected.update(validate_grammar(resume).get("affected_sections") or [])

    overall = _clamp(sum(dimensions.values()) / max(len(dimensions), 1))
    return {
        "passed": len(weak) == 0 and bool(ling.get("passed", True)),
        "overall_score": overall,
        "threshold": threshold,
        "dimensions": dimensions,
        "weak_dimensions": weak,
        "affected_sections": sorted(affected),
        "flags": flags,
        "regeneration_required": len(weak) > 0 or bool(ling.get("regeneration_required")),
        "linguistic_integrity": ling,
        "validator": "WritingQualityValidator",
    }


class WritingQualityValidator:
    def __init__(self, threshold: int = DEFAULT_THRESHOLD):
        self.threshold = threshold

    def validate(self, resume: dict[str, Any]) -> dict[str, Any]:
        return evaluate_writing_quality(resume, threshold=self.threshold)


class StyleValidator(WritingQualityValidator):
    """Alias matching the requested StyleValidator name."""

    pass
