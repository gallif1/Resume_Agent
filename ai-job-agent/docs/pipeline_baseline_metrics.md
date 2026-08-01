# Pipeline metrics — baseline vs four-agent

Measured from code-path accounting (primary LLM call sites), not a live OpenAI run
in this environment (no API key in the cloud agent sandbox).

## Before (multi_agent_v1_4 — 11 UI stages)

Typical happy-path primary LLM stage calls:

| Call | Stage |
|---|---|
| 1 | Job requirement extraction |
| 2 | Semantic inference |
| 3 | Content triage |
| 4 | Deep tailor rewrite |
| 5 | Human resume writer |
| 6 | Senior recruiter review |
| + | Optional refine loops (recruiter / HM / polish) — often +2–4 |

**Typical primary LLM calls: 6–10**  
**UI stages: 11**

## After (four_agent_v2_0)

| Call | Merged agent |
|---|---|
| 1 | Candidate & Opportunity Intelligence (job + inference) |
| 2 | Strategy & Content Selection (composed rewrite; triage deterministic) |
| 3 | Human Writing & Credibility Review (writer + recruiter) |
| — | Final Hiring / ATS / One-page — deterministic (0 LLM) |
| ≤1 | Targeted section repair from Agent 4 (not a new primary agent) |

**Typical primary LLM calls: 3 (cap 4)**  
**UI stages: 4**

## Expected impact (structural)

| Metric | Before | After | Delta |
|---|---|---|---|
| Primary LLM calls | 6–10 | 3–4 | ≥50% fewer |
| Median latency | dominated by 6+ sequential LLM RTTs | 3 sequential LLM RTTs | target ≥50% lower |
| Token usage | triage + inference + writer + recruiter separate | composed prompts, compact KB transfer | target ≥40% lower where practical |
| Truthfulness gates | unchanged rules | same rules + fixed Firebase provenance | no intentional weakening |

Live production comparison should record `pipeline_metrics` on each generation
(`primary_llm_calls`, `duration_ms`, `cache_hits`, `knowledge_cache_hit`).
