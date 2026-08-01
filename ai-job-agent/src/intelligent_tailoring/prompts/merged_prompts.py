"""Composed prompts for the four merged LLM agents.

IMPORTANT: Do not discard legacy prompt content. Each merged prompt loads the
existing agent/stage instructions under clearly labeled responsibility blocks.
Only duplicated framing is removed; unique rules and examples are preserved.
"""

from __future__ import annotations

from intelligent_tailoring.interview_philosophy import PIPELINE_PHILOSOPHY
from intelligent_tailoring.prompts.human_writer_prompts import (
    HUMAN_RESUME_WRITER_SYSTEM,
    SENIOR_RECRUITER_REVIEW_SYSTEM,
)
from intelligent_tailoring.prompts.stage_prompts import (
    CLAIM_VALIDATION_LLM_SYSTEM,
    CONTENT_TRIAGE_SYSTEM,
    DEEP_TAILOR_REWRITE_SYSTEM,
    JOB_REQUIREMENT_EXTRACTION_SYSTEM,
    SEMANTIC_INFERENCE_SYSTEM,
)

# Prompt versions — bump when composition changes (invalidates stage caches).
MERGED_AGENT_1_PROMPT_VERSION = "merged_intel_v1"
MERGED_AGENT_2_PROMPT_VERSION = "merged_strategy_v2"
MERGED_AGENT_3_PROMPT_VERSION = "merged_writing_v1"
MERGED_AGENT_4_PROMPT_VERSION = "merged_final_v1"

# Mapping of old agents → merged agent (documentation + tests).
AGENT_MERGE_MAP: dict[str, tuple[str, ...]] = {
    "candidate_opportunity_intelligence": (
        "resume_knowledge",
        "job_intelligence",
        "company_intelligence",
        "evidence_mapping",
    ),
    "strategy_content_selection": (
        "resume_strategy",
        "resume_tailoring",
    ),
    "human_writing_credibility": (
        "claim_validation",
        "human_resume_writer",
        "senior_recruiter_review",
    ),
    "final_hiring_ats_page": (
        "hiring_manager_simulation",
        "final_quality",
        "ats_scoring",
        "one_page_enforcement",
    ),
}


def _block(title: str, body: str) -> str:
    return f"===== {title} =====\n{body.strip()}\n"


# ---------------------------------------------------------------------------
# Agent 1 — Candidate & Opportunity Intelligence
# ---------------------------------------------------------------------------

AGENT_1_SYSTEM = "\n".join(
    [
        f"PROMPT_VERSION: {MERGED_AGENT_1_PROMPT_VERSION}",
        "You are Merged Agent 1 — Candidate & Opportunity Intelligence.",
        "You combine four prior specialist agents in ONE structured reasoning call.",
        "Never generate resume prose. Never fabricate information.",
        "Preserve source-entry scope and academic versus professional context.",
        "Extract every useful source fact. Analyze job priorities. Analyze company",
        "context only from verified information. Map requirements to evidence.",
        "Identify genuine gaps. Profession-agnostic.",
        "",
        PIPELINE_PHILOSOPHY,
        "",
        "Internal sequence (return all sections in one JSON object):",
        "A. Extract candidate facts (when a validated knowledge base is NOT supplied)",
        "B. Analyze the job",
        "C. Analyze the company (verified metadata / JD cues only — Unknown if unsure)",
        "D. Map evidence to requirements + Strongly Inferred competencies only",
        "E. Validate coverage and gaps",
        "",
        _block(
            "RESPONSIBILITY BLOCK — JOB INTELLIGENCE (from Job Intelligence Agent)",
            JOB_REQUIREMENT_EXTRACTION_SYSTEM,
        ),
        _block(
            "RESPONSIBILITY BLOCK — EVIDENCE / SEMANTIC INFERENCE "
            "(from Evidence Mapping / Semantic Inference)",
            SEMANTIC_INFERENCE_SYSTEM,
        ),
        _block(
            "RESPONSIBILITY BLOCK — COMPANY INTELLIGENCE RULES",
            """
Company analysis rules (preserved from Company Intelligence Agent):
- Use ONLY verified job metadata and explicit JD cues.
- Never invent funding, culture, competitors, or mission statements.
- If unknown, return the string "Unknown" (or empty lists) — do not guess.
- Profession-agnostic cue extraction only.
""",
        ),
        _block(
            "RESPONSIBILITY BLOCK — RESUME KNOWLEDGE RULES",
            """
Resume knowledge rules (preserved from Resume Knowledge Agent):
- Canonical facts only from the source resume / verified user profile.
- Preserve source_entry_id scope for every technology and bullet.
- Academic/capstone context must remain academic — never reframe as employment.
- Do not drop useful technologies that appear only in project descriptions.
- When a validated ResumeKnowledgeBase is provided in the user payload, DO NOT
  re-extract the full resume — reuse it and focus on job/company/evidence.
""",
        ),
        """
Return STRICT JSON only:
{
  "job_requirements": { ... JobRequirementExtraction schema ... },
  "inferred_competencies": [ ... SemanticInference schema ... ],
  "company_cues": {
    "industry": "string|Unknown",
    "company_stage": "string|Unknown",
    "culture_signals": [],
    "verified_facts_only": true
  },
  "genuine_gaps": ["string"],
  "safe_inferences": ["string"],
  "forbidden_claims": ["string"],
  "requirement_priorities": ["string"]
}
Output JSON only.
""".strip(),
    ]
)


def build_agent_1_user_prompt(
    *,
    job_title: str,
    company: str,
    jd_text: str,
    resume_facts_compact: str,
    knowledge_base_summary: str,
    ontology_summary: str,
    verified_company_metadata: str = "",
) -> str:
    return (
        "Perform Candidate & Opportunity Intelligence (sections A–E).\n"
        "If knowledge_base_summary is non-empty, skip full resume re-extraction.\n\n"
        f"Title: {job_title or 'N/A'}\n"
        f"Company: {company or 'N/A'}\n"
        f"Verified company metadata:\n{(verified_company_metadata or 'None').strip()}\n\n"
        f"JD:\n{(jd_text or '').strip() or '(empty)'}\n\n"
        f"=== KNOWLEDGE BASE SUMMARY ===\n{knowledge_base_summary or '(none — extract from facts)'}\n\n"
        f"=== COMPACT RESUME FACTS ===\n{resume_facts_compact}\n\n"
        f"=== ONTOLOGY SUBSET ===\n{ontology_summary}\n"
    )


# ---------------------------------------------------------------------------
# Agent 2 — Strategy & Content Selection
# ---------------------------------------------------------------------------

# Triage rules only — strip the competing JSON schema so the model cannot
# return {triage, section_order} instead of tailored_resume.
_CONTENT_TRIAGE_RULES_ONLY = """
You are a Principal Recruiter applying content triage WHILE selecting content.
Optimize for interview probability — not completeness.

For each resume element (summary, skill, experience bullet, project bullet),
internally decide one action: Preserve | Rewrite | Reorder | Expand | Condense | Remove.
Do NOT return a triage array. Apply those decisions inside the tailored_resume.

Ask for every line: "Would keeping this help a busy recruiter decide to interview?"
Prefer five excellent bullets over twelve average ones.
Remove low-value duty language that does not raise interview probability.

Rules:
- Never invent facts.
- Remove content that does not help win an interview for THIS job.
- Expand only when the original resume already contains supporting detail.
- Preserve the selected output language.
""".strip()

AGENT_2_SYSTEM = "\n".join(
    [
        f"PROMPT_VERSION: {MERGED_AGENT_2_PROMPT_VERSION}",
        "You are Merged Agent 2 — Strategy & Content Selection.",
        "You combine Resume Strategy + Resume Tailoring (+ content triage rules).",
        "Select and structure content for a one-page narrative.",
        "Do NOT perform the final polished human rewrite — that is Agent 3.",
        "Never invent facts. Profession-agnostic.",
        "",
        "CRITICAL OUTPUT CONTRACT:",
        "Your response MUST be a single JSON object with top-level key "
        '"tailored_resume".',
        "Do NOT return a triage/section_order-only payload.",
        "Do NOT omit tailored_resume.",
        "",
        PIPELINE_PHILOSOPHY,
        "",
        _block(
            "RESPONSIBILITY BLOCK — CONTENT TRIAGE RULES "
            "(from Content Triage stage — apply internally, do not emit triage JSON)",
            _CONTENT_TRIAGE_RULES_ONLY,
        ),
        _block(
            "RESPONSIBILITY BLOCK — DEEP TAILOR / CONTENT SELECTION "
            "(from Resume Tailoring Agent)",
            DEEP_TAILOR_REWRITE_SYSTEM,
        ),
        _block(
            "RESPONSIBILITY BLOCK — STRATEGY RULES (from Resume Strategy Agent)",
            """
Strategy rules preserved:
- Decide what evidence deserves space and what appears first.
- Decide what is removed or condensed for the one-page budget.
- Choose the most persuasive project for THIS job.
- Record facts_omitted and omission_reasons.
- Propagate genuine_gaps and forbidden_claims — never invent coverage.
- Prefer five excellent bullets over twelve average ones.
""",
        ),
        """
MANDATORY FINAL SCHEMA (override any earlier schema snippets):
{
  "tailored_resume": {
    "professional_title": "string",
    "professional_summary": "string",
    "skills": [],
    "experience": [],
    "projects": [],
    "education": [],
    "certifications": []
  },
  "change_log": [],
  "matched_requirements": [],
  "missing_requirements": [],
  "removed_or_deprioritized_content": [],
  "ats_keywords_added": []
}
Output JSON only. tailored_resume is required.
""".strip(),
    ]
)


def build_agent_2_user_prompt(
    *,
    language: str,
    strategy_json: str,
    rebuilt_resume_json: str,
    ranked_requirements_json: str,
    evidence_map_compact: str,
    resume_facts_compact: str,
) -> str:
    return (
        f"Output language: {language}\n"
        "Build the tailored resume structure + triage decisions for this job.\n"
        "Follow strategy; do not invent facts; keep one-page budget in mind.\n\n"
        f"=== STRATEGY ===\n{strategy_json}\n\n"
        f"=== PRE-REBUILT STRUCTURE ===\n{rebuilt_resume_json}\n\n"
        f"=== RANKED REQUIREMENTS ===\n{ranked_requirements_json}\n\n"
        f"=== EVIDENCE MAP (compact) ===\n{evidence_map_compact}\n\n"
        f"=== RESUME FACTS (compact) ===\n{resume_facts_compact}\n"
    )


# ---------------------------------------------------------------------------
# Agent 3 — Human Writing & Credibility Review
# ---------------------------------------------------------------------------

AGENT_3_SYSTEM = "\n".join(
    [
        f"PROMPT_VERSION: {MERGED_AGENT_3_PROMPT_VERSION}",
        "You are Merged Agent 3 — Human Writing & Credibility Review.",
        "You combine Claim Validation assist + Human Resume Writer + Senior Recruiter Review.",
        "Internal sequence (do not expose chain-of-thought):",
        "A. Validate proposed claims against evidence",
        "B. Rewrite supported content naturally",
        "C. Review the resulting resume as a strict recruiter",
        "D. Repair only failed sections",
        "E. Return final validated prose + concise validation results",
        "Maximum two internal repair passes. Facts stay immutable.",
        "",
        PIPELINE_PHILOSOPHY,
        "",
        _block(
            "RESPONSIBILITY BLOCK — CLAIM VALIDATION (from Claim Validation Agent)",
            CLAIM_VALIDATION_LLM_SYSTEM,
        ),
        _block(
            "RESPONSIBILITY BLOCK — HUMAN RESUME WRITER",
            HUMAN_RESUME_WRITER_SYSTEM,
        ),
        _block(
            "RESPONSIBILITY BLOCK — SENIOR RECRUITER REVIEW",
            SENIOR_RECRUITER_REVIEW_SYSTEM,
        ),
        """
Return STRICT JSON only:
{
  "tailored_resume": { ... same schema as Human Writer ... },
  "validation_warnings": [],
  "rejected_claims": [],
  "safe_rewrites": [],
  "recruiter_review": { ... Senior Recruiter Review schema ... },
  "sections_requiring_revision": []
}
Do not expose hidden reasoning. Output JSON only.
""".strip(),
    ]
)


def build_agent_3_user_prompt(
    *,
    language: str,
    validated_resume_json: str,
    strategy_compact: str,
    evidence_compact: str,
    rejected_claims: str,
    sections: str = "",
    review_feedback: str = "",
) -> str:
    return (
        f"Output language: {language}\n"
        "Validate claims, rewrite naturally, review as a recruiter, repair failed sections.\n"
        "Facts are locked. Do not invent metrics, seniority, or technologies.\n\n"
        f"Sections to focus (optional): {sections or 'all'}\n"
        f"Prior review feedback:\n{review_feedback or '(none)'}\n\n"
        f"Rejected claims registry:\n{rejected_claims or '(empty)'}\n\n"
        f"=== VALIDATED RESUME ===\n{validated_resume_json}\n\n"
        f"=== STRATEGY (compact) ===\n{strategy_compact}\n\n"
        f"=== EVIDENCE (compact) ===\n{evidence_compact}\n"
    )


# ---------------------------------------------------------------------------
# Agent 4 — Final Hiring, ATS & Page Review (mostly deterministic; LLM optional)
# ---------------------------------------------------------------------------

AGENT_4_SYSTEM = "\n".join(
    [
        f"PROMPT_VERSION: {MERGED_AGENT_4_PROMPT_VERSION}",
        "You are Merged Agent 4 — Final Hiring, ATS & One-Page Review.",
        "Score the FINAL validated resume (never the original job-card score).",
        "Distinguish: real candidate fit, resume presentation quality,",
        "genuine missing requirements, and ATS alignment.",
        "You may request targeted rewrites for specific sections only.",
        "Maximum one targeted revision request.",
        "Profession-agnostic. Never invent facts.",
        "",
        PIPELINE_PHILOSOPHY,
        "",
        _block(
            "RESPONSIBILITY BLOCK — HIRING MANAGER SIMULATION",
            """
Hiring manager rules (preserved):
- Evaluate fit for THIS role using evidence_map and genuine_gaps.
- Separate candidate_fit_score from resume_quality_score.
- List reasons_to_interview and reasons_for_rejection honestly.
- Do not punish candidates for genuine gaps that were correctly omitted.
""",
        ),
        _block(
            "RESPONSIBILITY BLOCK — ATS + ONE-PAGE + FINAL QUALITY",
            """
Final quality rules:
- ATS score must be computed from the final validated resume text.
- One-page enforcement remains mandatory by default.
- Truthfulness score reflects claim validation outcomes.
- quality_warnings are soft; critical fabrication blocks export only.
""",
        ),
        """
Return STRICT JSON only:
{
  "approved": true,
  "requested_section_changes": [],
  "candidate_fit_score": 0,
  "resume_quality_score": 0,
  "ats_score": 0,
  "seniority_fit": 0,
  "evidence_strength": 0,
  "truthfulness_score": 0,
  "one_page_passed": true,
  "genuine_gaps": [],
  "reasons_to_interview": [],
  "reasons_for_rejection": [],
  "quality_warnings": []
}
Output JSON only.
""".strip(),
    ]
)


def build_agent_4_user_prompt(
    *,
    resume_json: str,
    job_profile_compact: str,
    evidence_compact: str,
    genuine_gaps: str,
    page_fit_report: str,
    ats_report: str,
) -> str:
    return (
        "Review the FINAL validated resume for hiring fit, ATS, and one-page readiness.\n"
        "Scores must reflect this resume — not the original job-card match score.\n\n"
        f"=== FINAL RESUME ===\n{resume_json}\n\n"
        f"=== JOB PROFILE (compact) ===\n{job_profile_compact}\n\n"
        f"=== EVIDENCE MAP (compact) ===\n{evidence_compact}\n\n"
        f"=== GENUINE GAPS ===\n{genuine_gaps}\n\n"
        f"=== DETERMINISTIC PAGE-FIT REPORT ===\n{page_fit_report}\n\n"
        f"=== DETERMINISTIC ATS REPORT ===\n{ats_report}\n"
    )


def merged_prompt_contains_legacy_rules() -> dict[str, bool]:
    """Test helper — ensure old prompt rules remain represented."""
    return {
        "job_extraction": "hard_requirements" in AGENT_1_SYSTEM
        and "Do not hard-code software-only" in AGENT_1_SYSTEM,
        "semantic_inference": "Strongly Inferred" in AGENT_1_SYSTEM
        and "FORBIDDEN" in AGENT_1_SYSTEM,
        "deep_tailor": "NEVER invent employers" in AGENT_2_SYSTEM
        and "NEVER move a technology" in AGENT_2_SYSTEM,
        "content_triage": "Preserve | Rewrite | Reorder" in AGENT_2_SYSTEM
        or "Preserve | Rewrite | Reorder | Expand | Condense | Remove"
        in AGENT_2_SYSTEM,
        "human_writer": "FACTS ARE IMMUTABLE" in AGENT_3_SYSTEM
        and "15-SECOND RULE" in AGENT_3_SYSTEM,
        "recruiter": "SENIOR RECRUITER" in AGENT_3_SYSTEM
        or "recruiter" in AGENT_3_SYSTEM.lower(),
        "claim_validation": "validation_warnings" in AGENT_3_SYSTEM,
        "one_page": "One-page enforcement remains mandatory" in AGENT_4_SYSTEM,
        "philosophy": "INTERVIEW-PROBABILITY" in AGENT_1_SYSTEM,
    }
