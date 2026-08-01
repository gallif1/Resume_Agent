"""Tailored-CV documents built from the honest match evaluation.

This module owns the tailored-CV *document*: it renders the structured
``tailored_cv`` produced by :mod:`match_tailor_service` into resume Markdown,
persists it, records score history and shapes the API response.

Requirement extraction, scoring and resume writing all live in
:mod:`match_tailor_service`. There is deliberately no second prompt or scoring
rule here — one pipeline decides what a candidate's fit is and what their
tailored CV says, so the job list, the tailored-CV view and the PDF can never
disagree with each other.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_client import truncate_text
from config import (
    AGENT_USER_ID,
    OPENAI_CV_MAX_CHARS,
    cv_data_dir,
    user_cv_profile_path,
    user_data_dir,
)
from db import (
    DEFAULT_USER_ID,
    WORKSPACE_CV_ID,
    apply_honest_match_score,
    get_latest_cv_tailor_version,
    list_cv_tailor_versions,
    record_cv_tailor_version,
    save_tailored_resume_report,
)
from match_scoring import score_label_for
from intelligent_tailoring import (
    PIPELINE_VERSION as INTELLIGENT_PIPELINE_VERSION,
    IntelligentTailorError,
    run_intelligent_tailoring,
)
from match_tailor_service import MatchTailorError

NO_IMPROVEMENT_MESSAGE = "לא הצלחתי לייצר גרסה יותר טובה"

# Stamped into every saved draft so drafts written by an older pipeline are
# regenerated instead of being served with their stale scores. The marker lives
# only on disk — it is stripped before the document reaches the API or the PDF.
# Intelligent Resume Tailoring supersedes the single mega-prompt match_tailor path.
TAILOR_PIPELINE_VERSION = INTELLIGENT_PIPELINE_VERSION
_PIPELINE_MARKER_RE = re.compile(r"^<!--\s*tailor-pipeline:\s*(\S+)\s*-->\s*\n?")

HR_SPLIT_RE = re.compile(r"\n---\s*\n", re.MULTILINE)
CV_SECTION_HEADING_RE = re.compile(
    r"^##\s*(?:קורות החיים המעודכנים|The Tailored CV|Tailored CV)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
SCORE_IN_TEXT_RE = re.compile(
    r"(?:ציון(?:\s+משוער)?|score|ATS)[^\d]{0,40}?(\d{1,3})(?:\s*/\s*100)?",
    re.IGNORECASE,
)
# "שיפרנו את ההתאמה למשרה מ־62 ל־71" — the score after tailoring is the second one.
SCORE_PROGRESSION_RE = re.compile(r"ל־\s*(\d{1,3})")

CONTACT_FIELD_ORDER = ("location", "phone", "email", "linkedin", "github", "portfolio")


class TailorCvError(RuntimeError):
    """Raised when CV tailoring cannot be completed."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# --------------------------------------------------------------------------- #
# Paths & document splitting
# --------------------------------------------------------------------------- #


def tailored_cv_dir(cv_id: str) -> Path:
    if cv_id == WORKSPACE_CV_ID and (AGENT_USER_ID or DEFAULT_USER_ID):
        return user_data_dir(AGENT_USER_ID or DEFAULT_USER_ID) / "tailored_cvs"
    return cv_data_dir(cv_id) / "tailored_cvs"


def tailored_cv_path(cv_id: str, job_id: int) -> Path:
    return tailored_cv_dir(cv_id) / f"{job_id}.md"


def split_tailored_markdown(markdown: str) -> tuple[str, str]:
    """Split full tailor output into (preamble, cv_body).

    Prefers the content after the first horizontal rule (`---`). Falls back to
    the "## קורות החיים המעודכנים" heading, then to the full document.
    """
    text = (markdown or "").strip()
    if not text:
        return "", ""

    parts = HR_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        preamble = parts[0].strip()
        body = parts[1].strip()
        body = CV_SECTION_HEADING_RE.sub("", body, count=1).strip()
        return preamble, body

    heading = CV_SECTION_HEADING_RE.search(text)
    if heading:
        preamble = text[: heading.start()].strip()
        body = text[heading.end() :].strip()
        return preamble, body

    return "", text


def extract_cv_markdown_for_copy(markdown: str | dict[str, Any] | None) -> str:
    """Return the resume body suitable for clipboard / download of the CV only.

    Accepts either the full tailored markdown string or a tailor result dict
    (``markdown`` / ``cv_markdown`` keys) so API callers cannot crash with 500.
    """
    if isinstance(markdown, dict):
        preferred = markdown.get("cv_markdown") or markdown.get("markdown") or ""
        text = preferred if isinstance(preferred, str) else ""
    else:
        text = markdown or ""
    if not isinstance(text, str):
        text = str(text)
    _, body = split_tailored_markdown(text)
    return body or text.strip()


def _clamp_score(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, score))


def _parse_score_from_markdown(markdown: str) -> int | None:
    """Recover the score a saved document reports, so drafts are self-describing."""
    text = markdown or ""
    progression = SCORE_PROGRESSION_RE.search(text)
    if progression:
        return _clamp_score(progression.group(1))
    match = SCORE_IN_TEXT_RE.search(text)
    if not match:
        return None
    return _clamp_score(match.group(1))


def _string_list(value: Any, *, max_items: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _assemble_structured_markdown(
    *,
    changes_breakdown: list[str],
    estimated_ats_score: int | None,
    cv_markdown: str,
    score_line: str | None = None,
    score_notes: list[str] | None = None,
) -> str:
    change_lines = "\n".join(f"- {item}" for item in changes_breakdown) or "- לא צוינו שינויים."
    if score_line:
        score_block = score_line.strip()
    elif estimated_ats_score is not None:
        score_block = f"**ציון משוער: {estimated_ats_score}/100**"
    else:
        score_block = "**ציון משוער:** לא צוין"
    if score_notes:
        score_block += "\n\n" + "\n".join(f"- {note}" for note in score_notes)

    return (
        "## פירוט שינויים\n"
        f"{change_lines}\n\n"
        "## ציון התאמה למשרה\n"
        f"{score_block}\n\n"
        "---\n\n"
        "## קורות החיים המעודכנים\n\n"
        f"{cv_markdown.strip()}\n"
    )


# --------------------------------------------------------------------------- #
# Structured tailored_cv -> resume Markdown
# --------------------------------------------------------------------------- #


def build_resume_header(
    cv_profile: dict[str, Any], job: dict[str, Any]
) -> tuple[str, str, str]:
    """Return (name, contact_line, target_role) from verified profile facts.

    The model never supplies header facts, so contact details and the candidate's
    name cannot be invented — they are copied from the parsed profile.
    """
    contact = cv_profile.get("contact")
    contact = contact if isinstance(contact, dict) else {}
    name = str(contact.get("name") or "").strip()
    parts = [str(contact.get(field) or "").strip() for field in CONTACT_FIELD_ORDER]
    contact_line = " | ".join(part for part in parts if part)
    target_role = str(job.get("title") or "").strip()
    return name, contact_line, target_role


def _skill_rows(skills: list[str]) -> list[str]:
    """Split skills into "Category: a, b" rows plus one row of ungrouped skills."""
    grouped = [s for s in skills if ":" in s and s.split(":", 1)[1].strip()]
    plain = [s for s in skills if s not in grouped]
    rows = list(grouped)
    if plain:
        rows.append(", ".join(plain))
    return rows


def _entry_meta_line(*values: str) -> str:
    return " | ".join(v for v in (value.strip() for value in values) if v)


def render_tailored_cv_markdown(
    tailored_cv: dict[str, Any],
    *,
    name: str = "",
    contact_line: str = "",
    target_role: str = "",
) -> str:
    """Render the structured tailored CV into resume Markdown.

    The shape is what ``pdf_generator_service.parse_resume_markdown`` expects:
    ``#`` name, a contact line, ``##`` section headings, ``###`` entry titles with
    a ``Company | Dates`` meta line, and ``Category: a, b`` skill rows.
    """
    cv = tailored_cv if isinstance(tailored_cv, dict) else {}
    lines: list[str] = []

    if name:
        lines.append(f"# {name}")
    if contact_line:
        lines += ["", contact_line]
    professional_title = str(cv.get("professional_title") or "").strip()
    # Honest headline for how the candidate presents (may differ from the JD
    # Target Role when hard coverage is too weak to claim that role directly).
    if professional_title and professional_title.lower() != (target_role or "").lower():
        lines += ["", professional_title]
    if target_role:
        lines += ["", f"Target Role: {target_role}"]

    summary = str(
        cv.get("professional_summary") or cv.get("summary") or ""
    ).strip()
    if summary:
        lines += ["", "## Professional Summary", "", summary]

    experience = [e for e in cv.get("experience") or [] if isinstance(e, dict)]
    if experience:
        lines += ["", "## Experience"]
        for entry in experience:
            title = str(entry.get("title") or "").strip()
            company = str(entry.get("company") or "").strip()
            dates = str(entry.get("dates") or "").strip()
            heading = title or company
            if not heading and not entry.get("bullets"):
                continue
            lines += ["", f"### {heading or 'Experience'}"]
            meta = _entry_meta_line(company if title else "", dates)
            if meta:
                lines += ["", meta]
            bullets = _string_list(entry.get("bullets"), max_items=8)
            if bullets:
                lines.append("")
                lines += [f"- {bullet}" for bullet in bullets]

    projects = [p for p in cv.get("projects") or [] if isinstance(p, dict)]
    if projects:
        lines += ["", "## Projects"]
        for entry in projects:
            project_name = str(entry.get("name") or "").strip()
            description = str(entry.get("description") or "").strip()
            if not project_name and not description and not entry.get("bullets"):
                continue
            lines += ["", f"### {project_name or 'Project'}"]
            if description:
                lines += ["", description]
            bullets = _string_list(entry.get("bullets"), max_items=8)
            if bullets:
                lines.append("")
                lines += [f"- {bullet}" for bullet in bullets]
            techs = _string_list(entry.get("technologies"), max_items=20)
            if techs:
                lines += ["", f"Technologies: {', '.join(techs)}"]

    skills = _string_list(cv.get("skills"), max_items=40)
    if skills:
        lines += ["", "## Skills", ""]
        lines += _skill_rows(skills)

    education = [e for e in cv.get("education") or [] if isinstance(e, dict)]
    if education:
        lines += ["", "## Education"]
        for entry in education:
            degree = str(entry.get("degree") or "").strip()
            institution = str(entry.get("institution") or "").strip()
            dates = str(entry.get("dates") or "").strip()
            heading = degree or institution
            if not heading:
                continue
            lines += ["", f"### {heading}"]
            meta = _entry_meta_line(institution if degree else "", dates)
            if meta:
                lines += ["", meta]

    certifications = list(cv.get("certifications") or [])
    if certifications:
        lines += ["", "## Certifications", ""]
        for cert in certifications:
            text = (
                str(cert.get("name") if isinstance(cert, dict) else cert).strip()
            )
            if text:
                lines.append(f"- {text}")

    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------- #
# Report -> document / API payload
# --------------------------------------------------------------------------- #

_SCORE_LABEL_HE = {
    "Excellent Match": "התאמה מצוינת",
    "Good Match": "התאמה טובה",
    "Partial Match": "התאמה חלקית",
    "Potential Match": "התאמה פוטנציאלית",
    "Weak Match": "התאמה חלשה",
    "Baseline": "ציון בסיס",
}

RECOMMENDATION_HE = {
    "STRONG_APPLY": "מומלץ להגיש — התאמה חזקה",
    "APPLY_WITH_HONEST_FRAMING": "כדאי להגיש עם מיסגור כנה של הפערים",
    "STRETCH_APPLY_LOW_ODDS": "הגשה אופטימית — סיכויים נמוכים",
    "DO_NOT_RECOMMEND": "לא מומלץ להגיש למשרה הזו",
}


def _hebrew_score_label(label: str | None) -> str | None:
    if not label:
        return None
    text = str(label).strip()
    if not text or text.lower() == "baseline":
        return None
    return _SCORE_LABEL_HE.get(text, text)


def _score_line_for_display(
    *,
    score: int,
    label: str | None,
    score_before: int | None = None,
    initial_match_score: int | None = None,
) -> str:
    """Human Hebrew score summary (the server is the only source of the number)."""
    he_label = _hebrew_score_label(label)
    label_suffix = f" — {he_label}" if he_label else ""
    before = score_before if score_before is not None else initial_match_score
    if before is not None and before < score:
        return f"**שיפרנו את ההתאמה למשרה מ־{before} ל־{score}{label_suffix}**"
    return f"**ציון ההתאמה למשרה: {score}{label_suffix}**"


def report_match_score(report: dict[str, Any]) -> int:
    """The one score the whole app shows for a tailored job."""
    return int((report.get("scoring") or {}).get("realistic_match_score") or 0)


def _gap_skill_labels(entries: list[Any] | None) -> list[str]:
    """Strip ``skill — reason`` gap entries down to skill names for UI/DB lists."""
    labels: list[str] = []
    for item in entries or []:
        if isinstance(item, dict):
            label = str(
                item.get("skill") or item.get("name") or item.get("requirement") or ""
            ).strip()
        else:
            label = str(item).split(" — ", 1)[0].strip() or str(item)
        if label and label not in labels:
            labels.append(label)
    return labels


def matcher_feedback_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Project the honest report onto the feedback snapshot the UI already reads."""
    scoring = report.get("scoring") or {}
    validation = report.get("score_validation") or {}
    score = report_match_score(report)
    missing = _gap_skill_labels(report.get("missing_critical_skills"))
    rationale = str(scoring.get("score_rationale") or "").strip()
    return {
        "match_score": score,
        "ats_score": score,
        "score_label": score_label_for(score),
        "matched_required_skills": list(report.get("key_matching_points") or []),
        "missing_required_skills": missing,
        "missing_keywords": missing,
        "missing_mandatory_requirements": [
            item["requirements"][0]
            for item in validation.get("unmet_core_requirements") or []
            if item.get("requirements")
        ],
        "cv_improvements": [
            f"{item.get('gap')}: {item.get('how_to_honestly_frame_existing_experience')}"
            for item in report.get("transferable_skills_framing") or []
            if item.get("gap")
        ],
        "score_reasons": [rationale] if rationale else [],
        "component_scores": {
            "hard_requirements": scoring.get("hard_score_pct"),
            "soft_requirements": scoring.get("soft_score_pct"),
        },
        "mandatory_failed": bool(scoring.get("hard_cap_applied")),
        "profile_match_score": scoring.get("hard_score_pct"),
    }


def _document_changes(report: dict[str, Any]) -> list[str]:
    """Short Hebrew UI bullets for the markdown preamble — NOT raw LLM text.

    Full structured change_log is returned separately in the API for the review panel.
    """
    structured: list[str] = []
    for item in report.get("change_log") or []:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section") or "").strip()
        change_type = str(item.get("change_type") or "").strip()
        reason = str(item.get("reason") or "").strip()
        new_text = str(item.get("new_text") or "").strip()
        # Keep preamble concise — one line per change, truncated
        label = section or change_type or "שינוי"
        snippet = (new_text or reason)[:120]
        if snippet:
            structured.append(f"{label}: {snippet}")
        if len(structured) >= 8:
            break
    if structured:
        return structured

    extraction = report.get("requirement_extraction") or {}
    hard = extraction.get("hard_requirements") or []
    soft = extraction.get("soft_requirements") or []
    return [
        f"נותחו {len(hard)} דרישות חובה ו-{len(soft)} דרישות מועדפות מתוך תיאור המשרה.",
    ]


def _document_caveats(report: dict[str, Any]) -> list[str]:
    caveats: list[str] = []
    for skill in _gap_skill_labels(report.get("missing_critical_skills"))[:8]:
        caveats.append(f"לא נטען ניסיון ב-{skill} — הפער נשאר גלוי")
    dropped = (report.get("score_validation") or {}).get(
        "dropped_unsupported_skills"
    ) or []
    if dropped:
        caveats.append(
            "הוסרו כישורים שלא נמצא להם ביסוס בקורות החיים המקוריים: "
            + ", ".join(str(item) for item in dropped[:6])
        )
    return caveats


def build_tailor_document(
    report: dict[str, Any],
    *,
    cv_profile: dict[str, Any],
    job: dict[str, Any],
    score_before: int | None = None,
    initial_match_score: int | None = None,
) -> dict[str, Any]:
    """Turn an evaluation report into the saved/displayed tailored-CV document."""
    name, contact_line, target_role = build_resume_header(cv_profile, job)
    cv_markdown = render_tailored_cv_markdown(
        report.get("tailored_cv") or {},
        name=name,
        contact_line=contact_line,
        target_role=target_role,
    )
    if not cv_markdown.strip():
        raise TailorCvError("המנוע החזיר קורות חיים ריקים", status_code=502)

    score = report_match_score(report)
    scoring = report.get("scoring") or {}
    changes = _document_changes(report)
    score_notes: list[str] = []
    rationale = str(scoring.get("score_rationale") or "").strip()
    if rationale:
        score_notes.append(rationale)
    recommendation_he = RECOMMENDATION_HE.get(str(report.get("recommendation") or ""))
    if recommendation_he:
        score_notes.append(recommendation_he)

    markdown = _assemble_structured_markdown(
        changes_breakdown=changes,
        estimated_ats_score=score,
        cv_markdown=cv_markdown,
        score_line=_score_line_for_display(
            score=score,
            label=score_label_for(score),
            score_before=score_before,
            initial_match_score=initial_match_score,
        ),
        score_notes=score_notes,
    )

    honesty_note = (
        "לא נוספה שום חוויה או טכנולוגיה שאינה מגובה בקורות החיים המקוריים "
        "(רק Explicit / Strongly Inferred עברו את בודק הטענות)."
    )
    caveats = _document_caveats(report)
    if honesty_note not in caveats:
        caveats.insert(0, honesty_note)

    return {
        "markdown": markdown.strip(),
        "cv_markdown": cv_markdown.strip(),
        "changes_breakdown": changes,
        "estimated_ats_score": score,
        "highlights": _string_list(report.get("key_matching_points")),
        "caveats": caveats,
        # Structured evaluation carried through to the API for transparency.
        "realistic_match_score": score,
        "requirement_extraction": report.get("requirement_extraction"),
        "key_matching_points": list(report.get("key_matching_points") or []),
        "missing_critical_skills": list(report.get("missing_critical_skills") or []),
        "transferable_skills_framing": list(
            report.get("transferable_skills_framing") or []
        ),
        "score_validation": report.get("score_validation"),
        "recommendation": report.get("recommendation"),
        "tailored_cv": report.get("tailored_cv"),
        # Intelligent Resume Tailoring structured report fields
        "tailored_resume": report.get("tailored_resume") or report.get("tailored_cv"),
        "matched_requirements": list(report.get("matched_requirements") or []),
        "missing_requirements": list(report.get("missing_requirements") or []),
        "inferred_competencies": list(report.get("inferred_competencies") or []),
        "removed_or_deprioritized_content": list(
            report.get("removed_or_deprioritized_content") or []
        ),
        "ats_keywords_added": list(report.get("ats_keywords_added") or []),
        "change_log": list(report.get("change_log") or []),
        "validation_warnings": list(report.get("validation_warnings") or []),
        "original_match_score": report.get("original_match_score"),
        "tailored_match_score": report.get("tailored_match_score") or score,
        "evidence_map": list(report.get("evidence_map") or []),
        "language": report.get("language"),
        "claim_validator_passed": bool(report.get("claim_validator_passed", True)),
        "jd_snapshot_hash": report.get("jd_snapshot_hash"),
        "resume_hash": report.get("resume_hash"),
        "pipeline_version": report.get("pipeline_version") or TAILOR_PIPELINE_VERSION,
        "quality_gates": report.get("quality_gates") or {},
        "quality_report": report.get("quality_report") or {},
        "extraction_coverage": report.get("extraction_coverage") or {},
        "tailoring_report": report.get("tailoring_report") or {},
        "rejected_statements": list(report.get("rejected_statements") or []),
    }


# --------------------------------------------------------------------------- #
# Profile / source-document loading
# --------------------------------------------------------------------------- #


def _cv_source_payload(cv_profile: dict[str, Any]) -> str:
    """Compact factual view of the parsed profile (structured sections)."""
    parts: list[str] = []
    raw = (cv_profile.get("raw_text") or "").strip()
    if raw:
        parts.append("=== RAW CV TEXT ===")
        parts.append(truncate_text(raw, OPENAI_CV_MAX_CHARS))

    contact = cv_profile.get("contact") or {}
    if isinstance(contact, dict) and any(contact.values()):
        parts.append("=== CONTACT ===")
        parts.append(json.dumps(contact, ensure_ascii=False, indent=2))

    for key in (
        "experience",
        "education",
        "skills",
        "projects",
        "certifications",
        "best_fit_roles",
        "universal_profile",
        "sections",
    ):
        value = cv_profile.get(key)
        if value:
            parts.append(f"=== {key.upper()} ===")
            parts.append(json.dumps(value, ensure_ascii=False, indent=2)[:8000])

    return "\n\n".join(parts)


def _load_source_cv_raw_text(cv_id: str) -> str:
    """Load raw text from a single uploaded CV's parsed profile."""
    path = cv_data_dir(cv_id) / "cv_profile.json"
    if not path.exists():
        return ""
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    raw = str(profile.get("raw_text") or "").strip()
    if raw:
        return raw
    sections = profile.get("sections")
    if isinstance(sections, dict):
        parts = [str(v).strip() for v in sections.values() if v]
        return "\n\n".join(p for p in parts if p)
    return ""


def gather_original_source_cvs(
    cv_id: str,
    *,
    user_id: str | None = None,
    cv_profile: dict[str, Any] | None = None,
) -> str:
    """Gather ALL original uploaded CV texts + compiled Master Profile.

    Tailoring works from full history so a skill that only ever appears in one
    uploaded file (or in an experience bullet) can still be surfaced honestly.
    """
    import db as db_mod

    profile = cv_profile or {}
    blocks: list[str] = []
    seen_fingerprints: set[str] = set()
    per_source_budget = max(4000, OPENAI_CV_MAX_CHARS // 3)

    def _append_block(title: str, body: str) -> None:
        text = (body or "").strip()
        if not text:
            return
        fingerprint = text[:500].lower()
        if fingerprint in seen_fingerprints:
            return
        seen_fingerprints.add(fingerprint)
        blocks.append(
            f"----- {title} -----\n{truncate_text(text, per_source_budget)}"
        )

    effective_user = user_id
    if not effective_user and cv_id == WORKSPACE_CV_ID:
        effective_user = AGENT_USER_ID or DEFAULT_USER_ID

    source_cvs: list[dict[str, Any]] = []
    if effective_user:
        try:
            source_cvs = db_mod.list_active_cvs_for_user(
                effective_user, db_path=db_mod.REGISTRY_DB_PATH
            )
        except Exception:  # noqa: BLE001 — fall back to profile-only
            source_cvs = []

    if source_cvs:
        for index, cv in enumerate(source_cvs, start=1):
            sid = str(cv.get("id") or "")
            label = cv.get("display_name") or cv.get("file_name") or sid or f"cv_{index}"
            raw = _load_source_cv_raw_text(sid) if sid else ""
            _append_block(f"ORIGINAL SOURCE CV #{index}: {label}", raw)
    elif cv_id and cv_id != WORKSPACE_CV_ID:
        raw = _load_source_cv_raw_text(cv_id)
        _append_block(f"ORIGINAL SOURCE CV: {cv_id}", raw)

    master = profile.get("master_profile")
    if master:
        _append_block(
            "COMPILED MASTER PROFILE",
            json.dumps(master, ensure_ascii=False, indent=2),
        )

    profile_raw = str(profile.get("raw_text") or "").strip()
    if profile_raw:
        _append_block("COMPILED PROFILE RAW TEXT", profile_raw)

    structured = _cv_source_payload(profile)
    if structured.strip():
        _append_block("COMPILED STRUCTURED PROFILE (base_cv_data)", structured)

    if not blocks:
        return "(no original source CV text available)"

    combined = "\n\n".join(blocks)
    return truncate_text(combined, OPENAI_CV_MAX_CHARS * 2)


def _profile_path_for(cv_id: str, user_id: str | None = None) -> Path:
    if cv_id == WORKSPACE_CV_ID or user_id:
        return user_cv_profile_path(user_id or AGENT_USER_ID or DEFAULT_USER_ID)
    from profile_utils import cv_profile_path_for

    return cv_profile_path_for(cv_id)


def _load_cv_profile_or_raise(cv_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    path = _profile_path_for(cv_id, user_id=user_id)
    if path.exists():
        try:
            cv_profile = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cv_profile = {}
    else:
        from profile_utils import load_cv_profile

        cv_profile = load_cv_profile(cv_id)
    if not cv_profile or not (
        cv_profile.get("raw_text")
        or cv_profile.get("experience")
        or cv_profile.get("skills")
        or cv_profile.get("master_profile")
    ):
        raise TailorCvError(
            "Parsed CV profile not found — run the agent / parse CV first",
            status_code=404,
        )
    return cv_profile


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _read_saved_draft(cv_id: str, job_id: int) -> tuple[str | None, str | None]:
    """Return (markdown, pipeline_version) for a saved draft, marker stripped."""
    path = tailored_cv_path(cv_id, job_id)
    if not path.exists():
        return None, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    marker = _PIPELINE_MARKER_RE.match(text)
    version = marker.group(1) if marker else None
    if marker:
        text = text[marker.end() :]
    text = text.strip()
    return (text or None), version


def assert_safe_to_export(report: dict[str, Any] | None) -> None:
    """Block PDF/DOCX/markdown export when hard quality gates failed."""
    from intelligent_tailoring.quality_gates import should_block_export

    report = report or {}
    gates = report.get("quality_gates")
    if gates is None:
        # Legacy drafts without gate metadata — allow, but require claim flag if present
        if report.get("claim_validator_passed") is False:
            raise TailorCvError(
                "לא ניתן לייצא — בודק הטענות נכשל. יש לייצר מחדש.",
                status_code=422,
            )
        return
    if should_block_export(gates):
        hard = [
            f
            for f in (gates.get("failures") or [])
            if any(
                str(f).startswith(p)
                for p in (
                    "unsupported_impact",
                    "unsupported_entity",
                    "cross_entry_tech",
                    "unknown_skill",
                    "missing_professional_summary",
                    "raw_llm_reasoning",
                    "linguistic_integrity",
                    "writing_quality:facts_changed",
                    "writing_quality:grammar:",
                    "writing_quality:ats:",
                    "page_count:",
                )
            )
        ]
        if hard:
            raise TailorCvError(
                "לא ניתן לייצא — שערי איכות נכשלו: " + "; ".join(hard[:5]),
                status_code=422,
            )


def load_saved_tailored_cv(cv_id: str, job_id: int) -> str | None:
    """The saved tailored document, without the internal pipeline marker."""
    markdown, _version = _read_saved_draft(cv_id, job_id)
    return markdown


def saved_draft_is_current(cv_id: str, job_id: int) -> bool:
    """True when a saved draft came from the current tailoring pipeline."""
    markdown, version = _read_saved_draft(cv_id, job_id)
    return bool(markdown) and version == TAILOR_PIPELINE_VERSION


def save_tailored_cv(cv_id: str, job_id: int, markdown: str) -> Path:
    directory = tailored_cv_dir(cv_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = tailored_cv_path(cv_id, job_id)
    path.write_text(
        f"<!-- tailor-pipeline: {TAILOR_PIPELINE_VERSION} -->\n"
        + markdown.strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _result_from_saved_markdown(markdown: str, *, saved_path: str) -> dict[str, Any]:
    _, cv_body = split_tailored_markdown(markdown)
    return {
        "markdown": markdown,
        "cv_markdown": cv_body or markdown,
        "changes_breakdown": [],
        "estimated_ats_score": _parse_score_from_markdown(markdown),
        "highlights": [],
        "caveats": [],
        "from_cache": True,
        "saved_path": saved_path,
    }


def _attach_score_metadata(
    result: dict[str, Any],
    *,
    initial_match_score: int | None,
    score_before: int | None,
    score_after: int | None,
    version_id: int | None = None,
    matcher_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = {
        **result,
        "initial_match_score": initial_match_score,
        "score_before": score_before,
        "score_after": score_after,
        "version_id": version_id,
    }
    if matcher_feedback is not None:
        enriched["matcher_feedback"] = matcher_feedback
    if score_after is not None:
        enriched["estimated_ats_score"] = score_after
    return enriched


def _split_preamble_bullets(preamble: str) -> tuple[list[str], list[str]]:
    """Split a saved preamble back into (change bullets, score notes)."""
    changes: list[str] = []
    notes: list[str] = []
    target = changes
    for line in preamble.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            target = notes if "ציון" in stripped else changes
            continue
        if stripped.startswith("- "):
            target.append(stripped[2:].strip())
    return changes, notes


def _enrich_cached_result_with_db_scores(
    result: dict[str, Any],
    *,
    cv_id: str,
    job_id: int,
    db_path: Path | None,
) -> dict[str, Any]:
    """Replay stored score history onto a draft loaded from disk."""
    if db_path is None:
        return result
    latest = get_latest_cv_tailor_version(cv_id, job_id, db_path=db_path)
    score_after = (
        latest.get("score_after") if latest else result.get("estimated_ats_score")
    )
    if score_after is None:
        score_after = _parse_score_from_markdown(result.get("markdown") or "")
    score_before = latest.get("score_before") if latest else score_after

    if score_after is not None:
        score_line = _score_line_for_display(
            score=int(score_after),
            label=score_label_for(int(score_after)),
            score_before=_clamp_score(score_before),
        )
        preamble, _ = split_tailored_markdown(result.get("markdown") or "")
        saved_changes, score_notes = _split_preamble_bullets(preamble)
        changes = list(result.get("changes_breakdown") or []) or saved_changes
        cv_markdown = result.get("cv_markdown") or extract_cv_markdown_for_copy(
            result.get("markdown") or ""
        )
        result = {
            **result,
            "markdown": _assemble_structured_markdown(
                changes_breakdown=changes,
                estimated_ats_score=int(score_after),
                cv_markdown=cv_markdown,
                score_line=score_line,
                score_notes=score_notes,
            ).strip(),
            "changes_breakdown": changes,
            "cv_markdown": cv_markdown,
            "estimated_ats_score": int(score_after),
        }

    return _attach_score_metadata(
        result,
        initial_match_score=_clamp_score(score_before),
        score_before=_clamp_score(score_before),
        score_after=_clamp_score(score_after),
        version_id=latest.get("id") if latest else None,
    )


def _match_scope_id(cv_id: str, user_id: str | None) -> str:
    """The cv_id that owns match rows for this request.

    Workspace scans namespace their match rows per user on Postgres, so the
    profile id ("workspace") is not always the row key.
    """
    if cv_id != WORKSPACE_CV_ID:
        return cv_id
    from db import workspace_scope_id

    return workspace_scope_id(user_id or AGENT_USER_ID or DEFAULT_USER_ID)


def _publish_score(
    cv_id: str,
    job_id: int,
    *,
    score: int,
    db_path: Path | None,
    report: dict[str, Any] | None = None,
) -> None:
    """Replace the scan estimate on the job row with the evaluated score.

    Called after every evaluation and also when a saved draft is replayed, so a
    rescan cannot leave the job list quoting a different number than the CV.
    """
    if db_path is None:
        return
    scoring = (report or {}).get("scoring") or {}
    try:
        apply_honest_match_score(
            cv_id,
            job_id,
            match_score=score,
            score_label=score_label_for(score),
            explanation=str(scoring.get("score_rationale") or "").strip() or None,
            matched_skills=(
                list(report.get("key_matching_points") or []) if report else None
            ),
            missing_skills=(
                _gap_skill_labels(report.get("missing_critical_skills"))
                if report
                else None
            ),
            db_path=db_path,
        )
    except Exception:  # noqa: BLE001 — never fail a successful tailor on bookkeeping
        pass


# --------------------------------------------------------------------------- #
# Tailoring entry points
# --------------------------------------------------------------------------- #


def _evaluate(
    cv_id: str,
    job: dict[str, Any],
    *,
    cv_profile: dict[str, Any],
    user_id: str | None,
    use_cache: bool,
    language: str | None = None,
) -> dict[str, Any]:
    """Run Intelligent Resume Tailoring over all of the candidate's sources.

    Always goes through the staged pipeline's claim validator — there is no
    alternate generation path that skips validation.
    """
    sources = gather_original_source_cvs(
        cv_id, user_id=user_id, cv_profile=cv_profile
    )
    try:
        return run_intelligent_tailoring(
            cv_profile=cv_profile,
            job=job,
            use_cache=use_cache,
            source_documents=sources,
            language=language,
        )
    except (IntelligentTailorError, MatchTailorError) as exc:
        message = getattr(exc, "message", str(exc))
        status = getattr(exc, "status_code", 502)
        raise TailorCvError(message, status_code=status) from exc


def evaluate_job_for_cv(
    cv_id: str,
    job: dict[str, Any],
    *,
    user_id: str | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Evaluate one job for one CV with exactly the inputs tailoring uses.

    The match-report endpoints go through here so a report and a tailored CV can
    never quote different scores for the same job.
    """
    cv_profile = _load_cv_profile_or_raise(cv_id, user_id=user_id)
    return _evaluate(
        cv_id,
        job,
        cv_profile=cv_profile,
        user_id=user_id,
        use_cache=use_cache,
    )


def _honest_version_scores(
    cv_id: str, job_id: int, *, db_path: Path | None
) -> tuple[int | None, int | None]:
    """Return (first_honest_score, latest_honest_score) from version history.

    Score progression is only ever honest-score to honest-score. The scan
    estimate is a different scoring system, so mixing it into the progression
    would claim an improvement that tailoring did not make.
    """
    if db_path is None:
        return None, None
    latest = get_latest_cv_tailor_version(cv_id, job_id, db_path=db_path)
    if not latest:
        return None, None
    latest_score = _clamp_score(latest.get("score_after"))
    try:
        history = list_cv_tailor_versions(cv_id, job_id, db_path=db_path)
    except Exception:  # noqa: BLE001 — history is a nicety, not a requirement
        history = []
    first_score = None
    if history:
        oldest = history[-1]
        first_score = _clamp_score(oldest.get("score_before")) or _clamp_score(
            oldest.get("score_after")
        )
    return first_score, latest_score


def _record_version(
    cv_id: str,
    job_id: int,
    *,
    score_before: int | None,
    score_after: int,
    path: Path,
    db_path: Path | None,
    report: dict[str, Any] | None = None,
) -> int | None:
    if db_path is None:
        return None
    try:
        version_id = record_cv_tailor_version(
            cv_id,
            job_id,
            score_before=int(score_before if score_before is not None else score_after),
            score_after=int(score_after),
            tailored_cv_path=str(path),
            db_path=db_path,
        )
    except Exception:  # noqa: BLE001 — version history must not fail a good tailor
        return None
    if report and version_id is not None:
        try:
            save_tailored_resume_report(
                cv_id=cv_id,
                job_id=job_id,
                report=report,
                version_id=version_id,
                jd_snapshot_text=str(report.get("jd_snapshot") or "") or None,
                db_path=db_path,
            )
        except Exception:  # noqa: BLE001
            pass
    return version_id


def _regenerate_tailored_cv(
    cv_id: str,
    job: dict[str, Any],
    *,
    use_cache: bool = False,
    user_id: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Re-run the evaluation over all source documents and keep the better draft.

    A rewrite cannot invent experience, so the score only moves when a fresh pass
    finds evidence in the original uploads that the previous draft left out. The
    guard keeps the saved draft whenever the new score is not strictly higher.
    """
    job_id = int(job["id"])
    previous = load_saved_tailored_cv(cv_id, job_id)
    if not previous:
        raise TailorCvError(
            "לא נמצא קובץ קורות חיים מותאם לשיפור — יש ליצור גרסה ראשונה קודם",
            status_code=404,
        )

    cv_profile = _load_cv_profile_or_raise(cv_id, user_id=user_id)
    first_score, latest_score = _honest_version_scores(cv_id, job_id, db_path=db_path)
    previous_score = (
        latest_score
        if latest_score is not None
        else _parse_score_from_markdown(previous)
    ) or 0

    report = _evaluate(
        cv_id,
        job,
        cv_profile=cv_profile,
        user_id=user_id,
        use_cache=use_cache,
    )
    new_score = report_match_score(report)
    new_feedback = matcher_feedback_from_report(report)
    previous_feedback = {
        "match_score": previous_score,
        "ats_score": previous_score,
        "score_label": score_label_for(int(previous_score)),
    }
    saved_path = str(tailored_cv_path(cv_id, job_id))

    if new_score <= previous_score:
        preserved = _result_from_saved_markdown(previous, saved_path=saved_path)
        enriched = _attach_score_metadata(
            preserved,
            initial_match_score=first_score if first_score is not None else previous_score,
            score_before=previous_score,
            score_after=previous_score,
            version_id=None,
            matcher_feedback={
                "previous": previous_feedback,
                "current": previous_feedback,
                "discarded": new_feedback,
            },
        )
        return {
            **enriched,
            "from_cache": True,
            "saved_path": saved_path,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "regenerated": False,
            "improved": False,
            "no_improvement": True,
            "message": NO_IMPROVEMENT_MESSAGE,
        }

    document = build_tailor_document(
        report,
        cv_profile=cv_profile,
        job=job,
        score_before=previous_score,
        initial_match_score=first_score,
    )
    path = save_tailored_cv(cv_id, job_id, document["markdown"])
    version_id = _record_version(
        cv_id,
        job_id,
        score_before=previous_score,
        score_after=new_score,
        path=path,
        db_path=db_path,
        report=report,
    )
    _publish_score(
        _match_scope_id(cv_id, user_id),
        job_id,
        score=new_score,
        report=report,
        db_path=db_path,
    )

    return _attach_score_metadata(
        {
            **document,
            "from_cache": bool(report.get("from_cache")),
            "saved_path": str(path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "regenerated": True,
            "improved": True,
            "no_improvement": False,
            "message": None,
            "matcher_feedback": {
                "previous": previous_feedback,
                "current": new_feedback,
            },
        },
        initial_match_score=first_score if first_score is not None else previous_score,
        score_before=previous_score,
        score_after=new_score,
        version_id=version_id,
    )


def tailor_cv_for_job(
    cv_id: str,
    job: dict[str, Any],
    *,
    force: bool = False,
    use_cache: bool = True,
    regenerate: bool = False,
    user_id: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Generate (or load) the tailored CV for one job.

    Production path: ``run_intelligent_tailoring`` via ``_evaluate``.
    Drafts saved by an older pipeline are regenerated rather than replayed.
    ``force=True`` always bypasses disk AND LLM caches.
    """
    if regenerate:
        return _regenerate_tailored_cv(
            cv_id, job, use_cache=False, user_id=user_id, db_path=db_path
        )

    job_id = int(job["id"])

    # Force regenerate must never reuse caches.
    effective_cache = bool(use_cache) and not force

    if not force and saved_draft_is_current(cv_id, job_id):
        cached = load_saved_tailored_cv(cv_id, job_id)
        if cached:
            result = _enrich_cached_result_with_db_scores(
                _result_from_saved_markdown(
                    cached, saved_path=str(tailored_cv_path(cv_id, job_id))
                ),
                cv_id=cv_id,
                job_id=job_id,
                db_path=db_path,
            )
            if result.get("score_after") is not None:
                _publish_score(
                    _match_scope_id(cv_id, user_id),
                    job_id,
                    score=int(result["score_after"]),
                    db_path=db_path,
                )
            return result

    cv_profile = _load_cv_profile_or_raise(cv_id, user_id=user_id)
    first_score, latest_score = _honest_version_scores(cv_id, job_id, db_path=db_path)

    report = _evaluate(
        cv_id,
        job,
        cv_profile=cv_profile,
        user_id=user_id,
        use_cache=effective_cache,
    )
    score = report_match_score(report)
    score_before = latest_score if latest_score is not None else score
    initial = first_score if first_score is not None else score

    document = build_tailor_document(
        report,
        cv_profile=cv_profile,
        job=job,
        score_before=score_before,
        initial_match_score=initial,
    )
    assert_safe_to_export({**report, **document})
    path = save_tailored_cv(cv_id, job_id, document["markdown"])
    version_id = _record_version(
        cv_id,
        job_id,
        score_before=score_before,
        score_after=score,
        path=path,
        db_path=db_path,
        report=report,
    )
    _publish_score(
        _match_scope_id(cv_id, user_id),
        job_id,
        score=score,
        report=report,
        db_path=db_path,
    )

    feedback = matcher_feedback_from_report(report)
    return _attach_score_metadata(
        {
            **document,
            "from_cache": bool(report.get("from_cache")),
            "saved_path": str(path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "regenerated": False,
            "matcher_feedback": {
                "previous": {
                    "match_score": score_before,
                    "ats_score": score_before,
                    "score_label": score_label_for(int(score_before)),
                },
                "current": feedback,
            },
        },
        initial_match_score=initial,
        score_before=score_before,
        score_after=score,
        version_id=version_id,
    )
