"""Optional Quality Intelligence layer.

Stores anonymous generation metadata only — never personal information,
never resume copies, never generated content memorization.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("intelligent_tailoring.quality_intelligence")

_LOCK = threading.Lock()


@dataclass
class AnonymousGenerationMetrics:
    """Aggregate-safe metrics for improving prompts/heuristics."""

    pipeline_version: str = ""
    job_family: str = "general"
    industry: str = "general"
    theme_id: str = ""
    language: str = "en"
    overall_fit: int = 0
    technical_fit: int = 0
    business_fit: int = 0
    resume_quality: int = 0
    evidence_quality: int = 0
    recruiter_interview_quality: int = 0
    recruiter_human_score: int = 0
    would_interview: bool = False
    hard_requirement_coverage: float = 0.0
    summary_word_count: int = 0
    experience_bullet_count: int = 0
    section_order: list[str] = field(default_factory=list)
    agent_timings_ms: dict[str, int] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _store_path() -> Path:
    override = os.environ.get("QUALITY_INTELLIGENCE_PATH", "").strip()
    if override:
        return Path(override)
    # Prefer package data dir when available
    try:
        from config import DATA_DIR

        base = Path(DATA_DIR)
    except Exception:  # noqa: BLE001
        base = Path(__file__).resolve().parents[3] / "data"
    return base / "quality_intelligence" / "metrics.jsonl"


def is_enabled() -> bool:
    flag = os.environ.get("QUALITY_INTELLIGENCE_ENABLED", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def record_generation_metrics(metrics: AnonymousGenerationMetrics | dict[str, Any]) -> bool:
    """Append one anonymous metrics row. Returns False if disabled/failed."""
    if not is_enabled():
        return False
    payload = metrics.to_dict() if isinstance(metrics, AnonymousGenerationMetrics) else dict(metrics)
    # Hard scrub — never persist free-form resume/JD text fields if passed accidentally
    for banned in (
        "resume",
        "resume_text",
        "jd_text",
        "summary",
        "bullets",
        "name",
        "email",
        "phone",
        "raw_text",
        "generated_content",
    ):
        payload.pop(banned, None)

    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return True
    except OSError as exc:
        logger.warning("quality_intelligence: failed to persist metrics: %s", exc)
        return False


def aggregate_insights(limit: int = 500) -> dict[str, Any]:
    """Compute simple aggregates for prompt/heuristic tuning."""
    path = _store_path()
    if not path.exists():
        return {"count": 0, "by_job_family": {}, "by_theme": {}, "avg_scores": {}}

    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return {"count": 0, "by_job_family": {}, "by_theme": {}, "avg_scores": {}}

    rows = rows[-limit:]
    if not rows:
        return {"count": 0, "by_job_family": {}, "by_theme": {}, "avg_scores": {}}

    def _avg(key: str) -> float:
        vals = [float(r.get(key) or 0) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    by_family: dict[str, list[float]] = {}
    by_theme: dict[str, list[float]] = {}
    for row in rows:
        fam = str(row.get("job_family") or "general")
        theme = str(row.get("theme_id") or "unknown")
        by_family.setdefault(fam, []).append(float(row.get("overall_fit") or 0))
        by_theme.setdefault(theme, []).append(float(row.get("resume_quality") or 0))

    return {
        "count": len(rows),
        "by_job_family": {
            k: round(sum(v) / len(v), 2) for k, v in by_family.items() if v
        },
        "by_theme": {k: round(sum(v) / len(v), 2) for k, v in by_theme.items() if v},
        "avg_scores": {
            "overall_fit": _avg("overall_fit"),
            "resume_quality": _avg("resume_quality"),
            "evidence_quality": _avg("evidence_quality"),
            "recruiter_interview_quality": _avg("recruiter_interview_quality"),
            "hard_requirement_coverage": _avg("hard_requirement_coverage"),
        },
        "interview_rate": round(
            sum(1 for r in rows if r.get("would_interview")) / len(rows), 3
        ),
    }


def build_metrics_from_pipeline(
    *,
    pipeline_version: str,
    job_family: str,
    industry: str,
    language: str,
    hiring_manager: dict[str, Any] | None,
    recruiter: dict[str, Any] | None,
    strategy: dict[str, Any] | None,
    resume: dict[str, Any] | None,
    evidence_coverage: float,
    agent_timings_ms: dict[str, int] | None = None,
    theme_id: str = "",
) -> AnonymousGenerationMetrics:
    hm = hiring_manager or {}
    rev = recruiter or {}
    strat = strategy or {}
    res = resume or {}
    summary = str(res.get("professional_summary") or res.get("summary") or "")
    bullets = 0
    for entry in res.get("experience") or []:
        if isinstance(entry, dict):
            bullets += len(entry.get("bullets") or [])
    return AnonymousGenerationMetrics(
        pipeline_version=pipeline_version,
        job_family=job_family or "general",
        industry=industry or "general",
        theme_id=theme_id,
        language=language or "en",
        overall_fit=int(hm.get("overall_fit") or 0),
        technical_fit=int(hm.get("technical_fit") or 0),
        business_fit=int(hm.get("business_fit") or 0),
        resume_quality=int(hm.get("resume_quality") or 0),
        evidence_quality=int(hm.get("evidence_quality") or 0),
        recruiter_interview_quality=int(rev.get("interview_quality") or 0),
        recruiter_human_score=int(rev.get("human_believability") or 0),
        would_interview=bool(rev.get("would_interview") or hm.get("overall_fit", 0) >= 70),
        hard_requirement_coverage=float(evidence_coverage or 0.0),
        summary_word_count=len(summary.split()),
        experience_bullet_count=bullets,
        section_order=[str(x) for x in (strat.get("section_order") or [])][:12],
        agent_timings_ms=dict(agent_timings_ms or {}),
    )
