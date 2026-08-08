# Structured Output + Deterministic Validation Layer

## Why

Prompt-only fixes in the 4-agent resume pipeline repeatedly regressed (missing
entries, raw dict leaks, contact drops, sparse weak-match output). Content
agents now emit structured JSON, and a **non-LLM** validation step runs after
every content-producing agent before handoff.

## Agents that produce structured JSON

| Agent | Role | Structured output |
|---|---|---|
| Resume extraction / knowledge (deterministic) | Base parse | Stamps stable `id` / `source_entry_id` on every experience & project; normalizes `contact` |
| **Agent 2 — Strategy & Content Selection** | Selection / reorder / rephrase | `tailored_resume` JSON with stable ids + contact |
| **Agent 3 — Human Writing & Credibility** | Prose polish | Same structured resume; must not drop/merge/invent ids |
| Agent 4 — Final Hiring / ATS / page | Deterministic format/score | Renders validated JSON only — does not alter entries |

## Schema (canonical)

See `structured_resume.py` (`STRUCTURED_RESUME_SCHEMA_VERSION = structured_resume_v1`):

- `contact` (location, phone, email, github, linkedin)
- `title` / `summary`
- `experience[]` with stable `id`, `position`, `organization`, `dateRange`, `bullets[]`
- `projects[]` with stable `id`, `title`, `description`, `bullets[]`
- `skills` as `{ category: [atoms] }` (also accepts categorized display lines)
- `education[]`

Pipeline renderers still consume the legacy field names (`title`/`company`/`name`);
conversion is bidirectional via `to_structured_resume` / `structured_to_pipeline_resume`.

## Validation checks (deterministic)

Implemented in `structured_validation.py`, run after Agent 2, Agent 3, and before
final formatting:

1. **Missing stable ids** — every base `experience` / `project` id must be present
2. **Raw data in strings** — reject `{...}`, list reprs, `"key":` leaks inside prose
3. **Duplicate entries** — duplicate `position+organization` or project `title` / ids
4. **Near-duplicate bullets** — >85% similarity within entry, across entries, or vs summary
5. **Contact links** — github / linkedin / email / phone present when in base resume
6. **Summary grammar** — complete sentences; reject competing lead-ins / fragment merges
7. **Content fullness** — tailored content units must be ≥ ~80% of base bullet volume
8. **Job-posting contamination** — reject Summary/bullet text that reuses JD n-grams
   (5+ words) or employer voice (second-person / motivational slogans). See
   `jd_contamination.py`. JD text may guide emphasis/keywords only — never become
   candidate claims. Prompts wrap inputs in `<candidate_facts>` vs `<job_posting>`.
9. **Summary candidate-only sanity** — summary must describe what the candidate
   did/knows; instructional/opinion fragments directed at someone else fail.

## Regeneration on failure

`call_stage_json_with_content_validation` (`llm_utils.py`):

1. Call the agent (JSON + schema validate, with the existing one-shot JSON retry)
2. Run deterministic content validation
3. On failure, **re-invoke once** with the specific `ValidationReport.feedback_for_agent()`
   lines (not a vague “try again”)
4. If still failing, apply `repair_structured_resume` (restore missing ids/content,
   sanitize raw fields, preserve contact) so downstream formatting never sees a
   sparse or corrupted payload

## Weak-match fullness

Agent 2 prompts now state that relevance affects **ordering / emphasis / phrasing
only** — never whole-entry exclusion. Weak matches must generalize transferable
skills honestly and write fuller bullets. The 80% volume check enforces this.
