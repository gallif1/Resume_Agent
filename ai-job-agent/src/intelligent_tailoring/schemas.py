"""Strict output schema for Intelligent Resume Tailoring.

Validation is deterministic (no Pydantic dependency). On schema violation the
pipeline retries the LLM once with a correction note, then fails cleanly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

INFERENCE_CATEGORIES = (
    "Explicit",
    "Strongly Inferred",
    "Weakly Inferred",
    "Unsupported",
)

ALLOWED_INFERENCE_IN_RESUME = frozenset({"Explicit", "Strongly Inferred"})

TRIAGE_ACTIONS = (
    "Preserve",
    "Rewrite",
    "Reorder",
    "Expand",
    "Condense",
    "Remove",
)

JSON_RETRY_NOTE = (
    "Your previous response was not valid JSON matching the required schema. "
    "Return ONLY the JSON object, nothing else."
)

PIPELINE_VERSION = "single_agent_v1_0"


@dataclass
class ChangeLogItem:
    original_text: str = ""
    new_text: str = ""
    reason: str = ""
    supporting_evidence: str = ""
    related_job_requirement: str = ""
    inference_category: str = "Explicit"
    confidence_score: float = 1.0
    accepted: bool | None = None  # None = pending user review
    section: str = ""
    change_type: str = ""
    source_fact_ids: list[str] = field(default_factory=list)
    evidence_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("evidence_type"):
            data["evidence_type"] = data.get("inference_category") or "Explicit"
        if data.get("confidence_score") is not None:
            data["confidence"] = data["confidence_score"]
        return data


@dataclass
class InferredCompetency:
    statement: str
    supporting_evidence: str
    reasoning: str
    confidence_score: float
    related_requirement: str
    ontology_rule_id: str = ""
    inference_category: str = "Strongly Inferred"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationWarning:
    statement: str
    reason: str
    inference_category: str = "Unsupported"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TailoredResume:
    professional_summary: str = ""
    skills: list[str] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    certifications: list[dict[str, Any] | str] = field(default_factory=list)
    professional_title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TailoringResult:
    """Full structured output returned by the pipeline and persisted for audit."""

    tailored_resume: TailoredResume = field(default_factory=TailoredResume)
    matched_requirements: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    inferred_competencies: list[InferredCompetency] = field(default_factory=list)
    removed_or_deprioritized_content: list[str] = field(default_factory=list)
    ats_keywords_added: list[str] = field(default_factory=list)
    change_log: list[ChangeLogItem] = field(default_factory=list)
    validation_warnings: list[ValidationWarning] = field(default_factory=list)
    original_match_score: int = 0
    tailored_match_score: int = 0
    language: str = "en"
    evidence_map: list[dict[str, Any]] = field(default_factory=list)
    job_requirements: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False
    pipeline_version: str = PIPELINE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "tailored_resume": self.tailored_resume.to_dict(),
            "matched_requirements": list(self.matched_requirements),
            "missing_requirements": list(self.missing_requirements),
            "inferred_competencies": [c.to_dict() for c in self.inferred_competencies],
            "removed_or_deprioritized_content": list(
                self.removed_or_deprioritized_content
            ),
            "ats_keywords_added": list(self.ats_keywords_added),
            "change_log": [c.to_dict() for c in self.change_log],
            "validation_warnings": [w.to_dict() for w in self.validation_warnings],
            "original_match_score": int(self.original_match_score),
            "tailored_match_score": int(self.tailored_match_score),
            "language": self.language,
            "evidence_map": list(self.evidence_map),
            "job_requirements": dict(self.job_requirements),
            "from_cache": bool(self.from_cache),
            "pipeline_version": self.pipeline_version,
        }


REQUIRED_TOP_LEVEL_KEYS = (
    "tailored_resume",
    "matched_requirements",
    "missing_requirements",
    "inferred_competencies",
    "removed_or_deprioritized_content",
    "ats_keywords_added",
    "change_log",
    "validation_warnings",
    "original_match_score",
    "tailored_match_score",
)

REQUIRED_RESUME_KEYS = (
    "professional_summary",
    "skills",
    "experience",
    "projects",
    "education",
    "certifications",
)

REQUIRED_CHANGE_LOG_KEYS = (
    "original_text",
    "new_text",
    "reason",
    "supporting_evidence",
    "related_job_requirement",
    "inference_category",
    "confidence_score",
)


class SchemaValidationError(ValueError):
    """Raised when LLM output or assembled result violates the strict schema."""


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))


def _clamp_score(value: Any, default: int = 0) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, score))


def normalize_inference_category(value: Any) -> str:
    text = str(value or "").strip()
    for cat in INFERENCE_CATEGORIES:
        if text.lower() == cat.lower():
            return cat
        # Accept compact forms like "strongly_inferred"
        if text.lower().replace("_", " ").replace("-", " ") == cat.lower():
            return cat
    return "Unsupported"


def sanitize_change_log_raw(raw_log: Any) -> list[dict[str, Any]]:
    """Normalize LLM change_log output before strict schema validation.

    Models sometimes return strings, partial objects, or omit keys. Salvage what
    we can and drop unusable entries instead of failing the entire tailoring run.
    """
    if raw_log is None:
        return []
    if isinstance(raw_log, dict):
        # Some models nest under "changes" or "entries"
        nested = raw_log.get("changes") or raw_log.get("entries") or raw_log.get("items")
        if isinstance(nested, list):
            raw_log = nested
        else:
            return []
    if not isinstance(raw_log, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw_log:
        if isinstance(item, dict):
            entry = _normalize_change_log_dict(item)
        elif isinstance(item, str) and item.strip():
            entry = _normalize_change_log_dict(
                {
                    "original_text": "",
                    "new_text": item.strip(),
                    "reason": "Tailoring change (auto-normalized from string entry)",
                }
            )
        else:
            continue

        category = normalize_inference_category(entry.get("inference_category"))
        entry["inference_category"] = category
        if category == "Strongly Inferred":
            if not str(entry.get("supporting_evidence") or "").strip():
                # Downgrade — do not invent Explicit cover for weak evidence
                entry["inference_category"] = "Unsupported"
                entry["accepted"] = False
            elif not str(entry.get("reason") or "").strip():
                entry["reason"] = "Strongly inferred competency from resume evidence"
        if category in ("Weakly Inferred", "Unsupported"):
            entry["accepted"] = False
        normalized.append(entry)
    return normalized


def _normalize_change_log_dict(item: dict[str, Any]) -> dict[str, Any]:
    reason = str(item.get("reason") or item.get("reasoning") or "").strip()
    related = str(
        item.get("related_job_requirement")
        or item.get("related_requirement")
        or item.get("requirement")
        or ""
    ).strip()
    confidence_raw = item.get("confidence_score", item.get("confidence", 1.0))
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 1.0
    fact_ids = item.get("source_fact_ids") or []
    if not isinstance(fact_ids, list):
        fact_ids = []

    return {
        "original_text": str(item.get("original_text") or item.get("before") or ""),
        "new_text": str(item.get("new_text") or item.get("after") or item.get("text") or ""),
        "reason": reason,
        "supporting_evidence": str(
            item.get("supporting_evidence") or item.get("evidence") or ""
        ),
        "related_job_requirement": related,
        "inference_category": str(item.get("inference_category") or "Explicit"),
        "confidence_score": max(0.0, min(1.0, confidence)),
        "accepted": item.get("accepted"),
        "section": str(item.get("section") or ""),
        "change_type": str(item.get("change_type") or ""),
        "source_fact_ids": [str(x) for x in fact_ids if str(x).strip()],
        "evidence_type": str(
            item.get("evidence_type") or item.get("inference_category") or "Explicit"
        ),
    }


def validate_change_log_item(item: Any, *, index: int = 0) -> ChangeLogItem:
    if not isinstance(item, dict):
        raise SchemaValidationError(f"change_log[{index}] must be an object")
    for key in REQUIRED_CHANGE_LOG_KEYS:
        if key not in item:
            raise SchemaValidationError(
                f"change_log[{index}] missing required key: {key}"
            )
    category = normalize_inference_category(item.get("inference_category"))
    confidence = _clamp_confidence(item.get("confidence_score"), 0.0)
    if category == "Strongly Inferred":
        if not str(item.get("supporting_evidence") or "").strip():
            raise SchemaValidationError(
                f"change_log[{index}] Strongly Inferred requires supporting_evidence"
            )
        if not str(item.get("reason") or "").strip():
            raise SchemaValidationError(
                f"change_log[{index}] Strongly Inferred requires reason/reasoning"
            )
    fact_ids = item.get("source_fact_ids") or []
    if not isinstance(fact_ids, list):
        fact_ids = []
    return ChangeLogItem(
        original_text=str(item.get("original_text") or ""),
        new_text=str(item.get("new_text") or ""),
        reason=str(item.get("reason") or ""),
        supporting_evidence=str(item.get("supporting_evidence") or ""),
        related_job_requirement=str(item.get("related_job_requirement") or ""),
        inference_category=category,
        confidence_score=confidence,
        accepted=item.get("accepted"),
        section=str(item.get("section") or ""),
        change_type=str(item.get("change_type") or ""),
        source_fact_ids=[str(x) for x in fact_ids if str(x).strip()],
        evidence_type=str(
            item.get("evidence_type") or category or "Explicit"
        ),
    )


def validate_tailored_resume(value: Any) -> TailoredResume:
    if not isinstance(value, dict):
        raise SchemaValidationError("tailored_resume must be an object")
    for key in REQUIRED_RESUME_KEYS:
        if key not in value:
            # Accept legacy aliases from the older match_tailor schema.
            if key == "professional_summary" and "summary" in value:
                continue
            raise SchemaValidationError(f"tailored_resume missing key: {key}")

    summary = str(
        value.get("professional_summary") or value.get("summary") or ""
    ).strip()
    skills = [str(s).strip() for s in _as_list(value.get("skills")) if str(s).strip()]
    experience = [e for e in _as_list(value.get("experience")) if isinstance(e, dict)]
    projects = [p for p in _as_list(value.get("projects")) if isinstance(p, dict)]
    education = [e for e in _as_list(value.get("education")) if isinstance(e, dict)]
    certifications = list(_as_list(value.get("certifications")))
    return TailoredResume(
        professional_summary=summary,
        skills=skills,
        experience=experience,
        projects=projects,
        education=education,
        certifications=certifications,
        professional_title=str(value.get("professional_title") or "").strip(),
    )


def validate_tailoring_result(data: Any) -> TailoringResult:
    """Strict-validate a full pipeline result dict."""
    if not isinstance(data, dict):
        raise SchemaValidationError("tailoring result must be a JSON object")
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            raise SchemaValidationError(f"missing required top-level key: {key}")

    resume = validate_tailored_resume(data.get("tailored_resume"))
    change_log = [
        validate_change_log_item(item, index=i)
        for i, item in enumerate(_as_list(data.get("change_log")))
    ]

    # Final resume may only contain Explicit / Strongly Inferred changes.
    for i, item in enumerate(change_log):
        if item.inference_category not in ALLOWED_INFERENCE_IN_RESUME:
            if item.accepted is True:
                raise SchemaValidationError(
                    f"change_log[{i}] category {item.inference_category} cannot be "
                    "accepted into the tailored resume"
                )

    inferred: list[InferredCompetency] = []
    for i, raw in enumerate(_as_list(data.get("inferred_competencies"))):
        if not isinstance(raw, dict):
            raise SchemaValidationError(f"inferred_competencies[{i}] must be an object")
        statement = str(raw.get("statement") or "").strip()
        if not statement:
            continue
        category = normalize_inference_category(
            raw.get("inference_category") or "Strongly Inferred"
        )
        if category != "Strongly Inferred":
            # Drop anything that is not Strongly Inferred from this list.
            continue
        evidence = str(raw.get("supporting_evidence") or "").strip()
        reasoning = str(raw.get("reasoning") or raw.get("reason") or "").strip()
        if not evidence or not reasoning:
            raise SchemaValidationError(
                f"inferred_competencies[{i}] requires supporting_evidence and reasoning"
            )
        inferred.append(
            InferredCompetency(
                statement=statement,
                supporting_evidence=evidence,
                reasoning=reasoning,
                confidence_score=_clamp_confidence(raw.get("confidence_score"), 0.0),
                related_requirement=str(
                    raw.get("related_requirement")
                    or raw.get("related_job_requirement")
                    or ""
                ),
                ontology_rule_id=str(raw.get("ontology_rule_id") or ""),
                inference_category="Strongly Inferred",
            )
        )

    warnings: list[ValidationWarning] = []
    for raw in _as_list(data.get("validation_warnings")):
        if isinstance(raw, dict):
            warnings.append(
                ValidationWarning(
                    statement=str(raw.get("statement") or ""),
                    reason=str(raw.get("reason") or ""),
                    inference_category=normalize_inference_category(
                        raw.get("inference_category") or "Unsupported"
                    ),
                )
            )
        elif raw:
            warnings.append(ValidationWarning(statement=str(raw), reason="flagged"))

    return TailoringResult(
        tailored_resume=resume,
        matched_requirements=[
            str(x).strip()
            for x in _as_list(data.get("matched_requirements"))
            if str(x).strip()
        ],
        missing_requirements=[
            str(x).strip()
            for x in _as_list(data.get("missing_requirements"))
            if str(x).strip()
        ],
        inferred_competencies=inferred,
        removed_or_deprioritized_content=[
            str(x).strip()
            for x in _as_list(data.get("removed_or_deprioritized_content"))
            if str(x).strip()
        ],
        ats_keywords_added=[
            str(x).strip()
            for x in _as_list(data.get("ats_keywords_added"))
            if str(x).strip()
        ],
        change_log=change_log,
        validation_warnings=warnings,
        original_match_score=_clamp_score(data.get("original_match_score")),
        tailored_match_score=_clamp_score(data.get("tailored_match_score")),
        language=str(data.get("language") or "en"),
        evidence_map=[
            e for e in _as_list(data.get("evidence_map")) if isinstance(e, dict)
        ],
        job_requirements=(
            data.get("job_requirements")
            if isinstance(data.get("job_requirements"), dict)
            else {}
        ),
        from_cache=bool(data.get("from_cache")),
        pipeline_version=str(data.get("pipeline_version") or PIPELINE_VERSION),
    )


def tailored_resume_to_legacy_cv(resume: TailoredResume | dict[str, Any]) -> dict[str, Any]:
    """Map the new schema onto the legacy tailored_cv shape used by renderers."""
    if isinstance(resume, TailoredResume):
        data = resume.to_dict()
    else:
        data = resume if isinstance(resume, dict) else {}
    return {
        "professional_title": str(data.get("professional_title") or "").strip(),
        "summary": str(
            data.get("professional_summary") or data.get("summary") or ""
        ).strip(),
        "professional_summary": str(
            data.get("professional_summary") or data.get("summary") or ""
        ).strip(),
        "skills": list(data.get("skills") or []),
        "experience": list(data.get("experience") or []),
        "projects": list(data.get("projects") or []),
        "education": list(data.get("education") or []),
        "certifications": list(data.get("certifications") or []),
    }
