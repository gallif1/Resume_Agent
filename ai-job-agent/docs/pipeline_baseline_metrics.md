# Pipeline metrics — baseline vs single smart agent

Measured from code-path accounting (primary LLM call sites), not a live OpenAI run
in this environment (no API key in the cloud agent sandbox).

## Before (multi_agent_v1_4 — 11 UI stages)

Typical happy-path primary LLM stage calls: **6–10**  
UI stages: **11**

## Intermediate (four_agent_v2_0)

| Call | Merged agent |
|---|---|
| 1 | Candidate & Opportunity Intelligence |
| 2 | Strategy & Content Selection |
| 3 | Human Writing & Credibility Review |
| — | Final Hiring / ATS / One-page — deterministic |

**Typical primary LLM calls: 3**  
**UI stages: 4**

## After (single_agent_v1_0)

| Call | Agent |
|---|---|
| 1 | Smart Resume Agent (GPT-5) — content selection + writing + recruiter self-review |
| — | Deterministic prep (knowledge, ontology job/evidence, strategy) |
| — | Deterministic post (claims, hiring manager, ATS, one-page) |

**Typical primary LLM calls: 1**  
**UI stages: 1**  
**Default model: `gpt-5` (`OPENAI_TAILOR_MODEL`)**
