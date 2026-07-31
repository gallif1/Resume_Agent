"""Deterministic, profession-agnostic polish that never invents facts.

Used as a baseline polish and as a safe fallback when LLM rewriting is
unavailable or rejected by the fact lock.
"""

from __future__ import annotations

import re
from typing import Any

from intelligent_tailoring.writing.ai_phrases import (
    AI_CLICHE_PHRASES,
    UNNATURAL_TRANSITIONS,
    WEAK_BULLET_OPENINGS,
)

_SPACE_RE = re.compile(r"\s+")
_MULTI_PUNCT = re.compile(r"([.!?]){2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:])")
_SPACE_AFTER_OPEN = re.compile(r"([(])\s+")
_SPACE_BEFORE_CLOSE = re.compile(r"\s+([)])")

_VERB_UPGRADES = (
    (re.compile(r"^Worked on\s+", re.I), "Contributed to "),
    (re.compile(r"^Helped with\s+", re.I), "Supported "),
    (re.compile(r"^Assisted with\s+", re.I), "Supported "),
    (re.compile(r"^Involved in\s+", re.I), "Supported "),
    (re.compile(r"^Participated in\s+", re.I), "Contributed to "),
    (re.compile(r"^Knowledge of\s+", re.I), "Applied "),
    (re.compile(r"^Familiar with\s+", re.I), "Used "),
    (re.compile(r"^Utilized\s+", re.I), "Used "),
    (re.compile(r"^Leveraged\s+", re.I), "Used "),
    (re.compile(r"^Helped to\s+", re.I), ""),
)

_GERUND_TO_PAST = {
    "implementing": "Implemented",
    "building": "Built",
    "developing": "Developed",
    "creating": "Created",
    "managing": "Managed",
    "leading": "Led",
    "designing": "Designed",
    "supporting": "Supported",
    "maintaining": "Maintained",
    "configuring": "Configured",
    "deploying": "Deployed",
    "testing": "Tested",
    "writing": "Wrote",
    "teaching": "Taught",
    "resolving": "Resolved",
    "coordinating": "Coordinated",
    "preparing": "Prepared",
    "handling": "Handled",
    "analyzing": "Analyzed",
    "training": "Trained",
    "scheduling": "Scheduled",
    "monitoring": "Monitored",
    "documenting": "Documented",
}


def _clean_whitespace(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = _SPACE_RE.sub(" ", text).strip()
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    text = _SPACE_BEFORE_CLOSE.sub(r"\1", text)
    text = _MULTI_PUNCT.sub(r"\1", text)
    return text


def _strip_cliches(text: str) -> str:
    out = text
    for phrase in AI_CLICHE_PHRASES:
        # Only strip when used as filler adjective clusters, not mid-tech terms
        pattern = re.compile(re.escape(phrase), re.I)
        out = pattern.sub("", out)
    for phrase in UNNATURAL_TRANSITIONS:
        out = re.sub(re.escape(phrase), "", out, flags=re.I)
    out = _clean_whitespace(out)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r"^\s*[,;]\s*", "", out)
    return _clean_whitespace(out)


def _upgrade_bullet(text: str) -> str:
    out = _clean_whitespace(text)
    out = _strip_cliches(out)
    # Convert weak lead-ins into strong past-tense openings.
    m = re.match(
        r"^(?:Responsible for|Tasked with|Duties included)\s+(\w+)(.*)$",
        out,
        flags=re.I,
    )
    if m:
        head, rest = m.group(1), m.group(2)
        past = _GERUND_TO_PAST.get(head.lower())
        if past:
            out = f"{past}{rest}"
        else:
            out = f"{head[0].upper() + head[1:]}{rest}"
    for pattern, repl in _VERB_UPGRADES:
        out = pattern.sub(repl, out)
    out = _clean_whitespace(out)
    if out:
        out = out[0].upper() + out[1:]
    # Ensure bullets don't end with orphan connectors
    out = re.sub(r"\b(and|or|with|using|to|for)\s*$", "", out, flags=re.I).strip()
    return out


def _repair_summary_gaps(text: str) -> str:
    """Clean grammatical holes left after cliché deletion."""
    out = text
    out = re.sub(r"\bwith a and\b", "with", out, flags=re.I)
    out = re.sub(r"\bwith an and\b", "with", out, flags=re.I)
    out = re.sub(r"\bwith and\b", "with", out, flags=re.I)
    out = re.sub(r"\band and\b", "and", out, flags=re.I)
    out = re.sub(r"\bof of\b", "of", out, flags=re.I)
    out = re.sub(r"\ba\s+and\b", "and", out, flags=re.I)
    out = re.sub(r"\ban\s+and\b", "and", out, flags=re.I)
    out = re.sub(r"\bin\s+\.", ".", out, flags=re.I)
    out = re.sub(r"\bin\s+$", "", out, flags=re.I)
    out = re.sub(r"\s+\.", ".", out)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" ,;")


def _polish_summary(text: str) -> str:
    original = _clean_whitespace(text)
    out = _strip_cliches(original)
    out = _repair_summary_gaps(out)
    out = _clean_whitespace(out)
    # Drop near-empty sentences created by cliché removal
    sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+", out):
        s = _repair_summary_gaps(_clean_whitespace(sentence))
        words = [w for w in s.split() if w.lower() not in {"a", "an", "the", "and", "of", "in", "with"}]
        if len(words) < 3:
            continue
        if not s.endswith((".", "!", "?")):
            s = s.rstrip(",;:") + "."
        sentences.append(s[0].upper() + s[1:] if s else s)
    out = " ".join(sentences).strip()
    words = out.split()
    if len(words) > 90:
        truncated = []
        count = 0
        for sentence in re.split(r"(?<=[.!?])\s+", out):
            sw = sentence.split()
            if count and count + len(sw) > 80:
                break
            truncated.append(sentence)
            count += len(sw)
        out = " ".join(truncated).strip()
    # If stripping left unusable prose, fall back to lightly cleaned original
    # with clichés removed only when the remainder stays readable.
    if len(out.split()) < 12 or re.search(r"\b(with a and|in s\b|,\s*\.)", out, re.I):
        fallback = _repair_summary_gaps(_strip_cliches(original))
        fallback = _clean_whitespace(fallback)
        if len(fallback.split()) >= 12 and not re.search(
            r"\b(with a and|in s\b)", fallback, re.I
        ):
            out = fallback[0].upper() + fallback[1:] if fallback else original
        else:
            # Last resort: keep original wording (still fact-safe) rather than
            # emit broken fragments. Style validators will request a rewrite.
            out = original
    return out


def _diversify_openings(bullets: list[str]) -> list[str]:
    """Lightly diversify identical openings without changing facts."""
    seen: dict[str, int] = {}
    out: list[str] = []
    synonyms = {
        "implemented": ["Built", "Developed", "Delivered"],
        "developed": ["Built", "Created", "Delivered"],
        "created": ["Built", "Developed", "Designed"],
        "managed": ["Led", "Coordinated", "Oversaw"],
        "used": ["Applied", "Adopted", "Employed"],
        "contributed": ["Supported", "Assisted", "Enabled"],
    }
    for bullet in bullets:
        words = bullet.split()
        if not words:
            out.append(bullet)
            continue
        first = re.sub(r"[^A-Za-z]", "", words[0]).lower()
        count = seen.get(first, 0)
        seen[first] = count + 1
        if count >= 1 and first in synonyms:
            alt = synonyms[first][(count - 1) % len(synonyms[first])]
            words[0] = alt
            out.append(" ".join(words))
        else:
            out.append(bullet)
    return out


def _summary_from_evidence(resume: dict[str, Any]) -> str:
    """Build a natural 2–3 sentence summary only from existing resume fields."""
    title = str(resume.get("professional_title") or "").strip()
    skills: list[str] = []
    for raw in resume.get("skills") or []:
        text = str(raw)
        if ":" in text:
            text = text.split(":", 1)[1]
        for part in re.split(r"[,|/]", text):
            atom = part.strip()
            if atom and atom.lower() not in {s.lower() for s in skills}:
                skills.append(atom)
        if len(skills) >= 4:
            break
    skill_phrase = ", ".join(skills[:4])

    evidence = ""
    for entry in resume.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        bullets = [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()]
        if bullets:
            evidence = _upgrade_bullet(bullets[0]).rstrip(".")
            company = str(entry.get("company") or "").strip()
            role = str(entry.get("title") or title).strip()
            who = role or title or "Contributor"
            where = f" at {company}" if company else ""
            focus = f" specializing in {skill_phrase}" if skill_phrase else ""
            # Avoid banned "Professional with..." patterns
            if who.lower() == "professional" and skill_phrase:
                sentence1 = f"Contributor specializing in {skill_phrase}."
            else:
                sentence1 = f"{who}{where}{focus}."
            sentence2 = f"{evidence}."
            return _clean_whitespace(f"{sentence1} {sentence2}")
    for entry in resume.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        desc = str(entry.get("description") or "").strip()
        name = str(entry.get("name") or "").strip()
        if desc or name:
            who = title or "Contributor"
            if who.lower() == "professional":
                who = "Contributor"
            focus = f" specializing in {skill_phrase}" if skill_phrase else ""
            detail = desc or name
            return _clean_whitespace(f"{who}{focus}. {detail}.")
    who = title or "Contributor"
    if who.lower() == "professional":
        who = "Contributor"
    if skill_phrase:
        return f"{who} specializing in {skill_phrase}."
    return who + "."


def polish_resume_deterministic(resume: dict[str, Any]) -> dict[str, Any]:
    """Return a polished copy. Does not add/remove entries or invent facts."""
    out: dict[str, Any] = dict(resume)
    summary = str(out.get("professional_summary") or out.get("summary") or "")
    if summary:
        polished = _polish_summary(summary)
        # Rebuild from existing evidence when the summary is still cliché-heavy
        low = polished.lower()
        still_ai = any(p in low for p in ("results-driven", "passionate about", "proven track record", "highly motivated", "seasoned professional"))
        if still_ai or len(polished.split()) < 18:
            polished = _summary_from_evidence(out)
        out["professional_summary"] = polished
        out["summary"] = polished

    experience = []
    for entry in out.get("experience") or []:
        if not isinstance(entry, dict):
            experience.append(entry)
            continue
        e = dict(entry)
        bullets = [_upgrade_bullet(str(b)) for b in (e.get("bullets") or []) if str(b).strip()]
        e["bullets"] = _diversify_openings(bullets)
        experience.append(e)
    out["experience"] = experience

    projects = []
    for entry in out.get("projects") or []:
        if not isinstance(entry, dict):
            projects.append(entry)
            continue
        e = dict(entry)
        desc = str(e.get("description") or "").strip()
        if desc:
            # Prefer a slightly fuller intro when the source is a stub sentence.
            if re.match(r"^(created|built|made)\s+.+\.$", desc, re.I) and len(desc.split()) <= 4:
                name = str(e.get("name") or "Project").strip()
                e["description"] = f"{name} — {desc[0].lower() + desc[1:]}" if desc else name
            else:
                e["description"] = _upgrade_bullet(desc)
        bullets = [_upgrade_bullet(str(b)) for b in (e.get("bullets") or []) if str(b).strip()]
        e["bullets"] = _diversify_openings(bullets)
        projects.append(e)
    out["projects"] = projects

    # Preserve skills/education/certs/title exactly (reorder only already done)
    out["skills"] = list(resume.get("skills") or [])
    out["education"] = [
        dict(e) if isinstance(e, dict) else e for e in (resume.get("education") or [])
    ]
    out["certifications"] = list(resume.get("certifications") or [])
    out["professional_title"] = str(resume.get("professional_title") or "")
    return out


def strip_ai_phrases_from_text(text: str) -> str:
    return _strip_cliches(_clean_whitespace(text))
