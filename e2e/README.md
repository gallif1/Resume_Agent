# Resume Agent — Playwright E2E QA Suite

Automated end-to-end tests against the **live** deployment:

`https://resume-agent-u8n6.onrender.com`

This folder is standalone and does **not** modify production application code.

## Prerequisites

- Node.js 20+
- Network access to the deployed app

```bash
cd e2e
npm install
npx playwright install chromium
```

## Environment variables

Copy `.env.example` to `.env` (optional):

| Variable | Purpose |
|----------|---------|
| `E2E_BASE_URL` | Override target (default: Render URL) |
| `QA_TEST_EMAIL` | Dedicated test account email |
| `QA_TEST_PASSWORD` | Dedicated test account password |
| `SKIP_EXPENSIVE_SCAN` | Set to `1` to skip starting a live job scan |

If `QA_TEST_*` are unset, the suite **registers a throwaway account** per run via the public registration API. Authenticated scenarios are marked **blocked** only if registration/login cannot be completed.

**Never commit real passwords.** Secrets are redacted from diagnostic artifacts.

## Run commands

```bash
# Full suite (all viewports: 1440×900, 1280×720, 768×1024, 390×844, 320×568)
npm test

# Desktop + mobile only (faster)
npx playwright test --project=chromium-desktop --project=chromium-mobile

# Individual phases
npm run test:discovery
npm run test:smoke
npm run test:auth
npm run test:resume
npm run test:jobs
npm run test:responsive
npm run test:a11y

# HTML report
npm run report
```

## Safety rules (enforced in tests)

- Does **not** confirm final job applications to employers (Apply modal is cancelled)
- Does **not** send emails
- Deletes only resumes whose filenames contain the `e2e-qa-temp` marker
- Triggers at most **one** expensive job scan (Drushim-only sites selected)
- Does not expose tokens/passwords in `QA_REPORT.md`

## Artifacts

| Path | Contents |
|------|----------|
| `artifacts/screenshots/` | Viewport + failure screenshots |
| `artifacts/diagnostics/` | Console / network captures (redacted) |
| `artifacts/test-results/` | Playwright traces/videos on failure |
| `artifacts/html-report/` | Playwright HTML report |
| `artifacts/discovery/` | Flow map from Phase 1 |
| `QA_REPORT.md` | Evidence-based QA verdict |
| `APPLICATION_FLOW_MAP.md` | App flow map |

## Spec files

1. `playwright.config.ts` — projects, screenshots, traces, video
2. `tests/smoke.spec.ts` — Phase 2
3. `tests/auth.spec.ts` — Phase 3
4. `tests/resume-management.spec.ts` — Phases 4–5
5. `tests/job-flow.spec.ts` — Phases 6–8
6. `tests/responsive-rtl.spec.ts` — Phases 9–10
7. `tests/accessibility.spec.ts` — Phase 11
8. `helpers/test-data.ts` — fixtures / credentials / redaction
9. `tests/discovery.spec.ts` — Phase 1 discovery
