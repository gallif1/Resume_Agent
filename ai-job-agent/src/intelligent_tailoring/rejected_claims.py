"""Shared RejectedClaims registry for the multi-agent pipeline.

Once a claim is rejected, no later agent may reintroduce it (or a near-duplicate).
Maximum three revision cycles may consult this registry when regenerating sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


def _norm(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip().lower())
    text = re.sub(r"[^\w\s\u0590-\u05FF.+#/%-]", "", text)
    return text


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9\u0590-\u05FF.+#-]{3,}", _norm(text))}


@dataclass
class RejectedClaim:
    text: str
    reason: str = ""
    source_agent: str = ""
    section: str = ""
    claim_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "reason": self.reason,
            "source_agent": self.source_agent,
            "section": self.section,
            "claim_id": self.claim_id,
            "normalized": _norm(self.text),
        }


@dataclass
class RejectedClaimsRegistry:
    """Pipeline-scoped registry — rejected claims cannot return later."""

    claims: list[RejectedClaim] = field(default_factory=list)
    revision_cycle: int = 0
    max_revision_cycles: int = 3
    _normalized: set[str] = field(default_factory=set, repr=False)

    def add(
        self,
        text: str,
        *,
        reason: str = "",
        source_agent: str = "",
        section: str = "",
        claim_id: str = "",
    ) -> bool:
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        key = _norm(cleaned)
        if not key or key in self._normalized:
            return False
        self._normalized.add(key)
        self.claims.append(
            RejectedClaim(
                text=cleaned,
                reason=reason,
                source_agent=source_agent,
                section=section,
                claim_id=claim_id or f"rej_{len(self.claims)+1}",
            )
        )
        return True

    def extend(
        self,
        texts: Iterable[str],
        *,
        reason: str = "",
        source_agent: str = "",
        section: str = "",
    ) -> int:
        added = 0
        for text in texts or []:
            if self.add(
                str(text),
                reason=reason,
                source_agent=source_agent,
                section=section,
            ):
                added += 1
        return added

    def contains(self, text: str, *, similarity: float = 0.82) -> bool:
        """True when text equals or closely matches a rejected claim."""
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        key = _norm(cleaned)
        if key in self._normalized:
            return True
        tokens = _token_set(cleaned)
        if len(tokens) < 3:
            return False
        for claim in self.claims:
            other = _token_set(claim.text)
            if not other:
                continue
            overlap = len(tokens & other) / max(len(tokens), len(other), 1)
            if overlap >= similarity:
                return True
            # Contained near-duplicate (rewrite that keeps the rejected core)
            if key and (_norm(claim.text) in key or key in _norm(claim.text)):
                if len(key) >= 24 or len(_norm(claim.text)) >= 24:
                    return True
        return False

    def filter_text(self, text: str) -> str | None:
        """Return None when the whole statement is rejected; else original text."""
        if self.contains(text):
            return None
        return text

    def filter_bullets(self, bullets: Iterable[Any]) -> list[str]:
        kept: list[str] = []
        for raw in bullets or []:
            text = str(raw).strip()
            if not text or self.contains(text):
                continue
            kept.append(text)
        return kept

    def scrub_resume(self, resume: dict[str, Any]) -> dict[str, Any]:
        """Remove previously rejected statements from a resume dict."""
        out = dict(resume or {})
        summary = str(out.get("professional_summary") or out.get("summary") or "")
        if summary and self.contains(summary):
            out["professional_summary"] = ""
            out["summary"] = ""

        experience: list[dict[str, Any]] = []
        for role in list(out.get("experience") or []):
            if not isinstance(role, dict):
                continue
            entry = dict(role)
            entry["bullets"] = self.filter_bullets(entry.get("bullets") or [])
            experience.append(entry)
        out["experience"] = experience

        projects: list[dict[str, Any]] = []
        for proj in list(out.get("projects") or []):
            if not isinstance(proj, dict):
                continue
            entry = dict(proj)
            desc = str(entry.get("description") or "").strip()
            if desc and self.contains(desc):
                entry["description"] = ""
            entry["bullets"] = self.filter_bullets(entry.get("bullets") or [])
            projects.append(entry)
        out["projects"] = projects

        # Skills atoms that were rejected as unsupported claims
        cleaned_skills: list[str] = []
        for line in list(out.get("skills") or []):
            text = str(line).strip()
            if not text:
                continue
            if ":" in text:
                category, rest = text.split(":", 1)
                atoms = [a.strip() for a in rest.split(",") if a.strip()]
                kept = [a for a in atoms if not self.contains(a)]
                if kept:
                    cleaned_skills.append(f"{category.strip()}: {', '.join(kept)}")
            elif not self.contains(text):
                cleaned_skills.append(text)
        out["skills"] = cleaned_skills
        return out

    def can_revise(self) -> bool:
        return self.revision_cycle < self.max_revision_cycles

    def begin_revision(self, reason: str = "") -> bool:
        if not self.can_revise():
            return False
        self.revision_cycle += 1
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": [c.to_dict() for c in self.claims],
            "revision_cycle": self.revision_cycle,
            "max_revision_cycles": self.max_revision_cycles,
            "count": len(self.claims),
        }

    @classmethod
    def from_texts(
        cls,
        texts: Iterable[str],
        *,
        reason: str = "",
        source_agent: str = "",
    ) -> "RejectedClaimsRegistry":
        reg = cls()
        reg.extend(texts, reason=reason, source_agent=source_agent)
        return reg
