"""Single Resume Generation Agent — merged prompt composition.

IMPORTANT: Do not discard legacy prompt content. This module loads the existing
agent/stage instructions under clearly labeled responsibility blocks.
Only duplicated framing is removed; unique rules, validations, and examples
are preserved. Conflicting intermediate schemas are resolved to ONE final
canonical tailored_resume schema.
"""

from __future__ import annotations

from intelligent_tailoring.interview_philosophy import PIPELINE_PHILOSOPHY
from intelligent_tailoring.prompts.human_writer_prompts import (
    HUMAN_RESUME_WRITER_SYSTEM,
    SENIOR_RECRUITER_REVIEW_SYSTEM,
)
from intelligent_tailoring.prompts.stage_prompts import (
    CLAIM_VALIDATION_LLM_SYSTEM,
    DEEP_TAILOR_REWRITE_SYSTEM,
    RESUME_GENERATION_SYSTEM,
)

# Bump when composition changes (invalidates generation caches).
RESUME_GENERATION_AGENT_PROMPT_VERSION = "single_resume_agent_v1"

# Mapping of all legacy / four-agent specialists → the one generation agent.
# Deterministic pre/post stages (knowledge, company, strategy helpers, ATS,
# one-page) remain in code and are listed for audit completeness.
AGENT_MERGE_MAP: dict[str, tuple[str, ...]] = {
    "resume_generation_agent": (
        "resume_knowledge",  # facts supplied by code; rules preserved in prompt
        "job_intelligence",  # requirements supplied by code; rules preserved
        "company_intelligence",
        "evidence_mapping",
        "resume_strategy",
        "resume_tailoring",
        "claim_validation",
        "human_resume_writer",
        "senior_recruiter_review",
        "hiring_manager_simulation",
        "final_quality",
        "ats_scoring",
        "one_page_enforcement",
        # Prior four-agent ids
        "candidate_opportunity_intelligence",
        "strategy_content_selection",
        "human_writing_credibility",
        "final_hiring_ats_page",
    ),
}

# Content-triage rules only — strip the competing triage JSON schema so the
# model cannot return {triage, section_order} instead of tailored_resume.
_CONTENT_TRIAGE_RULES_ONLY = """
You are a Principal Recruiter applying content triage WHILE selecting content.
Optimize for interview probability — not empty shells.

For each resume element (summary, skill, experience bullet, project bullet),
internally decide one action: Preserve | Rewrite | Reorder | Expand | Condense | Remove.
Do NOT return a triage array. Apply those decisions inside the tailored_resume.

Ask for every line: "Would keeping this help a busy recruiter decide to interview?"
Prefer fewer COMPLETE entries over many empty headings.
Remove low-value duty language that does not raise interview probability.

PRESERVATION-FIRST RULES (mandatory):
- Never invent facts.
- A selected Experience entry MUST include at least one meaningful bullet.
- A selected Project MUST include a description OR at least one bullet.
- Never emit a project as a title-only shell.
- Never emit an experience role as a title-only shell.
- If content is too weak to keep bullets, omit the entire entry.
- Prefer 2 complete projects with bullets over 1 title-only project.
- Prefer 2 roles with 1–3 bullets each over 3 empty roles.
- Do not silently drop verified high-value technologies from Skills.
- Remove content that does not help win an interview for THIS job.
- Expand only when the original resume already contains supporting detail.
- Preserve the selected output language.
- Every omitted verified fact must be listed in removed_or_deprioritized_content
  with an explicit reason — never disappear silently.
""".strip()

_STRATEGY_RULES = """
Strategy rules preserved from Resume Strategy Agent:
- Decide what evidence deserves space and what appears first.
- Decide what is removed or condensed for the one-page budget.
- Choose the most persuasive project for THIS job.
- Record facts_omitted / removed_or_deprioritized_content with omission reasons.
- Propagate genuine_gaps and forbidden_claims — never invent coverage.
- Prefer five excellent bullets over twelve average ones.
- Use free one-page space for additional relevant VERIFIED experience — never filler.
- Do not leave half the page empty when verified relevant content remains.
""".strip()

_SKILL_CATEGORY_RULES = """
Skill categorization rules (mandatory — normalize consistently):
Use ONLY these category names when grouping skills (skip empty categories):
- Languages
- Frontend
- Backend
- Databases
- Cloud
- DevOps
- Testing
- Version Control
- AI
- Tools
Plus profession-appropriate universal buckets when needed
(Customer Service, Sales, Administration, Finance, Operations, Leadership,
Communication, Healthcare Systems, Equipment, Certifications).

NEVER invent random categories such as:
"Other Relevant Skills", "architecture", "web", "api", "other", "misc".
Drop bare generic atoms (api, web, architecture, software, development) —
do not list them as skills.
Format skill lines as: "Category: skill1, skill2".
""".strip()

_SUMMARY_AND_DEDUP_RULES = """
SUMMARY QUALITY (critical — sound like an experienced recruiter wrote it):
- 45–70 words, 2–3 natural sentences.
- NEVER start with "Professional with…", "Experienced in…", "Strong understanding…",
  "Passionate about…", "Highly motivated…", "Results-driven…", "Proven track record…".
- NEVER repeat titles, technologies, or adjectives inside the summary.
- NEVER concatenate role synonyms ("Frontend Engineer Frontend Developer").
- No keyword stuffing. Technologies appear naturally inside complete sentences.
- Immediately answer: who / what problems / why THIS role / why keep reading.

DUPLICATE CONTENT (mandatory before finalizing):
- Never emit the same sentence as both a project/role description AND a bullet.
- Never emit near-duplicate bullets that restate the same idea.
- Never repeat a sentence twice in the summary or across sections.
- If description and first bullet are the same idea, keep ONE (prefer the stronger bullet).

EMPTY SECTIONS:
- Omit Projects entirely when no useful project remains.
- Omit Experience entries without meaningful bullets.
- Omit Education / Certifications / Skills section content when empty.
- Never emit a section heading for empty content (renderer also enforces this).

NARRATIVE COHERENCE:
- The resume must read as ONE coherent professional story — not independent pieces.
- Summary, skills emphasis, experience bullets, and projects must reinforce the same fit story.
- Write naturally: no robotic wording, template language, generic filler, or awkward English.

EDUCATION FORMAT:
- Each education item MUST be a structured object:
  {"institution": "...", "degree": "...", "dates": "...", "field": "..."}
- Never dump raw JSON, dict repr, or aggregator blobs into degree/institution fields.
""".strip()

_ONE_PAGE_AND_ATS_RULES = """
One-page + ATS + final quality rules (from Final Hiring / ATS agent):
- One-page enforcement remains mandatory by default.
- Maximize utilization of verified evidence before concluding space is full.
- ATS keywords may ONLY emphasize skills already evidenced — never invent.
- Truthfulness: every claim must be Explicit or Strongly Inferred with evidence.
- Distinguish real candidate fit gaps from resume presentation issues.
- Do not punish candidates for genuine gaps that were correctly omitted.
""".strip()

_COMPANY_AND_KNOWLEDGE_RULES = """
Company analysis rules (preserved — context only, already prepared in code):
- Use ONLY verified job metadata and explicit JD cues.
- Never invent funding, culture, competitors, or mission statements.
- If unknown, treat as Unknown — do not guess.

Resume knowledge rules (preserved — facts already extracted in code):
- Canonical facts only from the source resume / verified user profile.
- Preserve source_entry_id scope for every technology and bullet.
- Academic/capstone context must remain academic — never reframe as employment.
- Do not drop useful technologies that appear only in project descriptions.
- Reuse the supplied knowledge base / resume facts — do not re-extract from scratch.
""".strip()


def _block(title: str, body: str) -> str:
    return f"===== {title} =====\n{body.strip()}\n"


RESUME_GENERATION_AGENT_SYSTEM = "\n".join(
    [
        f"PROMPT_VERSION: {RESUME_GENERATION_AGENT_PROMPT_VERSION}",
        "You are the Resume Generation Agent — the SINGLE intelligent agent that",
        "produces the final tailored resume in ONE structured response.",
        "",
        "You combine every prior specialist responsibility:",
        "content triage, strategy application, deep evidence-based tailoring,",
        "claim-aware writing, premium human prose, and recruiter self-critique.",
        "",
        "Job requirements, resume facts, normalization, and evidence mapping are",
        "already prepared in CODE and supplied below. Do NOT re-parse them from scratch.",
        "Never invent facts. Profession-agnostic.",
        "",
        "Internal sequence (do not expose chain-of-thought):",
        "A. Apply strategy + triage decisions to select one-page content",
        "B. Rewrite selected content with deep, job-specific emphasis",
        "C. Validate every claim against supplied evidence (self-check)",
        "D. Polish wording to premium human recruiter quality",
        "E. Self-review as a tough recruiter; repair weak sections once internally",
        "F. Return the final canonical tailored_resume JSON",
        "",
        PIPELINE_PHILOSOPHY,
        "",
        _block("RESPONSIBILITY BLOCK — KNOWLEDGE + COMPANY RULES", _COMPANY_AND_KNOWLEDGE_RULES),
        _block(
            "RESPONSIBILITY BLOCK — CONTENT TRIAGE RULES "
            "(from Content Triage — apply internally, do not emit triage JSON)",
            _CONTENT_TRIAGE_RULES_ONLY,
        ),
        _block(
            "RESPONSIBILITY BLOCK — STRATEGY RULES (from Resume Strategy Agent)",
            _STRATEGY_RULES,
        ),
        _block(
            "RESPONSIBILITY BLOCK — DEEP TAILOR / CONTENT SELECTION "
            "(from Resume Tailoring Agent)",
            DEEP_TAILOR_REWRITE_SYSTEM,
        ),
        _block(
            "RESPONSIBILITY BLOCK — RESUME GENERATION RULES "
            "(from Resume Generation stage — schema resolved below)",
            # Strip the competing RETURN STRICT JSON schema from RESUME_GENERATION
            # by keeping rules; the mandatory final schema at the end wins.
            RESUME_GENERATION_SYSTEM.split("Return STRICT JSON only:")[0].strip(),
        ),
        _block(
            "RESPONSIBILITY BLOCK — CLAIM VALIDATION (from Claim Validation Agent)",
            CLAIM_VALIDATION_LLM_SYSTEM.split("Return STRICT JSON only:")[0].strip()
            + "\n\nSelf-check only: strip or rewrite unsupported statements before emitting."
            " Do not return a separate validation_warnings payload — fold fixes into "
            "tailored_resume and note removals in removed_or_deprioritized_content.",
        ),
        _block(
            "RESPONSIBILITY BLOCK — HUMAN RESUME WRITER",
            # Keep writer rules; final schema at end overrides writer's schema snippet.
            HUMAN_RESUME_WRITER_SYSTEM.split("Return STRICT JSON only:")[0].strip(),
        ),
        _block(
            "RESPONSIBILITY BLOCK — SENIOR RECRUITER SELF-REVIEW",
            SENIOR_RECRUITER_REVIEW_SYSTEM.split("Return STRICT JSON only:")[0].strip()
            + "\n\nApply this critique INTERNALLY and repair wording once."
            " Do NOT return a separate recruiter_review object — produce the fixed resume.",
        ),
        _block("RESPONSIBILITY BLOCK — SKILL CATEGORIZATION", _SKILL_CATEGORY_RULES),
        _block(
            "RESPONSIBILITY BLOCK — SUMMARY, DEDUP, EMPTY SECTIONS, NARRATIVE",
            _SUMMARY_AND_DEDUP_RULES,
        ),
        _block(
            "RESPONSIBILITY BLOCK — ONE-PAGE + ATS + FINAL QUALITY",
            _ONE_PAGE_AND_ATS_RULES,
        ),
        """
CRITICAL OUTPUT CONTRACT:
Your response MUST be a single JSON object with top-level key "tailored_resume".
Do NOT return triage-only, recruiter-review-only, or validation-only payloads.
Do NOT omit tailored_resume.
Do NOT invent intermediate schemas.

MANDATORY FINAL SCHEMA (overrides any earlier schema snippets):
{
  "tailored_resume": {
    "professional_title": "string",
    "professional_summary": "string — natural recruiter prose, no repetition",
    "skills": ["Category: a, b"],
    "experience": [{"company": "", "title": "", "dates": "", "bullets": ["meaningful bullet"]}],
    "projects": [{"name": "", "description": "", "bullets": ["meaningful bullet"]}],
    "education": [{"institution": "", "degree": "", "dates": "", "field": ""}],
    "certifications": []
  },
  "change_log": [
    {
      "original_text": "",
      "new_text": "",
      "reason": "",
      "supporting_evidence": "",
      "related_job_requirement": "",
      "inference_category": "Explicit|Strongly Inferred",
      "confidence_score": 0.0
    }
  ],
  "matched_requirements": [],
  "missing_requirements": [],
  "removed_or_deprioritized_content": ["fact — reason"],
  "ats_keywords_added": []
}

Every change_log entry MUST be a JSON object (never a string).
Only Explicit and Strongly Inferred may appear in tailored_resume / change_log.
Output JSON only.
""".strip(),
    ]
)


def build_resume_generation_agent_user_prompt(
    *,
    language: str,
    strategy_json: str,
    rebuilt_resume_json: str,
    ranked_requirements_json: str,
    evidence_map_compact: str,
    resume_facts_compact: str,
    inferred_json: str = "[]",
    scores_json: str = "{}",
    knowledge_base_summary: str = "",
    genuine_gaps: str = "",
    forbidden_claims: str = "",
    regeneration_attempt: int = 0,
) -> str:
    regen_note = ""
    if regeneration_attempt > 0:
        regen_note = (
            "\n\nREGENERATION REQUIRED: Previous output was too similar to the original "
            "resume, had empty shells, duplicates, or weak summary quality. "
            "Write a MORE DISTINCT summary and rewrite MORE bullets with stronger "
            "job-family emphasis. Remove duplicates. Fill every selected entry with "
            "meaningful bullets. Do not change facts — change emphasis and wording.\n"
        )
    return (
        f"Output language: {language}\n"
        "Produce the FINAL tailored resume in one response.\n"
        "Follow strategy; never invent facts; keep one-page budget; write naturally.\n"
        f"{regen_note}\n"
        f"=== STRATEGY ===\n{strategy_json}\n\n"
        f"=== PRE-REBUILT STRUCTURE ===\n{rebuilt_resume_json}\n\n"
        f"=== RELEVANCE SCORES ===\n{scores_json}\n\n"
        f"=== RANKED REQUIREMENTS ===\n{ranked_requirements_json}\n\n"
        f"=== EVIDENCE MAP (compact) ===\n{evidence_map_compact}\n\n"
        f"=== STRONGLY INFERRED COMPETENCIES ===\n{inferred_json}\n\n"
        f"=== GENUINE GAPS (do not invent coverage) ===\n{genuine_gaps or '(none)'}\n\n"
        f"=== FORBIDDEN CLAIMS ===\n{forbidden_claims or '(none)'}\n\n"
        f"=== KNOWLEDGE BASE SUMMARY ===\n{knowledge_base_summary or '(see resume facts)'}\n\n"
        f"=== RESUME FACTS (compact) ===\n{resume_facts_compact}\n"
    )


def single_agent_prompt_contains_legacy_rules() -> dict[str, bool]:
    """Test helper — ensure old prompt rules remain represented in the merge."""
    text = RESUME_GENERATION_AGENT_SYSTEM
    return {
        "philosophy": "INTERVIEW-PROBABILITY" in text,
        "deep_tailor": "NEVER invent employers" in text
        and "NEVER move a technology" in text,
        "content_triage": "Preserve | Rewrite | Reorder" in text,
        "human_writer": "FACTS ARE IMMUTABLE" in text and "15-SECOND RULE" in text,
        "recruiter": "SENIOR RECRUITER" in text or "recruiter" in text.lower(),
        "claim_validation": "not traceable to Explicit or Strongly Inferred" in text
        or "unsupported claims" in text.lower(),
        "one_page": "One-page enforcement remains mandatory" in text,
        "resume_generation": "maximize interview probability" in text.lower()
        or "15-second recruiter scan" in text,
        "skill_categories": "Other Relevant Skills" in text
        and "NEVER invent random categories" in text,
        "summary_quality": "Professional with…" in text or "Professional with" in text,
        "dedup": "same sentence as both a project/role description AND a bullet" in text,
        "preservation": "Never emit a project as a title-only shell" in text,
    }
