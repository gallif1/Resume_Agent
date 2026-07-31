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
