"""Configurable skill/competency ontology loader and mapper.

Mappings live in ``data/skill_ontology.json``. Generation logic reads this file;
new relationships can be added without modifying the pipeline code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from config import DATA_DIR

ONTOLOGY_PATH = DATA_DIR / "skill_ontology.json"

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


@dataclass(frozen=True)
class OntologyRelation:
    id: str
    sources: tuple[str, ...]
    target: str
    relation: str
    confidence: float
    also_implies: tuple[str, ...] = ()
    hedged_statement_en: str = ""
    hedged_statement_he: str = ""

    def hedged_statement(self, language: str = "en") -> str:
        if (language or "en").lower().startswith("he"):
            return self.hedged_statement_he or self.hedged_statement_en
        return self.hedged_statement_en or self.hedged_statement_he


@dataclass
class OntologyHit:
    relation: OntologyRelation
    matched_source: str
    resume_evidence: str
    inferred_competency: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ontology_rule_id": self.relation.id,
            "matched_source": self.matched_source,
            "resume_evidence": self.resume_evidence,
            "inferred_competency": self.inferred_competency,
            "confidence": self.confidence,
            "relation": self.relation.relation,
            "target": self.relation.target,
            "also_implies": list(self.relation.also_implies),
            "hedged_statement": self.relation.hedged_statement(),
        }


@dataclass
class SkillOntology:
    version: int = 1
    relationships: list[OntologyRelation] = field(default_factory=list)
    # source_norm -> list of relations
    _index: dict[str, list[OntologyRelation]] = field(default_factory=dict, repr=False)

    def build_index(self) -> None:
        index: dict[str, list[OntologyRelation]] = {}
        for rel in self.relationships:
            for source in rel.sources:
                key = _norm(source)
                if not key:
                    continue
                index.setdefault(key, []).append(rel)
        self._index = index

    def find_by_term(self, term: str) -> list[OntologyRelation]:
        return list(self._index.get(_norm(term), []))

    def map_terms(self, terms: Iterable[str]) -> list[tuple[str, OntologyRelation]]:
        """Map raw terms to ontology relations (exact + substring source match)."""
        hits: list[tuple[str, OntologyRelation]] = []
        seen: set[str] = set()
        normalized_terms = [(t, _norm(t)) for t in terms if _norm(t)]
        for original, key in normalized_terms:
            for source_key, rels in self._index.items():
                if key == source_key or source_key in key or key in source_key:
                    for rel in rels:
                        stamp = f"{rel.id}:{original}"
                        if stamp in seen:
                            continue
                        seen.add(stamp)
                        hits.append((original, rel))
        return hits

    def infer_from_resume_text(
        self,
        resume_text: str,
        *,
        min_confidence: float = 0.8,
        language: str = "en",
    ) -> list[OntologyHit]:
        """Deterministic ontology hits where a source phrase appears in the resume."""
        text = resume_text or ""
        text_l = _norm(text)
        if len(text_l) < 20:
            return []

        hits: list[OntologyHit] = []
        seen_targets: set[str] = set()
        for rel in self.relationships:
            if rel.confidence < min_confidence:
                continue
            matched = ""
            for source in rel.sources:
                src_n = _norm(source)
                if len(src_n) >= 3 and src_n in text_l:
                    matched = source
                    break
            if not matched:
                continue
            target_key = _norm(rel.target)
            if target_key in seen_targets:
                continue
            # Extract a short evidence snippet around the match.
            evidence = _snippet_around(text, matched) or matched
            statement = rel.hedged_statement(language) or rel.target
            hits.append(
                OntologyHit(
                    relation=rel,
                    matched_source=matched,
                    resume_evidence=evidence,
                    inferred_competency=statement,
                    confidence=rel.confidence,
                )
            )
            seen_targets.add(target_key)
            for extra in rel.also_implies:
                extra_key = _norm(extra)
                if extra_key and extra_key not in seen_targets:
                    seen_targets.add(extra_key)
        return hits

    def normalize_term(self, term: str) -> str:
        """Return the ontology target for a term when a mapping exists, else the term."""
        rels = self.find_by_term(term)
        if rels:
            # Prefer highest confidence.
            best = max(rels, key=lambda r: r.confidence)
            return best.target
        # Prefer word-boundary / exact match; avoid "java" matching inside "javascript".
        key = _norm(term)
        best: OntologyRelation | None = None
        for source_key, rels in self._index.items():
            if len(source_key) < 3:
                continue
            if key == source_key:
                candidate = max(rels, key=lambda r: r.confidence)
                return candidate.target
            # Allow substring only when the shorter token is a full word boundary
            # of the longer one (not a prefix inside a compound like java⊂javascript).
            if len(source_key) >= 4 and (
                re.search(rf"(^|[^a-z0-9]){re.escape(source_key)}([^a-z0-9]|$)", key)
                or re.search(rf"(^|[^a-z0-9]){re.escape(key)}([^a-z0-9]|$)", source_key)
            ):
                candidate = max(rels, key=lambda r: r.confidence)
                if best is None or candidate.confidence > best.confidence:
                    best = candidate
        return best.target if best else term.strip()

    def to_prompt_summary(self, *, max_relations: int = 80) -> str:
        """Compact ontology text for LLM stage prompts."""
        lines: list[str] = []
        for rel in self.relationships[:max_relations]:
            sources = ", ".join(rel.sources[:6])
            extras = (
                f" (+ {', '.join(rel.also_implies[:4])})" if rel.also_implies else ""
            )
            lines.append(
                f"- [{rel.id}] ({rel.relation}, conf={rel.confidence:.2f}) "
                f"{sources} → {rel.target}{extras}"
            )
        return "\n".join(lines)


def _snippet_around(text: str, needle: str, *, radius: int = 80) -> str:
    lower = text.lower()
    idx = lower.find(needle.lower())
    if idx < 0:
        return ""
    start = max(0, idx - radius)
    end = min(len(text), idx + len(needle) + radius)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return _WS_RE.sub(" ", snippet)


def _parse_relation(raw: dict[str, Any]) -> OntologyRelation | None:
    source = raw.get("source")
    if isinstance(source, str):
        sources = (source,)
    elif isinstance(source, list):
        sources = tuple(str(s).strip() for s in source if str(s).strip())
    else:
        return None
    target = str(raw.get("target") or "").strip()
    if not sources or not target:
        return None
    hedged = raw.get("hedged_statement") or {}
    if not isinstance(hedged, dict):
        hedged = {}
    also = raw.get("also_implies") or []
    if not isinstance(also, list):
        also = []
    try:
        confidence = float(raw.get("confidence") or 0.8)
    except (TypeError, ValueError):
        confidence = 0.8
    return OntologyRelation(
        id=str(raw.get("id") or target),
        sources=sources,
        target=target,
        relation=str(raw.get("relation") or "tool_to_competency"),
        confidence=max(0.0, min(1.0, confidence)),
        also_implies=tuple(str(x).strip() for x in also if str(x).strip()),
        hedged_statement_en=str(hedged.get("en") or ""),
        hedged_statement_he=str(hedged.get("he") or ""),
    )


def load_ontology(path: Path | None = None) -> SkillOntology:
    """Load ontology from disk. Missing/invalid files yield an empty ontology."""
    ontology_path = path or ONTOLOGY_PATH
    ontology = SkillOntology()
    if not ontology_path.exists():
        ontology.build_index()
        return ontology
    try:
        data = json.loads(ontology_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ontology.build_index()
        return ontology
    if not isinstance(data, dict):
        ontology.build_index()
        return ontology
    ontology.version = int(data.get("version") or 1)
    relations: list[OntologyRelation] = []
    for raw in data.get("relationships") or []:
        if isinstance(raw, dict):
            rel = _parse_relation(raw)
            if rel:
                relations.append(rel)
    ontology.relationships = relations
    ontology.build_index()
    return ontology


@lru_cache(maxsize=4)
def get_ontology(path_str: str | None = None) -> SkillOntology:
    return load_ontology(Path(path_str) if path_str else None)


def clear_ontology_cache() -> None:
    get_ontology.cache_clear()


def dedupe_skills(skills: Iterable[str]) -> list[str]:
    """Deterministic skill dedupe across normalization paths."""
    seen: set[str] = set()
    result: list[str] = []
    for skill in skills:
        text = str(skill or "").strip()
        if not text:
            continue
        key = _norm(text)
        # Collapse "Category: a, b" rows atom-wise when comparing plain duplicates.
        if key in seen:
            continue
        # Also skip if a shorter/longer near-duplicate already exists.
        if any(key == s or key in s or s in key for s in seen if len(s) >= 3):
            # Prefer the longer, more specific form already stored — skip new.
            if any(key in s and key != s for s in seen):
                continue
            # Prefer replacing a shorter entry with a longer one.
            to_remove = [s for s in seen if s in key and s != key]
            for old in to_remove:
                seen.discard(old)
                result = [r for r in result if _norm(r) != old]
        seen.add(key)
        result.append(text)
    return result
