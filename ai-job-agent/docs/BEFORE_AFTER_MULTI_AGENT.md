# Before / After — Multi-Agent Refactor

## Before (`intelligent_tailor_v6`)

- Staged pipeline with strong evidence safety, but stages were functions, not agents
- Job understanding focused on skills/responsibilities lists
- No first-class `CompanyProfile`
- Evidence map lacked allowed/forbidden wording contracts
- Strategy did not consume employer intelligence
- No Hiring Manager Simulation feedback object
- No optional anonymous Quality Intelligence layer
- Architecture harder to test per responsibility

## After (`multi_agent_v1`)

| Concern | Before | After |
|--------|--------|-------|
| Architecture | Staged functions in `pipeline.py` | 10 specialist agents with typed I/O |
| Candidate parsing | KB stage | **Resume Knowledge Agent** (facts only) |
| JD understanding | Requirement extraction | **Job Intelligence Agent** → `JobProfile` with importance/confidence |
| Employer context | Implicit / unused | **Company Intelligence Agent** → `CompanyProfile` (Unknown preserved) |
| Evidence | MATCH/PARTIAL/MISSING | + Explicit/Strong/Weak/No Evidence, source location, wording constraints |
| Strategy | Title/evidence driven | **Resume Strategy Agent** + company-influenced priorities |
| Content selection | Deep rewrite service | **Resume Tailoring Agent** (selection only) |
| Validation | Claim validator tool | **Claim Validation Agent** with Accept/Rewrite/Regenerate/Reject |
| Writing | Human writer stage | **Human Resume Writer Agent** (wording only) |
| Recruiter critique | Review service | **Senior Recruiter Review Agent** (structured questions) |
| HM view | Absent | **Hiring Manager Simulation Agent** |
| Learning | Absent | Optional anonymous Quality Intelligence |
| Templates | 5 ATS themes | Refined typography/spacing; still table-free ATS-safe |
| API compatibility | Dual schema | Preserved (`tailored_cv`, scores, change_log, reports) |

## Example differentiation (profession-agnostic)

Same candidate pipeline, different jobs:

1. **Healthcare RN role** — prioritizes patient care, EHR, discharge planning; company profile leans clinical / patient safety; HM flags missing clinical requirements explicitly.
2. **Enterprise AE role** — prioritizes Salesforce, quota, pipeline; company profile leans B2B SaaS / ownership; HM scores business fit on revenue signals.
3. **Math Teacher role** — prioritizes classroom management, differentiation, assessment; company profile leans education / learning culture.

Result: genuinely different strategies, evidence coverage, and feedback — without profession-specific hardcoding.

## Quality upgrades (`multi_agent_v1_1`)

Writing quality is improved **inside** the existing agents/pipeline (no new agents):

- Natural summary builder (no “Professional with Knowledge…” filler)
- Evidence amplifier (inventory + thin project expansion + skill propagation)
- Role-dynamic skills category ordering
- Stronger Human Writer / Recruiter prompts + tougher heuristic review
- Hiring Manager challenges feed back into a refine writing pass
- Internal Resume Quality Score drives weak-section regeneration

## Premium one-page writing (`multi_agent_v1_2`)

Same architecture; stronger premium-writer behavior + hard one-page default:

- Concise natural summaries (~58 words) that sell role fit
- Tech weaver integrates evidenced tools into Experience/Projects
- Project stub bullets upgraded into value-oriented stories (facts only)
- One-page compressor ranks relevance, dedupes, caps bullets/roles
- Page-count gate blocks export when content still overflows
- Stricter recruiter + hiring-manager critique loops
- Denser premium ATS themes (readable fonts, balanced whitespace)

## Interview-first + live generation UX (`multi_agent_v1_3`)

Philosophy shift: every agent optimizes for interview probability, not document generation.

- 15-second recruiter screen + strongest-evidence selection (`top_interview_reasons`)
- Human-readable `decision_log` (no chain-of-thought)
- Live SSE progress (`GET /api/tailor/stream`) with agent timeline + decisions
- Final `generation_report` + section-change chips in the web preview
- Same multi-agent architecture — no new agents

## Decision-quality upgrade (`multi_agent_v1_4`)

Same agents — dramatically better reasoning. Success metric is interview probability only.

- Job Intelligence infers hiring intent (person archetype, priorities, narrative themes)
- Knowledge / Evidence discover soft + transferable competencies (ownership, debugging, teaching, …)
- Strategy builds a job-specific professional story; two jobs → different resumes
- Writer sells strongest evidence; Recruiter/HM actively challenge weak/generic sections
- Quality loop adds 20-second recruiter screen + interview_probability dimensions
- Requirement coverage tiers: Explicit / Strong Supporting / Transferable / No Evidence
- Never invent facts

## Acceptance criteria mapping

1. Different jobs → different resumes — strategy + evidence + company influence
2. Every important requirement evaluated — Evidence Mapping Agent
3. Every sentence supported — Claim Validation Agent
4. Safe inference works — Strong Inference path retained
5. Unsupported claims never generated — reject/strip at sentence level
6. Premium human wording — Human Resume Writer + style/AI gates
7. Recruiter understands match — Senior Recruiter Review structured output
8. HM feedback useful — Hiring Manager Simulation scores + why interview/reject
9. Company intelligence influences prioritization without invention — Strategy Agent
10. Modular/testable — `agents/` package + per-agent tests
11. Modern PDF themes — refined ATS themes
12. ATS excellent — no tables/graphics; structure gates
13. Technical + non-technical — multi-profession fixtures
14. Agents independently testable — `test_multi_agent_architecture.py`
15. Maintainable/extensible — documented catalog + schemas
