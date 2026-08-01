# Pipeline metrics — baseline vs single-agent

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

## Intermediate (four_agent_v2_0)

| Call | Merged agent |
|---|---|
| 1 | Candidate & Opportunity Intelligence (job + inference) |
| 2 | Strategy & Content Selection (composed rewrite) |
| 3 | Human Writing & Credibility Review (writer + recruiter) |
| — | Final Hiring / ATS / One-page — deterministic (0 LLM) |

**Typical primary LLM calls: 3 (cap 4)**  
**UI stages: 4**

## After (single_agent_v1_0)

| Call | Stage |
|---|---|
| — | Prepare evidence — parse resume/JD, normalize, evidence, strategy (code) |
| 1 | **Resume Generation Agent** — merged prompts, final tailored_resume |
| — | Final Hiring / ATS / One-page / dedupe — deterministic (0 LLM) |

**Typical primary LLM calls: 1**  
**UI stages: 3**

## Expected impact (structural)

| Metric | Before (11-agent) | Four-agent | Single-agent | Delta vs four-agent |
|---|---|---|---|---|
| Primary LLM calls | 6–10 | 3–4 | **1** | ≥66% fewer |
| Sequential LLM RTTs | 6+ | 3 | **1** | target ≥66% lower latency |
| Token usage | separate stages | composed ×3 | composed ×1 | target ≥50% lower |
| Truthfulness gates | unchanged | unchanged | unchanged + dedupe/education fixes | no intentional weakening |

Live production comparison should record `pipeline_metrics` on each generation
(`primary_llm_calls`, `duration_ms`, `cache_hits`, `knowledge_cache_hit`).
