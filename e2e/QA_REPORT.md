# QA Report — Resume Agent (Live)

**Target:** https://resume-agent-u8n6.onrender.com  
**Suite:** `e2e/` Playwright Chromium automation  
**Report date (UTC):** 2026-07-19  

## Executive summary

Overall quality is **good for core auth + resume management**, with a working Hebrew RTL SPA and stable API health. Production risks are mainly **accessibility contrast**, **very small viewport overflow**, and **API/SPA path collisions** (`/jobs`, `/cvs`). A live job scan completed without match results, so deep job-result and apply-confirm flows were only partially exercised.

**Production-readiness verdict:** Ready after minor fixes

**Critical risks**

1. Browser navigation to `/jobs` or `/cvs` returns API JSON instead of the React app (blank/JSON “Pretty-print” page).
2. WCAG AA color-contrast failures on auth tabs and footer.
3. ~10px horizontal overflow at 320×568.
4. Job match yield on a minimal Drushim-only scan was zero in this run — apply/results depth blocked.

**Count by severity**

| Severity | Count |
|----------|------:|
| Critical | 1 (DEF-001 SPA/API collision) |
| Major | 1 (DEF-002 a11y contrast) |
| Minor | 1 (DEF-003 320px overflow) |
| Blocked coverage | 3 (job results depth, apply confirm, password-mismatch N/A) |

## Environment

| Item | Value |
|------|--------|
| Browser | Playwright Chromium (Chrome for Testing 149 / Playwright 1.58+) |
| OS | Linux 6.12.94+ (x86_64) |
| Test date | 2026-07-19 (UTC) |
| Viewports | 1440×900, 1280×720, 768×1024, 390×844, 320×568 |
| Base URL | `https://resume-agent-u8n6.onrender.com` |
| Test account state | `QA_TEST_EMAIL` / `QA_TEST_PASSWORD` **unset**; suite registered throwaway `qa.e2e.*@example.com` accounts via public registration |
| Test data | Local dummy PDFs under `e2e/fixtures/` with marker `e2e-qa-temp`; unsupported `.exe`; Hebrew / long / spaced filenames |
| Secrets in report | None (tokens/passwords redacted from diagnostics) |

### Application discovery (Phase 1)

- **Frontend:** React + TypeScript + Vite SPA (no client router)
- **Backend:** FastAPI same-origin (`/api`, `/cvs`, `/jobs`)
- **Auth:** JWT in `localStorage` (`resume_agent_jwt`); login + register; password min length 6; **no** password-confirm field
- **UI:** Hebrew RTL (`html[dir=rtl][lang=he]`)
- **Main flows:** Auth → upload CVs → configure sites → run agent → view workspace matches → apply confirm / tailor CV
- **Flow map:** `e2e/APPLICATION_FLOW_MAP.md` and `e2e/artifacts/discovery/`

## Coverage

Logical scenario counts below are based on **executed** Playwright results across the primary full run plus confirmatory reruns after test fixes. “Skipped” project duplicates (intentional viewport scoping) are not counted as blocked.

| Area | Tests | Passed | Failed | Blocked |
|------|------:|-------:|-------:|--------:|
| Discovery | 1 | 1 | 0 | 0 |
| Smoke | 5 | 5 | 0 | 0 |
| Authentication | 4 | 4 | 0 | 1 (password mismatch N/A — no confirm field) |
| Resume management | 1 | 1 | 0 | 0 |
| Resume data separation | 1 | 1 | 0 | 0 |
| Job scan / results / apply | 2 | 1 | 0 | 2 (no matches → results/apply depth; secondary site-disable test skipped when config absent) |
| Responsive (5 viewports) | 5 | 5 | 0 | 0 |
| RTL | 1 | 1 | 0 | 0 |
| Accessibility | 2 | 2 | 0 | 0 |
| **Total (unique scenarios)** | **22** | **21** | **0** | **3** |

**Initial full-run raw Playwright tally (before test hardening):** 25 passed, 4 failed, 51 skipped (skips = intentional cross-project filters). Failures were axe contrast (product defect) and mobile status-label visibility (test locator vs intentional CSS `display:none`). After locator/assertion fixes, confirmatory reruns on desktop/mobile/small-mobile passed.

**Artifacts:** `e2e/artifacts/screenshots/`, `e2e/artifacts/diagnostics/`, `e2e/artifacts/reports/`, failure videos/traces under `e2e/artifacts/test-results/`.

## Defects

### DEF-001 — SPA deep links collide with API prefixes

| Field | Detail |
|-------|--------|
| Severity | Critical (UX / supportability) |
| Route / feature | `/jobs`, `/cvs` browser navigation |
| Preconditions | None (unauthenticated browser) |
| Reproduction | 1. Open `https://resume-agent-u8n6.onrender.com/jobs` 2. Observe raw JSON `{"detail":"Not found"}` with Chrome “Pretty-print” 3. Open `/cvs` → JSON 401 4. Compare `/dashboard` or `/foo` → HTML SPA loads |
| Expected | Unknown UI paths serve `index.html` SPA shell |
| Actual | FastAPI API routes take precedence; no HTML fallback for `/jobs` / `/cvs` |
| Frequency | 100% |
| Evidence | Screenshot from smoke history failure: `artifacts/test-results/smoke-Phase-2-—-Smoke-navi-28bd9--refresh-and-history-behave-chromium-desktop/test-failed-1.png`; `curl` Content-Type `application/json` |
| Suggested fix | Mount SPA catch-all **after** API routes but exclude exact API paths, or serve frontend under a path prefix; ensure browser `Accept: text/html` gets `index.html` |

### DEF-002 — Insufficient color contrast (auth + footer)

| Field | Detail |
|-------|--------|
| Severity | Major (WCAG 2 AA) |
| Route / feature | Auth view tabs + footer |
| Preconditions | Open logged-out home |
| Reproduction | Run axe-core on auth view (suite does this automatically) |
| Expected | Contrast ≥ 4.5:1 for normal text |
| Actual | Inactive tab `#778599` on `#fefeff` ≈ **3.72:1**; footer `#94a3b8` on `#ffffff` ≈ **2.56:1** |
| Frequency | 100% |
| Evidence | `e2e/artifacts/reports/axe-auth-chromium-desktop.json` (and mobile twin) |
| Suggested fix | Darken inactive tab and footer text (e.g. ≥ `#667085` / `#64748b` depending on background) |

### DEF-003 — Horizontal overflow at 320×568

| Field | Detail |
|-------|--------|
| Severity | Minor |
| Route / feature | Authenticated workspace on smallest mobile viewport |
| Preconditions | Logged in at 320×568 |
| Reproduction | Load workspace; measure `document.scrollWidth` vs `clientWidth` |
| Expected | No horizontal page overflow |
| Actual | `scrollWidth=330 > clientWidth=320` (~10px) |
| Frequency | Observed on chromium-small-mobile |
| Evidence | `e2e/artifacts/reports/defect-003-overflow.json`; screenshot `artifacts/screenshots/defect-003-small-mobile-overflow.png` |
| Suggested fix | Audit hero/header padding and long unbroken strings; ensure `overflow-x` containment on `.app` / `.main` |

## Blocked tests

| Scenario | Why blocked |
|----------|-------------|
| Password mismatch validation | UI has no confirm-password field (by design in `AuthView.tsx`) |
| Job results: search / filter / pagination | **Not present** in product UI (sort only) — documented N/A, not a failure |
| Job results field-level checks (title, company, score, description, apply) | Live Drushim-only scan **completed with zero matches** (`hadMatches=false`; screenshots `job-scan-running.png`, `job-scan-after-wait.png`, `job-results-none.png`) |
| Apply flow stop-before-submit | Requires a visible “הגש קורות חיים” button on a match card — unavailable without matches |
| Env-credential-only auth path | `QA_TEST_EMAIL` / `QA_TEST_PASSWORD` unset; substituted with generated accounts (auth still fully tested) |
| Repeated expensive scans / multi-site scrape | Intentionally limited to **one** Drushim-only scan |

## Positive findings

- Site loads with brand, Hebrew auth UI, and no blank `#root` on `/`.
- `/api/health` returns OK; Playwright-ready flag true during probes.
- Registration validation works (empty/invalid/weak); duplicate email rejected; login/logout/refresh session OK; Enter-to-submit works; invalid login shows `role="alert"`.
- Protected workspace not reachable when logged out / with forged JWT.
- Resume upload accepts PDF; rejects unsupported types client-side; handles long, Hebrew, and spaced/parenthesis filenames; delete confirm + cancel work; cleanup limited to `e2e-qa-temp*` files.
- Multi-CV picker switches selection; workspace copy correctly describes **aggregated** profile (not per-CV isolated job sets).
- Scan CTA disabled with zero sites; UI lock / stop controls appear when scan starts; reload during/after scan does not crash.
- RTL `dir`/`lang`, LTR credential inputs, Hebrew labels verified.
- No critical `pageerror` events in successful diagnostics captures for smoke/auth/resume.
- axe: no **critical** violations; keyboard can reach email/password/submit; delete modal Escape not supported (cancelled via button — minor note).

## Browser diagnostics (Phase 12)

Observed during runs (redacted artifacts in `artifacts/diagnostics/`):

- Occasional `net::ERR_ABORTED` on `/api/health` during navigation aborts (benign).
- Expected 4xx on negative auth and unauthenticated `/cvs`.
- No CORS failures observed against same-origin API.
- No secrets printed in console captures.
- Delete modal does **not** close on Escape (annotation in a11y test) — minor UX/a11y gap, not filed as separate severity above.

## Final verdict

**Ready after minor fixes**

Ship blockers for polish: fix SPA fallback for API-colliding paths, raise contrast on auth/footer, and trim 320px overflow. Re-run job-flow after a scan that yields matches (or seed fixtures) to complete apply-confirm coverage. Do **not** treat zero-match scan yield alone as a release blocker without CV/content context — the dummy PDF may simply not match live board inventory.
