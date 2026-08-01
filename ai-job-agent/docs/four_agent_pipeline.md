# Four-Agent Resume Pipeline (superseded)

> **Superseded by** [`single_agent_pipeline.md`](./single_agent_pipeline.md)
> (`PIPELINE_VERSION = single_agent_v1_0`). The four-agent design below is kept
> as historical context; production now uses **one** Resume Generation Agent.

## Architecture (historical)

Eleven sequential specialist stages were merged into **four primary LLM agents**.
Legacy specialist modules remain as internal helpers, validators, and schema
builders.

| Merged agent | Legacy specialists | Primary LLM calls |
|---|---|---|
| 1. Candidate & Opportunity Intelligence | Resume Knowledge, Job Intelligence, Company Intelligence, Evidence Mapping | **1** (job + inference). Knowledge + company are deterministic / cached. |
| 2. Strategy & Content Selection | Resume Strategy, Resume Tailoring (+ triage rules) | **1** (composed deep-tailor rewrite). Strategy + triage are deterministic. |
| 3. Human Writing & Credibility Review | Claim Validation, Human Writer, Senior Recruiter | **1** (composed write+review). Claim validation deterministic. ≤2 repair passes. |
| 4. Final Hiring, ATS & One-Page | Hiring Manager, Final Quality, ATS, One-page | **0** (deterministic). ≤1 targeted Agent-3 section retry. |

**Historical budget: ≤ 4 primary LLM calls (typically 3).**  
**Current budget: 1 primary LLM call** — see `single_agent_pipeline.md`.

## Prompt composition

Historical four-agent prompts: `intelligent_tailoring/prompts/merged_prompts.py`.  
Production single-agent prompt: `prompts/resume_generation_agent_prompts.py`.
Each loads the existing stage/agent instructions under labeled responsibility
blocks. Unique rules are preserved; only duplicated framing is removed.

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
