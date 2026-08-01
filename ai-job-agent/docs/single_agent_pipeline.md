# Single Resume Generation Agent

## Architecture

All prior multi-agent / four-agent LLM stages are merged into **one** primary
LLM call. Deterministic prep and validation stay in code.

```
Parse Resume (code)
        ↓
Parse Job Description (code)
        ↓
Normalize Facts (code)
        ↓
Collect Supporting Evidence (code)
        ↓
ONE LLM CALL — Resume Generation Agent
        ↓
Structured Resume JSON
        ↓
Claim / ATS / One-page validation (code)
        ↓
HTML Renderer → PDF Renderer
```

| Stage | Implementation | Primary LLM |
|---|---|---|
| Prepare evidence | Resume knowledge, deterministic JD parse, normalize, evidence map, strategy | **0** |
| Resume Generation Agent | Merged prompts from every prior specialist | **1** |
| Final hiring / ATS / one-page | Hiring manager simulation, ATS rescore, one-page compress, dedupe | **0** |

**Normal generation budget: 1 primary LLM call.**

## Prompt composition

Merged prompt: `intelligent_tailoring/prompts/resume_generation_agent_prompts.py`

It loads existing stage/agent instructions under labeled responsibility blocks
(deep tailor, triage rules, human writer, claim validation, recruiter review,
skill categories, summary/dedup, one-page/ATS). Competing intermediate JSON
schemas are removed; one canonical `tailored_resume` schema remains.

Historical four-agent compositions remain in `prompts/merged_prompts.py` for
audit; production uses the single-agent prompt.

## Regression fixes included

1. **Duplicate content** — `content_deduper.dedupe_resume_content` before render
2. **Summary quality** — anti-repetition rules in prompt + deterministic summary builder
3. **Education JSON** — `education_normalize` flattens aggregator dicts; renderer never prints raw JSON
4. **Empty sections** — title-only shells dropped; empty section headings omitted
5. **Skill categories** — stable taxonomy (Languages, Frontend, Backend, Databases, Cloud, DevOps, Testing, Version Control, AI, Tools); no `Other Relevant Skills`
6. **Page utilization** — preserve/restore verified content when underfilled; still one page
7. **Omitted facts** — `removed_or_deprioritized_content` / coverage report require explicit reasons

## Pipeline version

`PIPELINE_VERSION = "single_agent_v1_0"`
