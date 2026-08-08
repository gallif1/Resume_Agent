# Four-Agent Content-Loss Regression Fix

## Root cause

Content disappeared **before** polished rewriting — mainly at structured intake and
merge/render boundaries:

1. **Extraction mismatch** — aggregator profiles store `master_profile.work_experience[].bullet_points`
   and string `projects`, while the pipeline only read `experience.roles` (usually empty)
   and skipped non-dict projects. Result: empty `experience_roles` / title-only projects.
2. **Agent 2 merge** — when the LLM returned empty `bullets: []`, merge kept the empty
   arrays instead of restoring rebuilt source bullets.
3. **One-page compressor + renderer** — kept title-only shells; PDF skill hints labeled
   `React` as Backend; generic atoms like `architecture` survived under Other.
4. **Summary** — role synonyms could concatenate (`Frontend Engineer Frontend Developer`).

## Stage where facts disappeared

| Stage | Loss mode |
|---|---|
| ResumeKnowledge / extract_structured_resume | Primary — bullets never loaded |
| Agent 2 rewrite merge | Secondary — empty LLM arrays preserved |
| One-page + markdown/PDF render | Tertiary — empty shells + wrong skill buckets |

## Fixes

- Canonical inventory + preservation helpers (`canonical_resume.py`)
- Extraction from `master_profile`, section text, and string projects
- Merge restores bullets/descriptions from rebuilt source
- Completeness + density repair before final gates
- Deterministic skill taxonomy drops (`architecture`, etc.)
- PDF React/Expo/Next → Frontend
- Summary synonym collapse
- Renderer skips empty experience/project shells
- Agent 2 prompt v3 preservation rules + cache bump

## Tests

`tests/test_four_agent_content_preservation.py` freezes the screenshot failure mode.

## Follow-on: structured validation layer

Prompt-only repairs kept regressing. The pipeline now enforces a structured
resume schema with stable entry ids and a deterministic (non-LLM) validation
gate after Agents 2 and 3 — see `docs/structured_validation_layer.md` and
`tests/test_structured_validation_layer.py`.
