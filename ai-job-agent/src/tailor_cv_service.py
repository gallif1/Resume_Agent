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
    cv_data_dir,
    user_cv_profile_path,
    user_data_dir,
)
from db import DEFAULT_USER_ID, WORKSPACE_CV_ID
from job_analyzer import JobProfile, parse_stored_job_profile
from multilingual_normalizer import expand_synonyms, to_canonical
from profile_matcher import score as profile_match_score
from skill_normalizer import normalize_skill

# Bump when the tailored Markdown / prompt contract changes (invalidates OpenAI file cache).
TAILOR_PROMPT_VERSION = "v5"
REGENERATE_PROMPT_VERSION = "v4"
NO_IMPROVEMENT_MESSAGE = "לא הצלחתי לייצר גרסה יותר טובה"

TAILOR_SYSTEM_PROMPT = """You are an expert ATS resume writer. You rewrite ANY candidate's existing CV
to maximize honest keyword/semantic alignment with ONE target job description, while producing a
dense ONE-PAGE A4 resume body.

Inputs (provided in the user message):
- base_cv_data — the candidate's real CV text + structured facts (any profession / seniority)
- job_description — the target job posting (title, company, full description, structured profile)

Your output must optimize for ATS parsers while remaining 100% truthful to base_cv_data.
These rules are UNIVERSAL — they apply to every candidate and every target role. Never hardcode
assumptions about a specific past role, company, industry, or transition path.

================================================================================
DYNAMIC DOMAIN ALIGNMENT & CAREER-PIVOT SAFETY RAILS (MANDATORY)
================================================================================
Before rewriting, dynamically extract:
1) Core Professional Domain of the candidate from base_cv_data (free-form label;
   e.g. Software Development, Marketing, Design, Product Management — deduced from
   THIS CV only; never use a fixed industry taxonomy).
2) Target Professional Domain from job_description.

If the target role is OUTSIDE the candidate's core professional domain (career pivot
or fundamental mismatch):
- NEVER hallucinate fake job titles, fake employers, or fake domain experience.
- NEVER rename past roles to look like the target profession.
- Honestly emphasize transferable skills, methods, and tools evidenced on base_cv_data.
- Adapt the professional Summary to reflect a career pivot / bridge narrative
  (honest intent + transferable strengths) without inventing history.
- Keep estimated_ats_score REALISTIC and typically low/moderate — do not inflate
  scores based on soft-skill or generic keyword overlap alone.
- Note residual domain gaps clearly in caveats / פירוט שינויים.

If domains align, tailor normally while still obeying zero-hallucination rules.

================================================================================
RETURN FORMAT
================================================================================
Return ONE JSON object with exactly these keys:
- markdown: string — full response document in Markdown (see REQUIRED MARKDOWN STRUCTURE)
- changes_breakdown: array of short strings — the change bullets (same content as section 1)
- estimated_ats_score: integer 0-100 — realistic expected ATS match for the *tailored* CV
- cv_markdown: string — ONLY the resume body (section 3), without the section heading
- highlights: array of short strings — 2-6 key ATS keyword alignments
- caveats: array of short strings — honesty notes (skills not claimed, residual gaps)

REQUIRED MARKDOWN STRUCTURE for `markdown` (use these Hebrew headings):

## פירוט שינויים
- Bullet list of what you reframed/highlighted for THIS job.
- Match the dominant language of base_cv_data (Hebrew and/or English).
- Describe alignments generically (tools, methods, domains from the source CV ↔ JD keywords).
  Do not invent role- or company-specific history.

## ציון התאמה למשרה
- One short line with the expected ATS match score out of 100 for the tailored CV.
- Format example: "**ציון משוער: 68/100** — …"
- Be realistic. Do NOT invent experience to inflate the score.

---

## קורות החיים המעודכנים

Then the full tailored resume in clean Markdown using ## section headings:
Summary, Experience, Projects (only if real projects exist), Skills, Education
(only if education exists). Omit any empty section entirely.

================================================================================
STRICT CONTENT GOVERNANCE (ZERO-BUGS)
================================================================================

A) NEVER OMIT REAL EMPLOYMENT
- Real professional employment history from base_cv_data (paid jobs / companies /
  titles / dates) MUST remain the core of the EXPERIENCE section.
- Example: a real employer entry such as "Support Specialist @ Acme Corp"
  MUST appear under Experience — never drop real company experience in favor of
  academic / personal projects.
- Projects belong ONLY under Projects. Never duplicate a project under Experience
  and Projects at the same time.

B) ELIMINATE DUPLICATIONS
- Each section heading (Summary, Experience, Projects, Skills, Education, …)
  appears EXACTLY ONCE.
- Do not repeat the same bullet, sentence, or paragraph.
- Do not cut mid-sentence or leave truncated raw text fragments.

C) HIDE GHOST SECTIONS
- If Military Service, Volunteering, Awards, Languages, Certifications, Other,
  or any section has NO real content for this candidate, OMIT the section title
  and body completely. Never print an empty header.

D) ACCURATE TECH CATEGORIZATION
- Place tools under the correct Skills domain. Examples:
  - SQLAlchemy → Frameworks / Libraries / ORM (NOT Cloud/DevOps)
  - Expo → Mobile Frameworks / Toolkits (NOT Cloud/DevOps)
  - Docker / Kubernetes / AWS / GCP → Cloud / DevOps
  - PostgreSQL / MySQL / SQLite → Databases
  - React / FastAPI / Django → Frameworks / Libraries
- Prefer inline comma-separated skill rows by category (not vertical bullet lists).

================================================================================
ONE-PAGE DENSITY CONSTRAINTS (MANDATORY)
================================================================================
The resume body MUST fit on EXACTLY ONE A4 page. Enforce these hard caps:
1) Summary: maximum 3 dense, impactful sentences. No fluff.
2) Experience / Projects: maximum 3–4 concise, technical, metrics-driven bullets
   per role or project. Prefer impact + tools over filler.
3) Skills: inline category rows (e.g. `Languages: Python, SQL`) — minimal height.
4) Prefer compact wording; drop low-value soft skills and redundant phrasing.
5) Keep the resume body short enough for one printed A4 page with ~10–12mm margins.

================================================================================
UNIVERSAL HIGH-ATS TAILORING RULES (apply in order)
================================================================================

1) DYNAMIC "TARGET ROLE" HEADER INJECTION
- Extract the exact job title from job_description (prefer the posted title field;
  otherwise the clearest title in the JD text).
- Inject a prominent header near the top of the resume body (after name/contact):
  `Target Role: [Exact Job Title]`
- Do not invent a title that is not in the job posting.

2) UNIVERSAL "TECH-FIRST" WORK EXPERIENCE REFRAMING
- Analyze every REAL employment entry in base_cv_data and KEEP all of them.
- Rewrite each role's bullet points (≤4) to emphasize tasks, methodologies, tools,
  and technologies that overlap with job_description.
- TRANSITION RULE: If a past job title differs from the target role, de-emphasize
  generic/non-overlapping tasks and maximize transferable achievements that
  honestly appear in the source.
- STRICT CONSTRAINT: Do NOT change actual job titles, company names, or employment dates.

3) DYNAMIC ACADEMIC / PERSONAL PROJECTS AMPLIFICATION
- Locate projects and academic experience in base_cv_data (if any).
- Put them ONLY under Projects (never under Experience).
- Rewrite ≤4 bullets to showcase hands-on work that maps to the JD using technologies
  named in job_description ONLY when foundational evidence exists in base_cv_data.
- If there are no projects/academic items, omit the Projects section entirely.

4) SEMANTIC SKILLS MATRIX ALIGNMENT
- Dynamically rebuild the Skills section as compact categorized inline rows.
- Cross-reference the candidate's base skills/tools against job requirements.
- Explicitly list matching languages, frameworks, libraries, platforms, and tools
  honestly evidenced in base_cv_data.
- Use accurate categories (Languages | Frameworks/Libraries | Databases |
  Cloud/DevOps | Mobile | Tools/Platforms | Domain Skills) — adapt to CV + JD.
- Goal: maximize ATS keyword coverage without claiming skills the candidate lacks.

================================================================================
ZERO HALLUCINATION / HARD CONSTRAINTS
================================================================================
1. NEVER invent employers, job titles, degrees, certifications, tools, projects,
   metrics, or years of experience absent from base_cv_data.
2. NEVER rename real past job titles to match the target role — especially across
   professional domains.
3. You MAY reframe existing bullets and weave in JD keywords ONLY when they honestly
   map to skills/experience already on base_cv_data.
4. If the candidate lacks a required skill or domain credential, do NOT claim it.
   Omit it or note an adjacent evidenced skill in caveats — never fabricate.
5. Preserve contact details, education facts, dates, and employers from base_cv_data.
6. Write the CV primarily in the same language as base_cv_data; English tech terms
   from the JD may be used for keyword alignment when natural.
7. The horizontal rule (`---`) MUST appear once between the analysis sections and
   the resume body.
8. Keep `cv_markdown` identical to the body under "## קורות החיים המעודכנים"
   (without that heading). Keep `estimated_ats_score` consistent with section 2.
9. Use Markdown ## headings for sections and ### for role/project titles so the
   PDF renderer can parse the document cleanly.
10. When hard must-have JD constraints are unmet with zero evidence on the CV,
    keep estimated_ats_score ≤ 30 and say so honestly in section 2 / caveats.
"""

REGENERATE_SYSTEM_PROMPT = (
    TAILOR_SYSTEM_PROMPT
    + """

================================================================================
REGENERATE & OPTIMIZE MODE (deep-scan feedback loop)
================================================================================
You are refining an existing tailored CV draft to boost its ATS score against the
Job Description.

The user message supplies THREE inputs (plus the job description):
1) original_source_cvs — ALL original raw CV files/text and/or the compiled Master
   Profile uploaded by the candidate at the beginning (full history / ground truth)
2) latest_tailored_draft — the current tailored version that currently holds the
   highest score
3) ats_feedback_gaps — the specific missing keywords or weak sections identified
   by the deterministic ATS scorer

Your primary instruction is to look at the missing keywords/skills provided in
`ats_feedback_gaps`.

Then, perform a **deep-scan of the `original_source_cvs`** (the raw files uploaded
initially). Check if the candidate actually possesses those missing skills, tools,
or completed any relevant projects that were accidentally omitted or summarized
too tightly in the `latest_tailored_draft`.

- **If the missing skill/context exists in the original files:** Extract it and
  explicitly weave it into the relevant Experience, Projects, or Skills section
  of the new draft.
- **If the missing skill does NOT exist in the original files:** Do NOT hallucinate.
  Instead, safely reframe the existing technical bullet points in the
  `latest_tailored_draft` to align as closely as possible with the required
  methodologies without fabricating experience.

Strict Constraint: Never delete real companies, degrees, or positions present in
the original source documents.

Additional regenerate rules:
- Prefer the exact keyword spelling used in ats_feedback_gaps / the JD when truthful.
- Keep Summary ≤3 sentences and ≤4 bullets per role/project (ONE A4 page).
- In "## פירוט שינויים", list which matcher gaps you recovered from original sources
  vs which you could only reframe (and note unrecovered gaps in caveats).
- Start from latest_tailored_draft; improve it — do not rebuild from scratch if that
  would drop real employment or education facts.
"""
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
) -> str:
    """Assemble the user message that supplies base_cv_data + job_description."""
    return (
        "Tailor the candidate CV for the target job using the universal ATS rules "
        "in the system prompt.\n"
        "Analyze ONLY the provided base_cv_data and job_description — do not assume "
        "any specific prior role, company, or career path beyond what appears here.\n"
        "FIRST: dynamically extract Core Professional Domain (CV) and Target "
        "Professional Domain (JD). If they fundamentally mismatch, do NOT invent "
        "domain experience or rename titles — write an honest pivot/bridge Summary, "
        "emphasize transferable skills only, and keep estimated_ats_score realistic.\n"
        "CRITICAL: keep EVERY real employer/job from base_cv_data in Experience; "
        "never replace real employment with academic projects; omit empty sections; "
        "Summary ≤3 sentences; ≤4 bullets per role/project; one-page density only.\n"
        "Remember: inject `Target Role: [exact JD title]`; reframe bullets "
        "without renaming past titles/companies/dates; put projects only "
        "under Projects; rebuild an accurately categorized inline skills matrix.\n"
        "Return markdown with sections: פירוט שינויים, ציון התאמה למשרה, then ---, "
        "then קורות החיים המעודכנים.\n\n"
        "===== base_cv_data =====\n"
        f"{base_cv_data}\n\n"
        "===== job_description =====\n"
        f"{job_description}"
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

    missing_keywords = list(
        dict.fromkeys(
            list(ats_result.missing_required_skills)
            + list(profile_result.missing_skills)
        )
    )

    return {
        "ats_score": ats_result.ats_score,
        "score_label": ats_result.score_label,
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
    score = feedback.get("ats_score")
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
        f"The deterministic ATS matcher evaluated this draft at {score}/100 ({label}).",
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
        f"Current best ATS score: {feedback.get('ats_score')}/100 "
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

    return (
        "REGENERATE & OPTIMIZE the existing tailored CV using the dual-lookup flow.\n"
        "Primary target: close ats_feedback_gaps by deep-scanning original_source_cvs.\n"
        "If a gap is evidenced in the originals, extract and weave it into the draft.\n"
        "If not evidenced, reframe latest_tailored_draft honestly — never hallucinate.\n"
        "Never delete real companies, degrees, or positions from the original sources.\n"
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


def _apply_matcher_score_to_result(
    result: dict[str, Any],
    *,
    feedback: dict[str, Any],
) -> dict[str, Any]:
    """Prefer the deterministic matcher score in the saved/displayed document."""
    score = _clamp_score(feedback.get("ats_score"))
    if score is None:
        return result

    label = feedback.get("score_label") or ""
    score_line = f"**ציון משוער: {score}/100**"
    if label:
        score_line += f" — {label} (מדד ATS דטרמיניסטי)"

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
) -> dict[str, Any]:
    """Improve the best tailored draft via deep-scan of original source CVs.

    Dual-lookup inputs:
    - original_source_cvs (all uploads + master profile)
    - latest_tailored_draft (current highest-scoring saved draft)
    - ats_feedback_gaps (deterministic matcher missing keywords / weak sections)

    Score guard: only overwrite the saved draft when the new ATS score is
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

    cv_profile = _load_cv_profile_or_raise(cv_id, user_id=user_id)
    job_profile = parse_stored_job_profile(job.get("job_profile"))

    previous_feedback = evaluate_tailored_draft(
        cv_profile=cv_profile,
        draft_markdown=previous,
        job=job,
        job_profile=job_profile,
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
    )

    try:
        raw = call_openai_json(
            REGENERATE_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.2,
            use_cache=use_cache,
            cache_namespace=(
                f"tailor_cv_regen_{REGENERATE_PROMPT_VERSION}_"
                f"{TAILOR_PROMPT_VERSION}_{cv_id}"
            ),
            cache_payload=(
                f"regen|{REGENERATE_PROMPT_VERSION}|{TAILOR_PROMPT_VERSION}|"
                f"{cv_id}|{job_id}|{previous_feedback.get('ats_score')}|"
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

    previous_score = int(previous_feedback.get("ats_score") or 0)
    new_score = int(new_feedback.get("ats_score") or 0)
    saved_path = str(tailored_cv_path(cv_id, job_id))

    # Score guard: never overwrite the saved draft with an equal/worse version.
    if new_score <= previous_score:
        preserved = _result_from_saved_markdown(previous, saved_path=saved_path)
        return {
            **preserved,
            "estimated_ats_score": previous_score or preserved.get(
                "estimated_ats_score"
            ),
            "from_cache": True,
            "saved_path": saved_path,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "regenerated": False,
            "improved": False,
            "no_improvement": True,
            "message": NO_IMPROVEMENT_MESSAGE,
            "matcher_feedback": {
                "previous": previous_feedback,
                "current": previous_feedback,
                "discarded": new_feedback,
            },
        }

    result = _apply_matcher_score_to_result(result, feedback=new_feedback)
    path = save_tailored_cv(cv_id, job_id, result["markdown"])
    return {
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
    }


def tailor_cv_for_job(
    cv_id: str,
    job: dict[str, Any],
    *,
    force: bool = False,
    use_cache: bool = True,
    regenerate: bool = False,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Generate (or load) an ATS-tailored Markdown CV for one job.

    When ``regenerate`` is True, deep-scan original source CVs against ATS gaps
    on the current best tailored draft and only keep a strictly higher score.
    """
    if regenerate:
        return _regenerate_tailored_cv(cv_id, job, use_cache=False, user_id=user_id)

    job_id = int(job["id"])
    if not force:
        cached = load_saved_tailored_cv(cv_id, job_id)
        if cached:
            return _result_from_saved_markdown(
                cached, saved_path=str(tailored_cv_path(cv_id, job_id))
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
    )

    try:
        raw = call_openai_json(
            TAILOR_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.25,
            use_cache=use_cache,
            cache_namespace=f"tailor_cv_{TAILOR_PROMPT_VERSION}_{cv_id}",
            cache_payload=(
                f"{TAILOR_PROMPT_VERSION}|{cv_id}|{job_id}|"
                f"{job_description[:2000]}|{base_cv_data[:4000]}"
            ),
        )
    except OpenAIAPIError as exc:
        raise TailorCvError(str(exc), status_code=502) from exc

    result = _normalize_tailor_result(raw)
    path = save_tailored_cv(cv_id, job_id, result["markdown"])
    return {
        **result,
        "from_cache": bool(raw.get("_from_cache")),
        "saved_path": str(path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regenerated": False,
    }
