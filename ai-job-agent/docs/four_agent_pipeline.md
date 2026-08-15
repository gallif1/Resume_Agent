# Single-Agent Resume Pipeline

## Architecture

Eleven sequential specialist stages (and the prior four merged LLM agents) are
now driven by **one primary GPT-5 smart agent**. Legacy specialist modules
remain as internal helpers, validators, and schema builders — they no longer
each require a separate sequential LLM call.

| Phase | Work | Primary LLM calls |
|---|---|---|
| Deterministic prep | Resume Knowledge, Job/Company/Evidence (ontology), Strategy, Triage | **0** |
| Smart Resume Agent | Content selection + polished writing + recruiter self-review | **1** (GPT-5) |
| Deterministic post | Claim Validation, Hiring Manager, ATS, One-page | **0** (optional ≤1 targeted repair) |

**Normal generation budget: 1 primary LLM call.**

## Model

Default tailor model: `OPENAI_TAILOR_MODEL=gpt-5` (see `config.py`).

## Prompt composition

The smart-agent prompt lives in `intelligent_tailoring/prompts/merged_prompts.py`
as `SMART_AGENT_SYSTEM`. It loads prior Agent 2 + Agent 3 responsibility blocks
under labeled sections. Unique rules are preserved; only duplicated framing is
removed.

## Caching

- `ResumeKnowledgeBase` — by resume content hash + parser + ontology version
- `JobProfile` raw requirements — by company + title + JD hash
- `CompanyProfile` — by company id + metadata hash + prompt version
- Full-result cache remains (resume hash + JD hash + language + pipeline version)

## Preview vs export

- Generation / preview **never** calls `assert_safe_to_export`
- `GET .../tailored-cv/preview` loads markdown + gate severity for review mode
- `GET .../download-pdf|docx` and `POST .../export` enforce critical gates
- Critical failures: preview opens in review mode; download disabled
- Warnings: preview + download allowed

## Firebase / cross-entry fix

`quality_gates` now uses shared `_resolve_project_entry_id` with soft project-name
matching and canonical skill ids. Renamed titles such as
`Restaurant Menu Ordering App` resolve to source `Restaurant App` / `project_N`,
so Firebase is accepted when evidenced on that entry.
