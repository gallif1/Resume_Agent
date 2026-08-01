# AI Resume Intelligence Platform — Multi-Agent Architecture

## Vision

The system does not simply rewrite resumes. It thinks like a team of specialists.
Each agent has exactly one responsibility. Inter-agent communication uses
structured objects, not free-form prompts.

Pipeline version: `multi_agent_v1_2`

## Writing quality layer (same agents, smarter behavior)

Quality improvements live inside existing stages — no additional agents:

- **Evidence amplifier** — inventories experience/projects, expands thin projects from same-entry KB facts, propagates important evidenced skills
- **Summary builder** — role-fit narrative (~58 words); bans AI filler lead-ins
- **Tech weaver** — integrates evidenced technologies into Experience/Projects bullets
- **One-page compressor** — relevance-ranked compression (roles/bullets/skills) so every resume fits one A4 page by default
- **Dynamic skill taxonomy order** — Backend/Frontend/Data/Sales/Healthcare/… category priority
- **Human writer + recruiter prompts** — tougher anti-AI standards; project storytelling; cross-section reinforcement
- **Hiring Manager refine loop** — actionable challenges feed one wording-only refine pass
- **Resume Quality Score** — naturalness, evidence utilization, job relevance, ATS, human writing, clarity, balance, role differentiation, one-page fit; regenerates weak sections below threshold
- **Premium ATS themes** — tighter professional spacing without shrinking fonts below readability

## Data flow

```
CV + Job
   │
   ▼
[1] Resume Knowledge Agent ──► ResumeKnowledgeBase / resume_facts
   │
   ▼
[2] Job Intelligence Agent ───► JobProfile
   │
   ▼
[3] Company Intelligence Agent ► CompanyProfile  (Unknown stays Unknown)
   │
   ▼
Normalization + Semantic Inference (deterministic / ontology tools)
   │
   ▼
[4] Evidence Mapping Agent ───► EvidenceMap
   │                              (Explicit / Strong / Weak / No Evidence
   │                               + allowed/forbidden wording)
   ▼
Ranking + Content Triage (tools)
   │
   ▼
[5] Resume Strategy Agent ────► ResumeStrategy  (no writing)
   │
   ▼
Rebuild / Score (tools)
   │
   ▼
[6] Resume Tailoring Agent ───► TailoredStructure  (content selection only)
   │
   ▼
[7] Claim Validation Agent ───► ClaimValidationResult
   │                              (Accept / Rewrite / Regenerate / Reject)
   ▼
Scope + Linguistic + Quality Gates (tools)
   │
   ▼
[8] Human Resume Writer ──────► wording-only polish (facts locked)
   │
   ▼
[9] Senior Recruiter Review ──► structured interview-readiness feedback
   │
   ▼
[10] Hiring Manager Simulation ► job-specific scores + actionable feedback
   │
   ▼
ATS rescore + reports + optional Quality Intelligence metrics
   │
   ▼
Markdown / PDF (ATS themes) / DOCX export
```

## Agent responsibilities

| # | Agent | Input | Output | Must never |
|---|-------|-------|--------|------------|
| 1 | Resume Knowledge | CV profile + source docs | `ResumeKnowledgeBase` | Generate, tailor, infer |
| 2 | Job Intelligence | Job + JD snapshot | `JobProfile` | Generate resume text |
| 3 | Company Intelligence | Job + JD + metadata | `CompanyProfile` | Fabricate facts |
| 4 | Evidence Mapping | Facts + JobProfile + inferences | `EvidenceMap` | Write resume prose |
| 5 | Resume Strategy | Profiles + EvidenceMap | `ResumeStrategy` | Write resume text |
| 6 | Resume Tailoring | All structured outputs | `TailoredStructure` | Invent facts / polish wording |
| 7 | Claim Validation | Resume + EvidenceMap | Decisions + cleaned resume | Delete mid-sentence words |
| 8 | Human Resume Writer | Validated resume | Polished resume | Change facts |
| 9 | Senior Recruiter | Resume | Review feedback | Modify facts |
| 10 | Hiring Manager Sim | Resume + profiles + evidence | Feedback scores | Modify resume |

## Key modules

- `intelligent_tailoring/agents/` — agent implementations, schemas, orchestrator
- `intelligent_tailoring/pipeline.py` — production `run_intelligent_tailoring` (multi-agent)
- `intelligent_tailoring/stages/` — reusable stage tools called by agents
- `intelligent_tailoring/services/` — strategy, rewrite, writer, scorer helpers
- `intelligent_tailoring/writing/` — fact lock, grammar, style, AI detector
- `intelligent_tailoring/themes/` — ATS-safe PDF themes
- `intelligent_tailoring/agents/quality_intelligence.py` — anonymous metrics only

## Schemas

Defined in `intelligent_tailoring/agents/schemas.py`:

- `ResumeKnowledgeOutput`
- `JobProfile` (+ `ScoredRequirement`)
- `CompanyProfile`
- `EvidenceMap` / `EvidenceMapping`
- `ResumeStrategy`
- `TailoredStructure`
- `ClaimValidationResult` / `ClaimValidationItem`
- `HumanWriterOutput`
- `RecruiterReviewOutput`
- `HiringManagerFeedback`

Legacy dual-schema fields (`tailored_cv`, `scoring`, `requirement_extraction`) remain
for API / UI compatibility via `_ensure_legacy_fields`.

## Profession-agnostic design

- No hardcoded software-engineering-only paths in agents
- Ontology + cue buckets generalize across healthcare, sales, education,
  finance, operations, hospitality, government, etc.
- Company intelligence leaves unknowns as `Unknown` rather than inventing

## Quality gates (pre-export)

- Facts unchanged (claim validation + fact lock)
- Grammar / readability / professional tone
- ATS structure (no table/layout hacks)
- No duplicated / AI-cliché wording (AI detector + style validator)
- Unsupported claims rejected at **sentence** level

## Quality Intelligence (optional)

Stores **anonymous aggregate metrics only**:

- job family / industry
- recruiter & hiring-manager score distributions
- section order heuristics
- theme performance
- hard-requirement coverage

Never stores: names, emails, resume text, JD text, or generated content copies.

Disable with `QUALITY_INTELLIGENCE_ENABLED=0`.
Path override: `QUALITY_INTELLIGENCE_PATH`.

## Testing

- `tests/test_multi_agent_architecture.py` — every agent independently
- Existing safety / claim / writer / PDF suites remain the regression harness
- Multi-profession fixtures: healthcare, sales, education

## Extending

1. Add a new agent class under `agents/` inheriting `Agent[InT, OutT]`
2. Define input/output dataclasses in `agents/schemas.py`
3. Register in `AGENT_CATALOG` + `build_agent_instances()`
4. Wire into `pipeline.run_intelligent_tailoring_agents`
5. Add an isolated unit test — no need to run the full LLM pipeline
