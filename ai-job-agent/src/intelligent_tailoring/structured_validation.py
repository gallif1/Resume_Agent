"""Deterministic (non-LLM) validation after every content-producing agent.

Checks run in plain code before handoff. Failures produce specific error codes
so the responsible agent can be re-invoked with actionable feedback rather than
passing broken output silently downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from intelligent_tailoring.canonical_resume import looks_like_raw_data, text_overlap_ratio
from intelligent_tailoring.linguistic_integrity import (
    detect_broken_patterns,
    has_duplicate_sentence,
    has_repeated_ngram,
)
from intelligent_tailoring.services.similarity import sequence_similarity
from intelligent_tailoring.structured_resume import (
    assign_stable_ids,
    base_source_ids,
    count_content_units,
    to_structured_resume,
    validate_structured_schema,
)

# Near-duplicate threshold requested by the fullness/quality spec.
NEAR_DUP_THRESHOLD = 0.85

# Tailored content volume must stay within this fraction of the base resume.
MIN_CONTENT_RATIO = 0.80

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_COMPETING_LEAD_IN = re.compile(
    r"\b("
    r"(?:frontend|backend|full[\s-]?stack|software)\s+"
    r"(?:engineer|developer|programmer)"
    r")\s+("
    r"(?:frontend|backend|full[\s-]?stack|software)\s+"
    r"(?:engineer|developer|programmer)"
    r")\b",
    flags=re.I,
)
_FRAGMENT_LEAD = re.compile(
    r"^(?:and|with|using|via|to|for|including)\b",
    flags=re.I,
)


@dataclass
class ValidationIssue:
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}

    def feedback_line(self) -> str:
        if self.path:
            return f"[{self.code}] {self.path}: {self.message}"
        return f"[{self.code}] {self.message}"


@dataclass
class ValidationReport:
    """Result of deterministic structured-resume validation."""

    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    structured: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)

    def error_codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def feedback_for_agent(self) -> str:
        if self.passed:
            return ""
        lines = [
            "DETERMINISTIC VALIDATION FAILED — fix these specific issues and "
            "return corrected structured JSON only:",
        ]
        for issue in self.issues:
            lines.append(f"- {issue.feedback_line()}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
            "checks": dict(self.checks),
            "error_codes": self.error_codes(),
        }


def _collect_text_fields(structured: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    summary = str(structured.get("summary") or "").strip()
    if summary:
        rows.append(("summary", summary))
    title = str(structured.get("title") or "").strip()
    if title:
        rows.append(("title", title))
    for idx, entry in enumerate(structured.get("experience") or []):
        if not isinstance(entry, dict):
            continue
        for key in ("position", "organization", "dateRange"):
            val = str(entry.get(key) or "").strip()
            if val:
                rows.append((f"experience[{idx}].{key}", val))
        for bi, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet or "").strip()
            if text:
                rows.append((f"experience[{idx}].bullets[{bi}]", text))
    for idx, entry in enumerate(structured.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        for key in ("title", "description"):
            val = str(entry.get(key) or "").strip()
            if val:
                rows.append((f"projects[{idx}].{key}", val))
        for bi, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet or "").strip()
            if text:
                rows.append((f"projects[{idx}].bullets[{bi}]", text))
    for cat, atoms in (structured.get("skills") or {}).items():
        for ai, atom in enumerate(atoms or []):
            text = str(atom or "").strip()
            if text:
                rows.append((f"skills.{cat}[{ai}]", text))
    for idx, entry in enumerate(structured.get("education") or []):
        if not isinstance(entry, dict):
            continue
        for key in ("degree", "institution", "fieldOfStudy", "dateRange"):
            val = str(entry.get(key) or "").strip()
            if val:
                rows.append((f"education[{idx}].{key}", val))
    return rows


def check_missing_ids(
    structured: dict[str, Any],
    *,
    source_facts: dict[str, Any] | None,
) -> list[ValidationIssue]:
    required = base_source_ids(source_facts)
    present_exp = {
        str(e.get("id") or "").strip()
        for e in (structured.get("experience") or [])
        if isinstance(e, dict) and str(e.get("id") or "").strip()
    }
    present_proj = {
        str(p.get("id") or "").strip()
        for p in (structured.get("projects") or [])
        if isinstance(p, dict) and str(p.get("id") or "").strip()
    }
    issues: list[ValidationIssue] = []
    for sid in sorted(required["experience_ids"] - present_exp):
        issues.append(
            ValidationIssue(
                code="missing_experience_id",
                message=(
                    f"Base experience id '{sid}' is missing. Every base Experience "
                    "position must appear (reorder/reword only — never drop)."
                ),
                path=f"experience.id={sid}",
            )
        )
    for sid in sorted(required["project_ids"] - present_proj):
        issues.append(
            ValidationIssue(
                code="missing_project_id",
                message=(
                    f"Base project id '{sid}' is missing. Every base Project "
                    "must appear (reorder/reword only — never drop)."
                ),
                path=f"projects.id={sid}",
            )
        )
    return issues


def check_raw_data_in_strings(structured: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path, text in _collect_text_fields(structured):
        if looks_like_raw_data(text):
            issues.append(
                ValidationIssue(
                    code="raw_data_in_string",
                    message=(
                        "Field contains raw dict/list/JSON syntax. Rewrite as "
                        "plain human-readable resume text."
                    ),
                    path=path,
                )
            )
        # Extra: list repr leftovers like ['Python', 'Go']
        if re.search(r"\[['\"][^'\"]+['\"]\s*,", text) or text.startswith("['"):
            issues.append(
                ValidationIssue(
                    code="raw_data_in_string",
                    message="Field looks like a stringified list. Use plain text.",
                    path=path,
                )
            )
    return issues


def check_duplicate_entries(structured: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_roles: dict[tuple[str, str], str] = {}
    for entry in structured.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        key = (
            str(entry.get("position") or "").strip().lower(),
            str(entry.get("organization") or "").strip().lower(),
        )
        if not key[0] and not key[1]:
            continue
        sid = str(entry.get("id") or "")
        if key in seen_roles:
            issues.append(
                ValidationIssue(
                    code="duplicate_experience_entry",
                    message=(
                        f"Duplicate position+organization '{entry.get('position')} @ "
                        f"{entry.get('organization')}'. Keep one entry per id."
                    ),
                    path=f"experience.id={sid}",
                )
            )
        else:
            seen_roles[key] = sid

    seen_projects: dict[str, str] = {}
    for entry in structured.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip().lower()
        if not title:
            continue
        sid = str(entry.get("id") or "")
        if title in seen_projects:
            issues.append(
                ValidationIssue(
                    code="duplicate_project_entry",
                    message=(
                        f"Duplicate project title '{entry.get('title')}'. "
                        "Keep one entry per id."
                    ),
                    path=f"projects.id={sid}",
                )
            )
        else:
            seen_projects[title] = sid

    # Duplicate stable ids
    exp_ids = [
        str(e.get("id") or "").strip()
        for e in (structured.get("experience") or [])
        if isinstance(e, dict) and str(e.get("id") or "").strip()
    ]
    if len(exp_ids) != len(set(exp_ids)):
        issues.append(
            ValidationIssue(
                code="duplicate_experience_id",
                message="Two experience entries share the same id.",
                path="experience",
            )
        )
    proj_ids = [
        str(p.get("id") or "").strip()
        for p in (structured.get("projects") or [])
        if isinstance(p, dict) and str(p.get("id") or "").strip()
    ]
    if len(proj_ids) != len(set(proj_ids)):
        issues.append(
            ValidationIssue(
                code="duplicate_project_id",
                message="Two project entries share the same id.",
                path="projects",
            )
        )
    return issues


def _near_dup(a: str, b: str, *, threshold: float = NEAR_DUP_THRESHOLD) -> bool:
    if not a or not b:
        return False
    if a.strip().lower() == b.strip().lower():
        return True
    if text_overlap_ratio(a, b) >= threshold:
        return True
    if sequence_similarity(a, b) >= threshold:
        return True
    return False


def check_near_duplicate_bullets(structured: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    summary = str(structured.get("summary") or "").strip()
    all_bullets: list[tuple[str, str]] = []

    for idx, entry in enumerate(structured.get("experience") or []):
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or idx)
        local: list[str] = []
        for bi, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet or "").strip()
            if not text:
                continue
            path = f"experience[{idx}/id={sid}].bullets[{bi}]"
            for prior in local:
                if _near_dup(text, prior):
                    issues.append(
                        ValidationIssue(
                            code="near_duplicate_bullet",
                            message="Bullet is a near-duplicate of another bullet in the same entry.",
                            path=path,
                        )
                    )
                    break
            local.append(text)
            all_bullets.append((path, text))

    for idx, entry in enumerate(structured.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or idx)
        local = []
        title = str(entry.get("title") or "").strip()
        desc = str(entry.get("description") or "").strip()
        if title and desc and _near_dup(title, desc):
            issues.append(
                ValidationIssue(
                    code="project_description_duplicates_title",
                    message=(
                        "Project description restates the title and would render as "
                        "a duplicated heading. Clear description or rewrite it."
                    ),
                    path=f"projects[{idx}/id={sid}].description",
                )
            )
        for bi, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet or "").strip()
            if not text:
                continue
            path = f"projects[{idx}/id={sid}].bullets[{bi}]"
            for prior in local:
                if _near_dup(text, prior):
                    issues.append(
                        ValidationIssue(
                            code="near_duplicate_bullet",
                            message="Bullet is a near-duplicate of another bullet in the same entry.",
                            path=path,
                        )
                    )
                    break
            if desc and _near_dup(text, desc):
                issues.append(
                    ValidationIssue(
                        code="near_duplicate_bullet",
                        message="Bullet near-duplicates the project description.",
                        path=path,
                    )
                )
            if title and _near_dup(text, title):
                issues.append(
                    ValidationIssue(
                        code="near_duplicate_bullet",
                        message="Bullet restates the project title.",
                        path=path,
                    )
                )
            local.append(text)
            all_bullets.append((path, text))

    # Cross-entry + summary
    for i, (path_a, text_a) in enumerate(all_bullets):
        if summary and len(text_a.split()) >= 6 and _near_dup(text_a, summary):
            # Only flag when the bullet heavily overlaps a summary sentence
            for sent in _SENTENCE_SPLIT.split(summary):
                if len(sent.split()) >= 6 and _near_dup(text_a, sent):
                    issues.append(
                        ValidationIssue(
                            code="near_duplicate_bullet",
                            message="Bullet near-duplicates a summary sentence.",
                            path=path_a,
                        )
                    )
                    break
        for path_b, text_b in all_bullets[i + 1 :]:
            # Same-entry already checked; cross-entry still matters
            if path_a.rsplit(".bullets", 1)[0] == path_b.rsplit(".bullets", 1)[0]:
                continue
            if _near_dup(text_a, text_b):
                issues.append(
                    ValidationIssue(
                        code="near_duplicate_bullet",
                        message=f"Bullet near-duplicates content at {path_b}.",
                        path=path_a,
                    )
                )
    # Deduplicate issue paths
    seen: set[str] = set()
    unique: list[ValidationIssue] = []
    for issue in issues:
        key = f"{issue.code}:{issue.path}:{issue.message}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def check_contact_links(
    structured: dict[str, Any],
    *,
    source_facts: dict[str, Any] | None,
) -> list[ValidationIssue]:
    facts = assign_stable_ids(source_facts or {})
    src = facts.get("contact") if isinstance(facts.get("contact"), dict) else {}
    dst = structured.get("contact") if isinstance(structured.get("contact"), dict) else {}
    issues: list[ValidationIssue] = []
    for field_name in ("github", "linkedin", "email", "phone"):
        src_val = str(src.get(field_name) or "").strip()
        dst_val = str(dst.get(field_name) or "").strip()
        # Treat JSON null as missing
        if dst.get(field_name) is None:
            dst_val = ""
        if src_val and not dst_val:
            issues.append(
                ValidationIssue(
                    code="missing_contact_field",
                    message=(
                        f"Base resume has contact.{field_name}='{src_val}' but the "
                        "output omitted it. Copy contact links unaltered."
                    ),
                    path=f"contact.{field_name}",
                )
            )
    return issues


def check_summary_grammar(structured: dict[str, Any]) -> list[ValidationIssue]:
    summary = str(structured.get("summary") or "").strip()
    issues: list[ValidationIssue] = []
    if not summary:
        # Empty summary is allowed only as a soft signal; fullness/content rules
        # may still fail separately. Flag as grammar issue so writer can fill it.
        issues.append(
            ValidationIssue(
                code="summary_empty",
                message="Summary is empty. Write 2–4 complete, well-formed sentences.",
                path="summary",
            )
        )
        return issues

    if _COMPETING_LEAD_IN.search(summary):
        issues.append(
            ValidationIssue(
                code="summary_competing_lead_ins",
                message=(
                    "Summary concatenates two competing role lead-ins "
                    "(e.g. 'Frontend Engineer Frontend Developer'). Use one clear role phrase."
                ),
                path="summary",
            )
        )
    if has_duplicate_sentence(summary):
        issues.append(
            ValidationIssue(
                code="summary_duplicate_sentence",
                message="Summary contains a duplicated sentence.",
                path="summary",
            )
        )
    if has_repeated_ngram(summary, n=3):
        issues.append(
            ValidationIssue(
                code="summary_repeated_ngram",
                message="Summary contains a repeated phrase fragment.",
                path="summary",
            )
        )
    for code in detect_broken_patterns(summary):
        issues.append(
            ValidationIssue(
                code=f"summary_broken:{code}",
                message=f"Summary has broken construction ({code}).",
                path="summary",
            )
        )
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(summary) if s.strip()]
    if len(sentences) == 0:
        issues.append(
            ValidationIssue(
                code="summary_not_sentences",
                message="Summary is not a complete sentence.",
                path="summary",
            )
        )
    elif len(sentences) > 6:
        issues.append(
            ValidationIssue(
                code="summary_too_many_sentences",
                message="Summary should be a small number of complete sentences (about 2–4).",
                path="summary",
            )
        )
    for sent in sentences:
        if _FRAGMENT_LEAD.match(sent) or not re.search(r"[.!?]$", sent + "."):
            # Fragment lead-in is a hard fail; missing terminal punct is soft —
            # only fail on clear fragment leads.
            if _FRAGMENT_LEAD.match(sent):
                issues.append(
                    ValidationIssue(
                        code="summary_fragment",
                        message=f"Summary sentence looks like a fragment: '{sent[:80]}'",
                        path="summary",
                    )
                )
    # Word-count sanity (complete professional summary)
    words = summary.split()
    if len(words) < 12:
        issues.append(
            ValidationIssue(
                code="summary_too_short",
                message="Summary is too short to read as a complete professional summary.",
                path="summary",
            )
        )
    return issues


def check_content_fullness(
    structured: dict[str, Any],
    *,
    source_facts: dict[str, Any] | None,
    min_ratio: float = MIN_CONTENT_RATIO,
) -> list[ValidationIssue]:
    """Fail when tailored content volume is visibly thinner than the base resume."""
    facts = assign_stable_ids(source_facts or {})
    base_resume = {
        "experience": facts.get("experience_roles") or [],
        "projects": facts.get("projects") or [],
    }
    base_counts = count_content_units(base_resume)
    out_counts = count_content_units(structured)
    issues: list[ValidationIssue] = []
    base_total = max(int(base_counts.get("total_units") or 0), 0)
    out_total = max(int(out_counts.get("total_units") or 0), 0)
    if base_total > 0 and out_total < max(1, int(round(base_total * min_ratio))):
        issues.append(
            ValidationIssue(
                code="content_volume_too_low",
                message=(
                    f"Output has {out_total} content units vs base {base_total} "
                    f"(below {int(min_ratio * 100)}%). Do not drop entries; write "
                    "fuller honest bullets (context + action + outcome/tech) and "
                    "keep every base experience/project id."
                ),
                path="experience+projects",
            )
        )
    return issues


def check_jd_contamination(
    structured: dict[str, Any],
    *,
    jd_text: str = "",
) -> list[ValidationIssue]:
    """Reject candidate claims that reuse job-posting wording or employer voice."""
    from intelligent_tailoring.jd_contamination import validate_resume_against_jd
    from intelligent_tailoring.structured_resume import structured_to_pipeline_resume

    # validate_resume_against_jd expects pipeline field names
    try:
        pipeline = structured_to_pipeline_resume(structured)
    except Exception:  # noqa: BLE001
        pipeline = structured if isinstance(structured, dict) else {}
    report = validate_resume_against_jd(pipeline, jd_text=jd_text or "")
    issues: list[ValidationIssue] = []
    for raw in report.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        issues.append(
            ValidationIssue(
                code=str(raw.get("code") or "jd_contamination"),
                message=str(raw.get("message") or "Job-posting language leaked into resume."),
                path=str(raw.get("path") or ""),
            )
        )
    return issues


def validate_structured_resume(
    resume: dict[str, Any] | None,
    *,
    source_facts: dict[str, Any] | None = None,
    enforce_fullness: bool = True,
    require_summary: bool = True,
    already_structured: bool = False,
    jd_text: str = "",
) -> ValidationReport:
    """Run all deterministic checks. Returns a report (never raises for soft fails).

    Schema malformation is recorded as issues rather than raising, so the caller
    can always feed ``feedback_for_agent()`` into a regeneration prompt.
    """
    issues: list[ValidationIssue] = []
    checks: dict[str, bool] = {}
    structured: dict[str, Any] = {}

    try:
        if already_structured and isinstance(resume, dict):
            structured = resume
            validate_structured_schema(structured)
        else:
            structured = to_structured_resume(resume, source_facts=source_facts)
            validate_structured_schema(structured)
        checks["schema"] = True
    except Exception as exc:  # noqa: BLE001 — convert to issue list
        checks["schema"] = False
        issues.append(
            ValidationIssue(
                code="schema_invalid",
                message=str(exc),
                path="structured_resume",
            )
        )
        # Best-effort partial structure for remaining checks
        try:
            structured = (
                resume
                if already_structured and isinstance(resume, dict)
                else to_structured_resume(
                    resume, source_facts=source_facts, require_ids=False
                )
            )
        except Exception:  # noqa: BLE001
            structured = resume if isinstance(resume, dict) else {}

    id_issues = check_missing_ids(structured, source_facts=source_facts)
    checks["stable_ids"] = not id_issues
    issues.extend(id_issues)

    raw_issues = check_raw_data_in_strings(structured)
    checks["no_raw_data"] = not raw_issues
    issues.extend(raw_issues)

    dup_issues = check_duplicate_entries(structured)
    checks["no_duplicate_entries"] = not dup_issues
    issues.extend(dup_issues)

    near_issues = check_near_duplicate_bullets(structured)
    checks["no_near_duplicate_bullets"] = not near_issues
    issues.extend(near_issues)

    contact_issues = check_contact_links(structured, source_facts=source_facts)
    checks["contact_preserved"] = not contact_issues
    issues.extend(contact_issues)

    if require_summary:
        summary_issues = check_summary_grammar(structured)
    else:
        # Still catch raw/broken when a summary is present
        summary_issues = [
            i
            for i in check_summary_grammar(structured)
            if i.code not in {"summary_empty", "summary_too_short"}
            or str(structured.get("summary") or "").strip()
        ]
        # If empty during Agent 2 pre-writer, allow empty
        summary_issues = [
            i
            for i in summary_issues
            if not (
                i.code in {"summary_empty", "summary_too_short"}
                and not str(structured.get("summary") or "").strip()
            )
        ]
    checks["summary_well_formed"] = not summary_issues
    issues.extend(summary_issues)

    if enforce_fullness:
        fullness_issues = check_content_fullness(structured, source_facts=source_facts)
    else:
        fullness_issues = []
    checks["content_fullness"] = not fullness_issues
    issues.extend(fullness_issues)

    # Prefer explicit jd_text; fall back to source_facts snapshot when callers
    # wired JD onto resume facts / strategy for repair + contamination checks.
    jd_blob = (jd_text or "").strip()
    if not jd_blob and isinstance(source_facts, dict):
        jd_blob = str(
            source_facts.get("jd_text")
            or source_facts.get("job_description")
            or (source_facts.get("job") or {}).get("description")
            or ""
        ).strip()
    jd_issues = check_jd_contamination(structured, jd_text=jd_blob)
    checks["no_jd_contamination"] = not jd_issues
    issues.extend(jd_issues)

    # Summary must read as candidate facts — not employer instructions/opinions.
    from intelligent_tailoring.jd_contamination import summary_describes_candidate_only

    summary_text = str(structured.get("summary") or "").strip()
    if summary_text:
        ok_candidate, sanity_codes = summary_describes_candidate_only(summary_text)
        if not ok_candidate:
            for code in sanity_codes:
                issues.append(
                    ValidationIssue(
                        code=f"summary_not_candidate:{code}",
                        message=(
                            "Summary must describe things the candidate did/knows "
                            "from candidate facts only — no JD instructions, "
                            "opinions, or second-person employer voice."
                        ),
                        path="summary",
                    )
                )
            checks["summary_candidate_only"] = False
        else:
            checks["summary_candidate_only"] = True
    else:
        checks["summary_candidate_only"] = not require_summary

    return ValidationReport(
        passed=len(issues) == 0,
        issues=issues,
        structured=structured,
        checks=checks,
    )


def _merge_bullets_without_dupes(preferred: list[str], fallback: list[str]) -> list[str]:
    """Keep preferred wording; fill gaps from fallback; near-dedupe at render threshold."""
    from intelligent_tailoring.services.one_page_compressor import (
        _dedupe_similar,
        texts_are_near_duplicates,
    )

    merged: list[str] = []
    for text in list(preferred) + list(fallback):
        cleaned = str(text or "").strip()
        if not cleaned:
            continue
        if any(texts_are_near_duplicates(cleaned, prior) for prior in merged):
            continue
        if any(_near_dup(cleaned, prior, threshold=0.85) for prior in merged):
            continue
        merged.append(cleaned)
    return _dedupe_similar(merged)


def _restore_full_source_bullets(
    resume: dict[str, Any],
    *,
    source_facts: dict[str, Any],
) -> dict[str, Any]:
    """Merge source bullets into matching entries without reintroducing duplicates."""
    from copy import deepcopy

    from intelligent_tailoring.services.one_page_compressor import (
        texts_are_near_duplicates,
    )

    out = deepcopy(resume) if isinstance(resume, dict) else {}
    facts = assign_stable_ids(source_facts)
    src_roles = [
        r for r in (facts.get("experience_roles") or []) if isinstance(r, dict)
    ]
    src_roles_by_id = {
        str(r.get("id") or r.get("source_entry_id") or ""): r for r in src_roles
    }
    src_projects = [
        p for p in (facts.get("projects") or []) if isinstance(p, dict)
    ]
    src_projects_by_id = {
        str(p.get("id") or p.get("source_entry_id") or ""): p for p in src_projects
    }

    def _norm_name(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip().lower())

    def _names_match(a: str, b: str) -> bool:
        na, nb = _norm_name(a), _norm_name(b)
        if not na or not nb:
            return False
        return na == nb or na in nb or nb in na

    def _resolve_role(entry: dict[str, Any]) -> dict[str, Any] | None:
        sid = str(entry.get("id") or entry.get("source_entry_id") or "")
        src = src_roles_by_id.get(sid) if sid else None
        title = str(entry.get("title") or "")
        company = str(entry.get("company") or "")
        if src is not None:
            if _names_match(title, str(src.get("title") or "")) or (
                not title
                and _names_match(company, str(src.get("company") or ""))
            ):
                return src
        for cand in src_roles:
            ct = str(cand.get("title") or "")
            cc = str(cand.get("company") or "")
            if title and ct and _names_match(title, ct):
                if (
                    not company
                    or not cc
                    or _names_match(company, cc)
                ):
                    return cand
        return None

    def _resolve_project(entry: dict[str, Any]) -> dict[str, Any] | None:
        sid = str(entry.get("id") or entry.get("source_entry_id") or "")
        src = src_projects_by_id.get(sid) if sid else None
        name = str(entry.get("name") or "")
        if src is not None and _names_match(name, str(src.get("name") or "")):
            return src
        for cand in src_projects:
            if name and _names_match(name, str(cand.get("name") or "")):
                return cand
        return None

    experience = []
    for entry in out.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        fixed = dict(entry)
        src = _resolve_role(fixed)
        if src:
            src_bullets = [
                str(b).strip() for b in (src.get("bullets") or []) if str(b).strip()
            ]
            cur = [str(b).strip() for b in (fixed.get("bullets") or []) if str(b).strip()]
            fixed["bullets"] = _merge_bullets_without_dupes(cur, src_bullets)
            if not fixed.get("title"):
                fixed["title"] = str(src.get("title") or "")
            if not fixed.get("company"):
                fixed["company"] = str(src.get("company") or "")
            if not fixed.get("dates"):
                fixed["dates"] = str(src.get("dates") or "")
            # Keep the verified source id after identity-resolved restore.
            sid = str(src.get("id") or src.get("source_entry_id") or "")
            if sid:
                fixed["id"] = sid
                fixed["source_entry_id"] = sid
        experience.append(fixed)
    out["experience"] = experience

    projects = []
    for entry in out.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        fixed = dict(entry)
        src = _resolve_project(fixed)
        if src:
            src_bullets = [
                str(b).strip() for b in (src.get("bullets") or []) if str(b).strip()
            ]
            cur = [str(b).strip() for b in (fixed.get("bullets") or []) if str(b).strip()]
            fixed["bullets"] = _merge_bullets_without_dupes(cur, src_bullets)
            name = str(fixed.get("name") or src.get("name") or "").strip()
            desc = str(fixed.get("description") or "").strip()
            src_desc = str(src.get("description") or "").strip()
            if not desc and src_desc:
                desc = src_desc
            # Never keep a description that restates the project title.
            if desc and name and texts_are_near_duplicates(desc, name):
                desc = ""
            if desc and any(texts_are_near_duplicates(desc, b) for b in fixed["bullets"]):
                desc = ""
            fixed["description"] = desc
            if not fixed.get("name"):
                fixed["name"] = name
            sid = str(src.get("id") or src.get("source_entry_id") or "")
            if sid:
                fixed["id"] = sid
                fixed["source_entry_id"] = sid
        projects.append(fixed)
    out["projects"] = projects
    return out


def repair_structured_resume(
    resume: dict[str, Any] | None,
    *,
    source_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic repair for common validation failures (no LLM).

    Used after a failed regeneration attempt so the pipeline still produces a
    complete resume: restore missing ids from source, strip raw data, dedupe,
    restore contact, and pad content volume from source bullets.
    """
    from intelligent_tailoring.canonical_resume import (
        ensure_minimum_content_from_source,
        sanitize_raw_data_fields,
    )
    from intelligent_tailoring.requirement_coverage import preserve_contact
    from intelligent_tailoring.structural_integrity import (
        validate_and_repair_resume_structure,
    )
    from intelligent_tailoring.structured_resume import (
        stamp_ids_on_resume,
        structured_to_pipeline_resume,
    )

    facts = assign_stable_ids(source_facts or {})
    # Start from pipeline shape
    try:
        structured = to_structured_resume(resume, source_facts=facts, require_ids=False)
        pipeline = structured_to_pipeline_resume(structured)
    except Exception:  # noqa: BLE001
        pipeline = dict(resume) if isinstance(resume, dict) else {}

    # Preserve a usable summary before sanitizers touch it
    kept_summary = str(
        pipeline.get("professional_summary") or pipeline.get("summary") or ""
    ).strip()

    pipeline = sanitize_raw_data_fields(pipeline)
    pipeline = ensure_minimum_content_from_source(
        pipeline,
        resume_facts=facts,
        min_bullets_per_role=2,
        min_bullets_per_project=2,
    )
    pipeline = _restore_full_source_bullets(pipeline, source_facts=facts)
    pipeline = validate_and_repair_resume_structure(pipeline)
    pipeline = preserve_contact(
        pipeline,
        source_contact=facts.get("contact") if isinstance(facts.get("contact"), dict) else {},
        resume_facts=facts,
    )
    # If still thin, restore more source bullets per entry — then re-scrub so
    # restore cannot ship title/description/bullet duplicates downstream.
    report = validate_structured_resume(
        pipeline, source_facts=facts, enforce_fullness=True, require_summary=False
    )
    if not report.checks.get("content_fullness", True):
        pipeline = ensure_minimum_content_from_source(
            pipeline,
            resume_facts=facts,
            min_bullets_per_role=3,
            min_bullets_per_project=2,
        )
        pipeline = _restore_full_source_bullets(pipeline, source_facts=facts)
        pipeline = validate_and_repair_resume_structure(pipeline)

    if kept_summary and not str(
        pipeline.get("professional_summary") or pipeline.get("summary") or ""
    ).strip():
        pipeline["professional_summary"] = kept_summary
        pipeline["summary"] = kept_summary
    elif kept_summary and len(
        str(pipeline.get("professional_summary") or pipeline.get("summary") or "").split()
    ) < 12:
        pipeline["professional_summary"] = kept_summary
        pipeline["summary"] = kept_summary

    # Final scrub always — renderers must never see doubled titles/bullets.
    from intelligent_tailoring.services.one_page_compressor import (
        scrub_resume_duplicate_content,
    )
    from intelligent_tailoring.jd_contamination import strip_jd_contaminated_sentences

    pipeline = scrub_resume_duplicate_content(pipeline)
    pipeline = validate_and_repair_resume_structure(pipeline)
    # Strip employer/JD voice from summary if it leaked through.
    jd_blob = ""
    if isinstance(source_facts, dict):
        jd_blob = str(
            source_facts.get("jd_text")
            or (source_facts.get("job") or {}).get("description")
            or ""
        )
    summary = str(
        pipeline.get("professional_summary") or pipeline.get("summary") or ""
    ).strip()
    if summary:
        cleaned_summary = strip_jd_contaminated_sentences(summary, jd_text=jd_blob)
        if cleaned_summary:
            pipeline["professional_summary"] = cleaned_summary
            pipeline["summary"] = cleaned_summary
    pipeline = stamp_ids_on_resume(pipeline, source_facts=facts)
    return pipeline
