"""On-demand ATS-optimized CV tailoring via OpenAI (zero hallucination)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_client import (
    OpenAIAPIError,
    call_openai_json,
    is_ai_available,
    truncate_text,
)
from ats_candidate import AtsCandidateProfile, build_ats_candidate
from ats_scorer import AtsMatchResult
from ats_scorer import score as ats_score
from config import (
    AGENT_USER_ID,
    OPENAI_CV_MAX_CHARS,
    OPENAI_JOB_MAX_CHARS,
    OPENAI_TAILOR_MODEL,
    cv_data_dir,
    user_cv_profile_path,
    user_data_dir,
)
from db import (
    DEFAULT_USER_ID,
    WORKSPACE_CV_ID,
    get_latest_cv_tailor_version,
    get_match_baseline_score,
    record_cv_tailor_version,
)
from job_analyzer import JobProfile, parse_stored_job_profile
from match_scoring import compute_final_match_score, score_label_for
from multilingual_normalizer import expand_synonyms, to_canonical
from profile_matcher import score as profile_match_score
from resume_generator_prompt import (
    REGENERATE_PROMPT_VERSION,
    REGENERATE_SYSTEM_PROMPT,
    REGENERATE_TEMPERATURE,
    TAILOR_PROMPT_VERSION,
    TAILOR_SYSTEM_PROMPT,
    TAILOR_TEMPERATURE,
    build_tailor_user_prompt as _build_tailor_user_prompt,
)
from skill_normalizer import normalize_skill

NO_IMPROVEMENT_MESSAGE = "לא הצלחתי לייצר גרסה יותר טובה"

# Re-export prompt contract for tests / callers that import from this module.
__all_prompt_exports__ = (
    "TAILOR_PROMPT_VERSION",
    "REGENERATE_PROMPT_VERSION",
    "TAILOR_SYSTEM_PROMPT",
    "REGENERATE_SYSTEM_PROMPT",
    "TAILOR_TEMPERATURE",
    "REGENERATE_TEMPERATURE",
)

HR_SPLIT_RE = re.compile(r"\n---\s*\n", re.MULTILINE)
CV_SECTION_HEADING_RE = re.compile(
    r"^##\s*(?:קורות החיים המעודכנים|The Tailored CV|Tailored CV)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
SCORE_IN_TEXT_RE = re.compile(
    r"(?:ציון(?:\s+משוער)?|score|ATS)[^\d]{0,40}?(\d{1,3})\s*/\s*100",
    re.IGNORECASE,
)


class TailorCvError(RuntimeError):
    """Raised when CV tailoring cannot be completed."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def tailored_cv_dir(cv_id: str) -> Path:
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
    match = SCORE_IN_TEXT_RE.search(markdown or "")
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
) -> str:
    change_lines = "\n".join(f"- {item}" for item in changes_breakdown) or "- לא צוינו שינויים."
    if score_line:
        score_block = score_line.strip()
    elif estimated_ats_score is not None:
        score_block = f"**ציון משוער: {estimated_ats_score}/100**"
    else:
        score_block = "**ציון משוער:** לא צוין"

    return (
        "## פירוט שינויים\n"
        f"{change_lines}\n\n"
        "## ציון התאמה למשרה\n"
        f"{score_block}\n\n"
        "---\n\n"
        "## קורות החיים המעודכנים\n\n"
        f"{cv_markdown.strip()}\n"
    )


def _cv_source_payload(cv_profile: dict[str, Any]) -> str:
    """Build compact factual `base_cv_data` for the tailor user prompt."""
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


def _job_prompt_payload(job: dict[str, Any], job_profile: JobProfile | None) -> str:
    """Build compact `job_description` payload for the tailor user prompt."""
    description = job.get("full_description") or job.get("description") or ""
    parts = [
        f"Title: {job.get('title') or ''}",
        f"Company: {job.get('company') or ''}",
        f"Location: {job.get('location') or ''}",
        f"Source: {job.get('source') or ''}",
        "Description:",
        truncate_text(description, OPENAI_JOB_MAX_CHARS),
    ]
    if job_profile is not None:
        parts.append("Structured JobProfile JSON:")
        parts.append(json.dumps(job_profile.to_dict(), ensure_ascii=False, indent=2))
    return "\n".join(parts)


def build_tailor_user_prompt(
    *,
    base_cv_data: str,
    job_description: str,
    current_score: int | None = None,
) -> str:
    """Assemble the user message that supplies base_cv_data + job_description."""
    return _build_tailor_user_prompt(
        base_cv_data=base_cv_data,
        job_description=job_description,
        current_score=current_score,
    )


def _skill_appears_in_text(skill: str, text: str) -> bool:
    """True when a skill (or known synonym) appears in free text."""
    haystack = (text or "").lower()
    if not skill or not haystack:
        return False
    candidates: set[str] = {skill.strip().lower()}
    canon = to_canonical(skill) or normalize_skill(skill)
    if canon:
        candidates.add(canon.lower())
        candidates.update(v.lower() for v in expand_synonyms(canon) if v)
        candidates.update(v.lower() for v in expand_synonyms(skill) if v)
    for term in candidates:
        cleaned = re.sub(r"\s+", " ", term).strip()
        if len(cleaned) >= 2 and cleaned in haystack:
            return True
    return False


def _job_skill_universe(job_profile: JobProfile | None) -> list[str]:
    if job_profile is None:
        return []
    items: list[str] = []
    for bucket in (
        job_profile.required_skills,
        job_profile.preferred_skills,
        job_profile.technologies,
    ):
        for skill in bucket or []:
            text = str(skill).strip()
            if text and text not in items:
                items.append(text)
    return items


def build_draft_ats_candidate(
    cv_profile: dict[str, Any],
    draft_markdown: str,
    job_profile: JobProfile | None,
) -> AtsCandidateProfile:
    """Build an ATS candidate that reflects skills present in the tailored draft."""
    base = build_ats_candidate(cv_profile)
    draft = draft_markdown or ""

    check_skills = list(_job_skill_universe(job_profile))
    for skill in list(base.skills) + list(base.technologies) + list(base.languages):
        if skill not in check_skills:
            check_skills.append(skill)

    found: set[str] = set()
    for skill in check_skills:
        if _skill_appears_in_text(skill, draft):
            canon = normalize_skill(skill, domain=base.domain)
            if canon:
                found.add(canon)

    # Keep language/cert facts from the base profile (hard attributes).
    found |= set(base.languages) | set(base.certifications)

    draft_l = draft.lower()
    projects = [
        p for p in base.projects if p and str(p).lower() in draft_l
    ] or list(base.projects)

    return AtsCandidateProfile(
        skills=sorted(found),
        technologies=sorted(
            {
                normalize_skill(t, domain=base.domain)
                for t in base.technologies
                if t and _skill_appears_in_text(t, draft)
            }
            - {""}
        ),
        experience_years=base.experience_years,
        previous_roles=list(base.previous_roles),
        projects=projects,
        education=list(base.education),
        languages=list(base.languages),
        certifications=list(base.certifications),
        seniority=base.seniority,
        domain=base.domain,
        core_professional_domain=base.core_professional_domain,
        domain_keywords=list(base.domain_keywords),
    )


def evaluate_tailored_draft(
    *,
    cv_profile: dict[str, Any],
    draft_markdown: str,
    job: dict[str, Any],
    job_profile: JobProfile | None,
) -> dict[str, Any]:
    """Run deterministic ATS + profile matchers against a tailored draft."""
    body = extract_cv_markdown_for_copy(draft_markdown)
    candidate = build_draft_ats_candidate(cv_profile, body, job_profile)

    empty_job = JobProfile(title=str(job.get("title") or ""))
    effective_job = job_profile or empty_job
    ats_result: AtsMatchResult = ats_score(candidate, effective_job, job)

    universal = dict(cv_profile.get("universal_profile") or {})
    # Reflect draft skill coverage in the universal profile used by profile_matcher.
    draft_skills = sorted(candidate.all_skills_set)
    if draft_skills:
        universal["canonical_skills"] = draft_skills
        universal["technologies_tools"] = list(candidate.technologies)

    profile_result = profile_match_score(universal, job, job_profile)

    final_score = compute_final_match_score(
        profile_result.score,
        ats_result,
        profile_exclusion_hit=bool(profile_result.exclusion_hit),
    )
    label = score_label_for(
        final_score,
        is_potential_junior=bool(ats_result.is_potential_junior_match),
    )

    missing_keywords = list(
        dict.fromkeys(
            list(ats_result.missing_required_skills)
            + list(profile_result.missing_skills)
        )
    )

    return {
        "match_score": final_score,
        "ats_score": ats_result.ats_score,
        "score_label": label,
        "matched_required_skills": list(ats_result.matched_required_skills),
        "missing_required_skills": list(ats_result.missing_required_skills),
        "missing_mandatory_requirements": list(
            ats_result.missing_mandatory_requirements
        ),
        "missing_hard_constraints": list(ats_result.missing_hard_constraints),
        "missing_keywords": missing_keywords,
        "cv_improvements": list(ats_result.cv_improvements),
        "score_reasons": list(ats_result.score_reasons),
        "component_scores": dict(ats_result.component_scores),
        "profile_match_score": profile_result.score,
        "profile_missing_skills": list(profile_result.missing_skills),
        "mandatory_failed": bool(ats_result.mandatory_failed),
        "hard_constraint_failed": bool(ats_result.hard_constraint_failed),
        "domain_mismatch": bool(ats_result.domain_mismatch),
        "candidate_domain": ats_result.candidate_domain,
        "target_domain": ats_result.target_domain,
    }


def format_matcher_feedback(feedback: dict[str, Any]) -> str:
    """Human-readable feedback block for the regenerate OpenAI prompt."""
    score = feedback.get("match_score", feedback.get("ats_score"))
    label = feedback.get("score_label") or ""
    missing_kw = feedback.get("missing_keywords") or feedback.get(
        "missing_required_skills"
    ) or []
    missing_mand = feedback.get("missing_mandatory_requirements") or []
    missing_hard = feedback.get("missing_hard_constraints") or []
    improvements = feedback.get("cv_improvements") or []
    reasons = feedback.get("score_reasons") or []
    components = feedback.get("component_scores") or {}
    profile_score = feedback.get("profile_match_score")

    lines = [
        f"The deterministic matcher evaluated this draft at {score}/100 ({label}).",
    ]
    if profile_score is not None:
        lines.append(f"Profile matcher score: {profile_score}/100.")
    if feedback.get("domain_mismatch"):
        lines.append(
            "Domain mismatch detected: "
            f"candidate '{feedback.get('candidate_domain') or '?'}' vs "
            f"target '{feedback.get('target_domain') or '?'}'. "
            "Do not invent domain experience — emphasize transferable skills only."
        )
    if missing_kw:
        lines.append(
            "It is still penalizing the CV for missing these specific keywords/skills: "
            + ", ".join(str(x) for x in missing_kw[:20])
            + "."
        )
    else:
        lines.append("No missing required skill keywords were detected.")
    if missing_hard:
        lines.append(
            "Failed hard constraints (score must stay ≤30 if still unmet): "
            + ", ".join(str(x) for x in missing_hard[:12])
            + "."
        )
    if missing_mand:
        lines.append(
            "Failed / missing mandatory requirements: "
            + ", ".join(str(x) for x in missing_mand[:12])
            + "."
        )
    if components:
        lines.append(
            "Component scores: "
            + ", ".join(f"{k}={v}" for k, v in components.items())
            + "."
        )
    if improvements:
        lines.append("Suggested CV improvements (weak sections):")
        lines.extend(f"- {item}" for item in improvements[:8])
    if reasons:
        lines.append("Matcher reasons:")
        lines.extend(f"- {item}" for item in reasons[:8])
    lines.append(
        "Deep-scan original_source_cvs for evidence of these gaps before deciding "
        "whether to extract omitted facts or only reframe latest_tailored_draft."
    )
    return "\n".join(lines)


def format_ats_feedback_gaps(feedback: dict[str, Any]) -> str:
    """Compact ATS gap block used as the regenerate primary instruction target."""
    missing_kw = feedback.get("missing_keywords") or feedback.get(
        "missing_required_skills"
    ) or []
    missing_mand = feedback.get("missing_mandatory_requirements") or []
    improvements = feedback.get("cv_improvements") or []
    lines = [
        f"Current best match score: {feedback.get('match_score', feedback.get('ats_score'))}/100 "
        f"({feedback.get('score_label') or 'n/a'}).",
        "Missing / weak keywords to recover if evidenced in original_source_cvs:",
    ]
    if missing_kw:
        lines.extend(f"- {item}" for item in missing_kw[:20])
    else:
        lines.append("- (none detected)")
    if missing_mand:
        lines.append("Missing mandatory requirements:")
        lines.extend(f"- {item}" for item in missing_mand[:12])
    if improvements:
        lines.append("Weak sections / improvements:")
        lines.extend(f"- {item}" for item in improvements[:8])
    lines.append("")
    lines.append(format_matcher_feedback(feedback))
    return "\n".join(lines)


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
    """Gather ALL original uploaded CV texts + compiled Master Profile for deep-scan.

    Prefer every source file uploaded by the user; always append the compiled
    master / structured profile so omitted details in the latest draft can be
    recovered from full history.
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

    # Compiled Master Profile / structured facts (always include when present).
    master = profile.get("master_profile")
    if master:
        _append_block(
            "COMPILED MASTER PROFILE",
            json.dumps(master, ensure_ascii=False, indent=2),
        )

    profile_raw = str(profile.get("raw_text") or "").strip()
    if profile_raw:
        _append_block("COMPILED PROFILE RAW TEXT", profile_raw)

    # Structured sections from the active profile (skills/experience/projects).
    structured = _cv_source_payload(profile)
    if structured.strip():
        _append_block("COMPILED STRUCTURED PROFILE (base_cv_data)", structured)

    if not blocks:
        return "(no original source CV text available)"

    combined = "\n\n".join(blocks)
    return truncate_text(combined, OPENAI_CV_MAX_CHARS * 2)


def build_regenerate_user_prompt(
    *,
    original_source_cvs: str,
    latest_tailored_draft: str,
    ats_feedback_gaps: dict[str, Any] | str,
    job_description: str,
    current_score: int | None = None,
    score_before: int | None = None,
    # Backward-compatible aliases used by older call sites / tests.
    base_cv_data: str | None = None,
    previous_tailored_cv: str | None = None,
    matcher_feedback: dict[str, Any] | None = None,
) -> str:
    """User prompt for regenerate & optimize (dual-lookup / three-input mode)."""
    sources = (original_source_cvs or base_cv_data or "").strip()
    draft = (latest_tailored_draft or previous_tailored_cv or "").strip()
    gaps_payload = ats_feedback_gaps if ats_feedback_gaps not in (None, "") else matcher_feedback
    if isinstance(gaps_payload, dict):
        gaps_text = format_ats_feedback_gaps(gaps_payload)
    else:
        gaps_text = str(gaps_payload or "")

    score_context = ""
    if score_before is not None:
        score_context += (
            f"The previous tailored CV version scored {score_before}/100 (score_before). "
            "Use this exact number — do NOT invent a different previous score.\n"
        )
    if current_score is not None:
        score_context += (
            f"The original scan baseline (current_score) was {current_score}/100. "
            "Do NOT contradict this baseline.\n"
        )

    return (
        "REGENERATE & OPTIMIZE the existing tailored CV using the dual-lookup flow.\n"
        f"{score_context}"
        "Primary target: close ats_feedback_gaps by deep-scanning original_source_cvs.\n"
        "If a gap is evidenced in the originals, extract and weave it into the draft.\n"
        "If not evidenced, reframe latest_tailored_draft honestly — never hallucinate.\n"
        "Preserve XYZ bullet depth (15–30 words), **bold** tech keywords, and 3–4 "
        "bullets per role. Never truncate with '...' or placeholder text.\n"
        "Never delete real companies, degrees, or positions from the original sources.\n"
        "Do NOT return previous_score or score_before in JSON — the server sets those.\n"
        "Return the same JSON/markdown structure as a normal tailor response.\n\n"
        "===== ats_feedback_gaps =====\n"
        f"{gaps_text}\n\n"
        "===== latest_tailored_draft =====\n"
        f"{truncate_text(draft, OPENAI_CV_MAX_CHARS)}\n\n"
        "===== original_source_cvs =====\n"
        f"{sources}\n\n"
        "===== job_description =====\n"
        f"{job_description}"
    )


def _normalize_tailor_result(raw: dict[str, Any]) -> dict[str, Any]:
    changes = _string_list(
        raw.get("changes_breakdown") or raw.get("highlights"),
        max_items=12,
    )
    caveats = _string_list(raw.get("caveats"), max_items=12)
    estimated = _clamp_score(raw.get("estimated_ats_score"))

    cv_markdown = str(raw.get("cv_markdown") or "").strip()
    markdown = str(raw.get("markdown") or "").strip()

    if not markdown and cv_markdown:
        markdown = _assemble_structured_markdown(
            changes_breakdown=changes,
            estimated_ats_score=estimated,
            cv_markdown=cv_markdown,
        )
    if not markdown:
        raise TailorCvError("OpenAI returned an empty tailored CV", status_code=502)

    # Prefer an explicit cv_markdown; otherwise peel it off the full document.
    if not cv_markdown:
        _, cv_markdown = split_tailored_markdown(markdown)
    if not cv_markdown:
        cv_markdown = markdown

    # Ensure the saved/displayed document always has the analysis + --- + CV layout
    # when we have structured fields (even if the model omitted the HR rule).
    if changes or estimated is not None:
        if "---" not in markdown or "## פירוט שינויים" not in markdown:
            markdown = _assemble_structured_markdown(
                changes_breakdown=changes,
                estimated_ats_score=estimated,
                cv_markdown=cv_markdown,
            )

    if estimated is None:
        estimated = _parse_score_from_markdown(markdown)

    highlights = _string_list(raw.get("highlights"), max_items=12) or changes[:6]

    return {
        "markdown": markdown.strip(),
        "cv_markdown": cv_markdown.strip(),
        "changes_breakdown": changes,
        "estimated_ats_score": estimated,
        "highlights": highlights,
        "caveats": caveats,
    }


def load_saved_tailored_cv(cv_id: str, job_id: int) -> str | None:
    path = tailored_cv_path(cv_id, job_id)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def save_tailored_cv(cv_id: str, job_id: int, markdown: str) -> Path:
    directory = tailored_cv_dir(cv_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = tailored_cv_path(cv_id, job_id)
    path.write_text(markdown.strip() + "\n", encoding="utf-8")
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


def tailored_cv_dir(cv_id: str) -> Path:
    if cv_id == WORKSPACE_CV_ID and (AGENT_USER_ID or DEFAULT_USER_ID):
        return user_data_dir(AGENT_USER_ID or DEFAULT_USER_ID) / "tailored_cvs"
    return cv_data_dir(cv_id) / "tailored_cvs"


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


def load_cv_profile_for_job(
    cv_id: str, *, user_id: str | None = None
) -> dict[str, Any]:
    """Public accessor for the parsed profile behind a tailor/match request."""
    return _load_cv_profile_or_raise(cv_id, user_id=user_id)


def _feedback_match_score(feedback: dict[str, Any] | None) -> int | None:
    if not feedback:
        return None
    return _clamp_score(feedback.get("match_score", feedback.get("ats_score")))


_SCORE_LABEL_HE = {
    "Excellent Match": "התאמה מצוינת",
    "Good Match": "התאמה טובה",
    "Partial Match": "התאמה חלקית",
    "Potential Match": "התאמה פוטנציאלית",
    "Weak Match": "התאמה חלשה",
    "Baseline": "ציון בסיס",
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
    """Human Hebrew score summary (server overwrites LLM score section)."""
    he_label = _hebrew_score_label(label)
    label_suffix = f" — {he_label}" if he_label else ""
    before = score_before if score_before is not None else initial_match_score
    if before is not None and before < score:
        return f"**שיפרנו את ההתאמה למשרה מ־{before} ל־{score}{label_suffix}**"
    if before is not None and before > score:
        return f"**ציון ההתאמה למשרה אחרי התאמה: {score}{label_suffix}**"
    if before is not None and before == score:
        return f"**ציון ההתאמה למשרה: {score}{label_suffix}**"
    return f"**ציון ההתאמה למשרה: {score}{label_suffix}**"


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


def _enrich_cached_result_with_db_scores(
    result: dict[str, Any],
    *,
    cv_id: str,
    job_id: int,
    db_path: Path | None,
) -> dict[str, Any]:
    if db_path is None:
        return result
    initial = get_match_baseline_score(cv_id, job_id, db_path=db_path)
    latest = get_latest_cv_tailor_version(cv_id, job_id, db_path=db_path)
    score_before = latest.get("score_before") if latest else initial
    score_after = latest.get("score_after") if latest else result.get("estimated_ats_score")
    if score_after is None:
        score_after = _parse_score_from_markdown(result.get("markdown") or "")

    # Refresh the in-document score line so cached drafts get the human wording.
    if score_after is not None:
        label = score_label_for(int(score_after)) if score_after is not None else None
        score_line = _score_line_for_display(
            score=int(score_after),
            label=label,
            score_before=_clamp_score(score_before),
            initial_match_score=_clamp_score(initial),
        )
        changes = list(result.get("changes_breakdown") or [])
        if not changes:
            # Best-effort: keep existing change bullets from the saved markdown.
            preamble, _ = split_tailored_markdown(result.get("markdown") or "")
            for line in preamble.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    changes.append(stripped[2:].strip())
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
            ).strip(),
            "cv_markdown": cv_markdown,
            "estimated_ats_score": int(score_after),
        }

    return _attach_score_metadata(
        result,
        initial_match_score=initial,
        score_before=score_before,
        score_after=score_after,
        version_id=latest.get("id") if latest else None,
    )


def _apply_matcher_score_to_result(
    result: dict[str, Any],
    *,
    feedback: dict[str, Any],
    score_before: int | None = None,
    initial_match_score: int | None = None,
) -> dict[str, Any]:
    """Prefer the deterministic matcher score in the saved/displayed document."""
    score = _feedback_match_score(feedback)
    if score is None:
        return result

    label = feedback.get("score_label") or ""
    score_line = _score_line_for_display(
        score=score,
        label=label,
        score_before=score_before,
        initial_match_score=initial_match_score,
    )

    changes = list(result.get("changes_breakdown") or [])
    cv_markdown = result.get("cv_markdown") or ""
    markdown = _assemble_structured_markdown(
        changes_breakdown=changes,
        estimated_ats_score=score,
        cv_markdown=cv_markdown,
        score_line=score_line,
    )
    return {
        **result,
        "markdown": markdown.strip(),
        "estimated_ats_score": score,
    }


def _regenerate_tailored_cv(
    cv_id: str,
    job: dict[str, Any],
    *,
    use_cache: bool = False,
    user_id: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Improve the best tailored draft via deep-scan of original source CVs.

    Dual-lookup inputs:
    - original_source_cvs (all uploads + master profile)
    - latest_tailored_draft (current highest-scoring saved draft)
    - ats_feedback_gaps (deterministic matcher missing keywords / weak sections)

    Score guard: only overwrite the saved draft when the new match score is
    strictly higher; otherwise roll back with ``no_improvement``.
    """
    job_id = int(job["id"])
    previous = load_saved_tailored_cv(cv_id, job_id)
    if not previous:
        raise TailorCvError(
            "לא נמצא קובץ קורות חיים מותאם לשיפור — יש ליצור גרסה ראשונה קודם",
            status_code=404,
        )

    if not is_ai_available():
        raise TailorCvError(
            "OPENAI_API_KEY is not configured — cannot tailor the CV",
            status_code=503,
        )

    initial_match_score = (
        get_match_baseline_score(cv_id, job_id, db_path=db_path)
        if db_path is not None
        else None
    )
    latest_version = (
        get_latest_cv_tailor_version(cv_id, job_id, db_path=db_path)
        if db_path is not None
        else None
    )

    cv_profile = _load_cv_profile_or_raise(cv_id, user_id=user_id)
    job_profile = parse_stored_job_profile(job.get("job_profile"))

    previous_feedback = evaluate_tailored_draft(
        cv_profile=cv_profile,
        draft_markdown=previous,
        job=job,
        job_profile=job_profile,
    )
    score_before = (
        int(latest_version["score_after"])
        if latest_version and latest_version.get("score_after") is not None
        else _feedback_match_score(previous_feedback)
    )

    original_source_cvs = gather_original_source_cvs(
        cv_id, user_id=user_id, cv_profile=cv_profile
    )
    job_description = _job_prompt_payload(job, job_profile)
    user_prompt = build_regenerate_user_prompt(
        original_source_cvs=original_source_cvs,
        latest_tailored_draft=previous,
        ats_feedback_gaps=previous_feedback,
        job_description=job_description,
        current_score=initial_match_score,
        score_before=score_before,
    )

    try:
        raw = call_openai_json(
            REGENERATE_SYSTEM_PROMPT,
            user_prompt,
            temperature=REGENERATE_TEMPERATURE,
            model=OPENAI_TAILOR_MODEL,
            use_cache=use_cache,
            cache_namespace=(
                f"tailor_cv_regen_{REGENERATE_PROMPT_VERSION}_"
                f"{TAILOR_PROMPT_VERSION}_{cv_id}"
            ),
            cache_payload=(
                f"regen|{REGENERATE_PROMPT_VERSION}|{TAILOR_PROMPT_VERSION}|"
                f"{OPENAI_TAILOR_MODEL}|{cv_id}|{job_id}|"
                f"{previous_feedback.get('match_score')}|"
                f"{','.join((previous_feedback.get('missing_keywords') or [])[:12])}|"
                f"{job_description[:1500]}|{previous[:3000]}|"
                f"{original_source_cvs[:2000]}"
            ),
        )
    except OpenAIAPIError as exc:
        raise TailorCvError(str(exc), status_code=502) from exc

    result = _normalize_tailor_result(raw)

    new_feedback = evaluate_tailored_draft(
        cv_profile=cv_profile,
        draft_markdown=result.get("cv_markdown") or result["markdown"],
        job=job,
        job_profile=job_profile,
    )

    previous_score = int(_feedback_match_score(previous_feedback) or 0)
    new_score = int(_feedback_match_score(new_feedback) or 0)
    saved_path = str(tailored_cv_path(cv_id, job_id))

    # Score guard: never overwrite the saved draft with an equal/worse version.
    if new_score <= previous_score:
        preserved = _result_from_saved_markdown(previous, saved_path=saved_path)
        enriched = _attach_score_metadata(
            preserved,
            initial_match_score=initial_match_score,
            score_before=score_before,
            score_after=previous_score or preserved.get("estimated_ats_score"),
            version_id=latest_version.get("id") if latest_version else None,
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

    result = _apply_matcher_score_to_result(
        result,
        feedback=new_feedback,
        score_before=score_before,
        initial_match_score=initial_match_score,
    )
    path = save_tailored_cv(cv_id, job_id, result["markdown"])
    version_id = None
    if db_path is not None and score_before is not None:
        try:
            version_id = record_cv_tailor_version(
                cv_id,
                job_id,
                score_before=int(score_before),
                score_after=new_score,
                tailored_cv_path=str(path),
                db_path=db_path,
            )
        except Exception:  # noqa: BLE001 — version history must not fail a successful tailor
            version_id = None
    return _attach_score_metadata(
        {
            **result,
            "from_cache": bool(raw.get("_from_cache")),
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
        initial_match_score=initial_match_score,
        score_before=score_before,
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
    """Generate (or load) an ATS-tailored Markdown CV for one job.

    When ``regenerate`` is True, deep-scan original source CVs against ATS gaps
    on the current best tailored draft and only keep a strictly higher score.
    """
    if regenerate:
        return _regenerate_tailored_cv(
            cv_id, job, use_cache=False, user_id=user_id, db_path=db_path
        )

    job_id = int(job["id"])
    initial_match_score = (
        get_match_baseline_score(cv_id, job_id, db_path=db_path)
        if db_path is not None
        else None
    )

    if not force:
        cached = load_saved_tailored_cv(cv_id, job_id)
        if cached:
            result = _result_from_saved_markdown(
                cached, saved_path=str(tailored_cv_path(cv_id, job_id))
            )
            return _enrich_cached_result_with_db_scores(
                result, cv_id=cv_id, job_id=job_id, db_path=db_path
            )

    if not is_ai_available():
        raise TailorCvError(
            "OPENAI_API_KEY is not configured — cannot tailor the CV",
            status_code=503,
        )

    cv_profile = _load_cv_profile_or_raise(cv_id, user_id=user_id)

    job_profile = parse_stored_job_profile(job.get("job_profile"))
    base_cv_data = _cv_source_payload(cv_profile)
    job_description = _job_prompt_payload(job, job_profile)
    user_prompt = build_tailor_user_prompt(
        base_cv_data=base_cv_data,
        job_description=job_description,
        current_score=initial_match_score,
    )

    try:
        raw = call_openai_json(
            TAILOR_SYSTEM_PROMPT,
            user_prompt,
            temperature=TAILOR_TEMPERATURE,
            model=OPENAI_TAILOR_MODEL,
            use_cache=use_cache,
            cache_namespace=f"tailor_cv_{TAILOR_PROMPT_VERSION}_{cv_id}",
            cache_payload=(
                f"{TAILOR_PROMPT_VERSION}|{OPENAI_TAILOR_MODEL}|{cv_id}|{job_id}|"
                f"{initial_match_score}|{job_description[:2000]}|{base_cv_data[:4000]}"
            ),
        )
    except OpenAIAPIError as exc:
        raise TailorCvError(str(exc), status_code=502) from exc

    result = _normalize_tailor_result(raw)
    feedback = evaluate_tailored_draft(
        cv_profile=cv_profile,
        draft_markdown=result.get("cv_markdown") or result["markdown"],
        job=job,
        job_profile=job_profile,
    )
    score_after = _feedback_match_score(feedback)
    score_before = initial_match_score
    result = _apply_matcher_score_to_result(
        result,
        feedback=feedback,
        score_before=score_before,
        initial_match_score=initial_match_score,
    )
    path = save_tailored_cv(cv_id, job_id, result["markdown"])
    version_id = None
    if db_path is not None and score_before is not None and score_after is not None:
        try:
            version_id = record_cv_tailor_version(
                cv_id,
                job_id,
                score_before=int(score_before),
                score_after=int(score_after),
                tailored_cv_path=str(path),
                db_path=db_path,
            )
        except Exception:  # noqa: BLE001 — version history must not fail a successful tailor
            version_id = None
    return _attach_score_metadata(
        {
            **result,
            "from_cache": bool(raw.get("_from_cache")),
            "saved_path": str(path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "regenerated": False,
            "matcher_feedback": {
                "previous": {
                    "match_score": score_before,
                    "ats_score": score_before,
                    "score_label": "Baseline",
                },
                "current": feedback,
            },
        },
        initial_match_score=initial_match_score,
        score_before=score_before,
        score_after=score_after,
        version_id=version_id,
    )
