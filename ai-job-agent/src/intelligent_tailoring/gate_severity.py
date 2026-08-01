"""Quality-gate severity classification and user-facing messages.

Critical gates block download/export but must not prevent preview/review.
Warning gates allow both preview and export.
"""

from __future__ import annotations

import re
from typing import Any

# Critical — fabricated / structurally unsafe output
CRITICAL_PREFIXES: tuple[str, ...] = (
    "unsupported_impact",
    "unsupported_entity",
    "cross_entry_tech",
    "unknown_skill",
    "missing_professional_summary",
    "raw_llm_reasoning",
    "linguistic_integrity",
    "writing_quality:facts_changed",
    "writing_quality:grammar:",
    "page_count:",
    "broken_structure",
    "inflated_years",
    "invalid_dates",
)

# Warning — quality / ATS / ordering issues (non-blocking for preview)
WARNING_PREFIXES: tuple[str, ...] = (
    "writing_quality:ats:",
    "writing_quality:style:",
    "underused_evidence",
    "non_optimal_ordering",
    "optional_requirement_missing",
    "weak_wording",
    "low_ats",
    "change_log_text_missing",
)


def classify_gate_failure(failure: str) -> str:
    """Return ``critical`` or ``warning`` for a technical failure code."""
    text = str(failure or "")
    for prefix in CRITICAL_PREFIXES:
        if text.startswith(prefix):
            return "critical"
    for prefix in WARNING_PREFIXES:
        if text.startswith(prefix):
            return "warning"
    # Unknown failures default to warning so preview stays available
    return "warning"


def humanize_gate_failure(failure: str) -> str:
    """Translate a technical gate code into a readable user message."""
    text = str(failure or "").strip()
    if not text:
        return "A quality check reported an issue."

    m = re.match(r"^cross_entry_tech:(.+?):(.+)$", text)
    if m:
        project, techs = m.group(1).strip(), m.group(2).strip()
        tech_label = techs.split(",")[0].strip()
        tech_display = tech_label[:1].upper() + tech_label[1:] if tech_label else "Technology"
        return (
            f"{tech_display} could not be verified for the {project}. "
            "Review the project details before downloading."
        )

    m = re.match(r"^unknown_skill:(.+)$", text)
    if m:
        return (
            f"The skill “{m.group(1)}” could not be verified from your source resume. "
            "Review skills before downloading."
        )

    m = re.match(r"^unsupported_impact:(.+?):(.+)$", text)
    if m:
        return (
            f"An impact claim in {m.group(1)} may not be supported by your source resume: "
            f"“{m.group(2)[:80]}”."
        )

    m = re.match(r"^unsupported_entity:(.+?):(.+)$", text)
    if m:
        return (
            f"A statement in {m.group(1)} may reference details not found in your source resume."
        )

    if text.startswith("missing_professional_summary"):
        return "The professional summary is missing. Review the summary before downloading."

    if text.startswith("page_count:"):
        return "The resume may not fit on one page. Review layout before downloading."

    if text.startswith("linguistic_integrity"):
        return "Some wording looks corrupted or incomplete. Review the highlighted sections."

    if text.startswith("writing_quality:facts_changed"):
        return "Writing polish changed factual content. Review before downloading."

    if text.startswith("writing_quality:ats:"):
        return "ATS alignment is lower than ideal. You can still preview and download."

    if text.startswith("low_ats") or text.startswith("underused_evidence"):
        return "Resume quality warnings were found. Preview is available; review before applying."

    return f"Quality check: {text}"


def classify_quality_gates(gates: dict[str, Any] | None) -> dict[str, Any]:
    """Split gate failures into critical / warning with user-facing messages."""
    gates = dict(gates or {})
    failures = list(gates.get("failures") or [])
    critical: list[str] = []
    warnings: list[str] = list(gates.get("warnings") or [])
    messages: list[dict[str, str]] = []

    for failure in failures:
        severity = classify_gate_failure(str(failure))
        message = humanize_gate_failure(str(failure))
        messages.append(
            {
                "code": str(failure),
                "severity": severity,
                "message": message,
            }
        )
        if severity == "critical":
            critical.append(str(failure))
        else:
            warnings.append(str(failure))

    return {
        **gates,
        "critical_failures": critical,
        "warning_failures": warnings,
        "user_messages": messages,
        "download_blocked": len(critical) > 0,
        "preview_allowed": True,
        "review_mode": len(critical) > 0,
        "passed": len(critical) == 0 and len(failures) == 0,
        "passed_critical": len(critical) == 0,
    }


def should_block_download(gates: dict[str, Any] | None) -> bool:
    """True when export/download must be blocked."""
    if not gates:
        return True
    classified = gates if "download_blocked" in gates else classify_quality_gates(gates)
    return bool(classified.get("download_blocked"))
