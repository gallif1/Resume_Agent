# Resume Agent — Technical Brief for Optimization Review

This document describes **what the software does** and **how it works**, for an AI that has never seen the codebase. Use it to propose concrete optimizations (latency, cost, recall/precision, reliability, architecture).

---

## 1. Product in one paragraph

**Resume Agent** is a job-search and auto-apply system aimed at the **Israeli job market** (Hebrew + English). A user uploads one or more résumés; the system analyzes them (optionally with OpenAI), builds a search strategy, scrapes job boards (Drushim, LinkedIn guest search, GotFriends), enriches listings with full descriptions, **scores each job** with a deterministic profile+ATS matcher, and can **auto-fill and submit applications** via Playwright against board/ATS sites. There is a Hebrew RTL web UI and a Python FastAPI backend; production ships as a **single Docker image** (UI static assets + API + Chromium).

---

## 2. Repository layout

```
Resume_Agent/
  ai-job-agent/       # Python backend: pipeline, scoring, Playwright apply, FastAPI
  resume-agent-web/   # React + TypeScript + Vite SPA (Hebrew RTL)
  Dockerfile          # Multi-stage: build frontend, run Playwright+Python API
  render.yaml         # Render.com deploy
  scripts/            # Local share / tunnel helpers
  .github/workflows/  # CI (pytest + frontend build) + Render deploy hook
```

Not Next.js. Frontend is Vite 8 + React 19. Backend is Python FastAPI + Playwright 1.61 + SQLite.

---

## 3. End-to-end pipeline (the core loop)

Per CV (isolated under `data/cvs/<cv_id>/`):

```
1. parse_cv        → extract text/skills + optional OpenAI universal profile
2. analyze_roles   → build matching strategy + board search queries
3. collect_jobs    → scrape selected boards; upsert into SQLite
4. enrich_jobs     → open job pages; store full_description
5. match_jobs      → score each job (no per-job OpenAI by default)
6. (optional) apply → Playwright provider fills ATS/board form and may submit
```

**Critical vs non-critical:** failures in `parse_cv`, `analyze_roles`, or `match` abort the scan. Collect/enrich can warn and continue.

**Incremental design:**
- Collect skips known job identity keys; upserts new/updated listings
- Enrich skips already-successful rows; retries failures after cool-down / attempt limits
- Match skips if already matched and strategy hash unchanged; rematches when CV/strategy changes

CLI one-shot: `python src/run_all.py`. Web: `POST /cvs/{id}/run-agent` runs the same pipeline in a subprocess with `AGENT_CV_ID` / `AGENT_SCAN_ID` set.

---

## 4. Backend (`ai-job-agent`) — how each stage works

### 4.1 Résumé parsing (`parse_cv.py`, `cv_reader.py`, `cv_analyzer.py`, `universal_profile.py`)

- Inputs: PDF / DOCX / TXT / images (upload API)
- Text extraction chain: pymupdf → pdfplumber → pypdf; images can use vision if OpenAI is configured
- Skills: rule-based keyword categories (`skills.py`) plus optional OpenAI structured extraction
- Output: structured `cv_profile.json` (contact, skills, experience, education, `ai_insights`, `universal_profile`)
- **The system does not generate résumés**; it only parses uploaded ones

### 4.2 Role / strategy analysis (`analyze_roles.py`, `role_analyzer.py`, `query_builder.py`)

- Turns the universal profile into a **matching strategy**: job categories, keywords, seniority, negatives, collection queries
- Caps how many queries/categories run (`COLLECT_MAX_QUERIES`, `COLLECT_MAX_CATEGORIES`; tighter when running under API with `AGENT_CV_ID`)
- Strategy hash is stored so rematching can detect drift

### 4.3 Job collection (`collect_jobs.py`, `gotfriends_collector.py`, `job_boards.py`)

| Board | Method |
|--------|--------|
| **Drushim** | Prefer HTTP + BeautifulSoup; optional Playwright fallback; stealth Chromium; Cloudflare/captcha detection |
| **LinkedIn** | Public guest Jobs API; parse cards; stop on HTTP 429; sleep between pages |
| **GotFriends** | Public HTML category/profession pages; keyword→slug hints (IT-heavy) |

Browser setup (`browser_utils.py`): Chromium, `he-IL` / `Asia/Jerusalem`, anti-automation flags, persistent profiles under `data/browser_profile/` or per-CV.

Jobs are deduped via **identity keys** (`job_identity.py`), e.g. `drushim:job:<id>`, plus URL/content hashes.

### 4.4 Enrichment (`enrich_jobs.py`)

- Opens each job URL (Playwright; HTTP fallback) to extract full JD text
- Status machine: `success` / `no_description` / `failed` / `timeout` / `blocked`
- Max attempts (~3), retry after ~3 days for stale failures
- Makes matching far more accurate than title-only

### 4.5 Matching / scoring (`match_jobs.py`, `profile_matcher.py`, `ats_scorer.py`, `job_analyzer.py`)

**Current production path is deterministic — no OpenAI call per job.**

For each job:
1. `analyze_job(..., use_ai=False)` builds a `JobProfile` (skills, seniority, mandatory reqs, years, etc.) via rules/keywords
2. **Profile matcher (60%)** — title, skills, domain, seniority, keywords, location/remote
3. **ATS scorer (40%)** — mandatory requirements, required skills, experience, seniority distance, preferred skills
4. Final: `round(0.60 * profile + 0.40 * ats)`; hard caps around **49** if exclusion keywords hit or mandatory requirements fail
5. Labels: Excellent ≥85, Good ≥70, Partial ≥50, else Weak

Optional AI rerank exists via env (`AI_RERANK_ENABLED`, top-N, min local score) but defaults **off**. Legacy `job_matcher.py` (AI-per-job) is largely unused by the current pipeline.

OpenAI’s main role today is **upstream**: understand the CV and build the search/matching strategy — not score every listing.

### 4.6 Auto-apply (`application_service.py`, `application_worker.py`, providers)

- Runs in a **separate subprocess** (Playwright sync cannot live in FastAPI async threads)
- Provider registry picks first `can_handle` match: Drushim, LinkedIn, Greenhouse, Lever, Workday, Comeet, Bullhorn, SmartRecruiters, then Generic
- Flow: open URL → auth if needed → fill mapped fields (name/email/phone/CV/cover letter) → validate → optional submit → verify
- Cover letter: optional file, else **static template** (not LLM) when enabled
- Captcha / SMS / ambiguous fields → `requires_user_action`
- Rate limit: ~10 apply requests / 60s / CV; duplicates → 409
- Legacy CLI `apply_jobs.py` is Drushim-specific and interactive

### 4.7 Persistence

- SQLite + WAL
- `data/registry.db` — CV registry
- `data/cvs/<cv_id>/jobs.db` — jobs, scans, matches, applications for that CV
- Legacy shared `data/jobs.db` still supported / migratable
- Tables include: `jobs`, `cv_scans`, `cv_job_matches`, `job_applications`, `job_application_steps`, plus legacy `applications`

### 4.8 HTTP API (primary surface for the UI)

Multi-CV (what the web app uses):
- `GET/POST /cvs`, `POST /cvs/upload`, `DELETE /cvs/{id}`
- `POST /cvs/{id}/run-agent`, `GET /cvs/{id}/scan-status`
- `GET /cvs/{id}/matches`, `PATCH .../status`
- `POST /cvs/{id}/jobs/{job_id}/apply`, application status/retry
- `GET/PUT /cvs/{id}/site-credentials`
- `GET /api/job-sites`, `GET /api/health` (includes `playwright_ready`)

Legacy: `/api/pipeline/*`, `/api/jobs`, `/api/cv`. In Docker, FastAPI also serves the built SPA.

**No end-user auth** on the API (open single-tenant tool). Job-board passwords can be stored per CV or via env (`DRUSHIM_*`, `LINKEDIN_*`).

---

## 5. Frontend (`resume-agent-web`) — how the UI works

- Thin Hebrew RTL SPA; **no router**, **no Redux/Query** — local `useState` + polling
- Views: CV list → CV details (jobs | profile) → run-agent modal (pick Drushim / LinkedIn / GotFriends)
- Dev: Vite proxies `/api` and `/cvs` to `:8000`
- Prod/Docker: `VITE_API_URL=""` so browser calls same origin
- Polling: health 3–10s; scan status every ~2.5s; apply refresh ~3s
- One scan at a time globally (backend 409 if another scan is running)

---

## 6. What OpenAI is (and is not) used for

| Used for | Not used for (by default) |
|----------|---------------------------|
| Structured CV / universal profile | Scoring every job |
| Role/strategy insight (when configured) | Generating résumés |
| Optional vision OCR for scanned CVs | Generating high-quality cover letters |

Without `OPENAI_API_KEY`, parsing/matching still work via rules; quality of strategy and CV structure drops.

---

## 7. Deployment / runtime constraints

- Docker: Node builds UI → Playwright Python image installs Chromium → `api_server.py --host 0.0.0.0:8000`
- Free Render sleeps when idle (~30s cold start)
- Scraping is fragile: selectors, Cloudflare, LinkedIn rate limits, GotFriends keyword map skew
- Apply often needs human help for SMS/captcha/complex ATS flows
- Single-tenant, no multi-user security model

---

## 8. Optimization-relevant hotspots (for the reviewing model)

1. **Collect query budget & board selection** — largest scan cost/latency lever (`COLLECT_MAX_QUERIES`, site enable flags, LinkedIn pagination)
2. **Enrich volume** — many Playwright page loads; HTTP-first + skip/retry already exist but still dominate runtime/RAM
3. **Scoring weights & caps** (`PROFILE_MATCH_WEIGHT`/`ATS_MATCH_WEIGHT`, ATS section weights, mandatory/exclusion caps) — precision/recall without API spend
4. **Hebrew↔English skill/synonym coverage** (`skill_normalizer`, `synonym_dictionary.json`, `KEYWORD_SYNONYMS`) — recall without LLMs
5. **Selective AI** — re-enable top-N rerank / per-job analysis only where rule scores are ambiguous (env knobs already exist)
6. **Polling vs push** — frontend chatty polls; SSE/WebSocket or backoff would cut load
7. **Provider selection accuracy** — wrong ATS provider wastes apply attempts
8. **Dedup / identity hashing** — false thrash causes re-enrich and rematch
9. **Dual legacy paths** — CLI apply vs worker providers; legacy `/api/pipeline` vs `/cvs/*` — complexity cost
10. **Security if multi-user** — open API + stored board passwords; out of scope today but limits hosting model

---

## 9. Mental model for the reviewer

Treat this as:

> **Strategy-driven multi-board collector + deterministic ATS-style matcher + Playwright multi-ATS applier**, with OpenAI mainly for CV understanding and search-plan generation — wrapped in a Hebrew multi-CV web UI and SQLite isolation per résumé.

It is **not** a résumé builder, a general CRM, or an email-sending bot.

---

## Suggested prompt to paste with this brief

```
You are reviewing a software system described in OPTIMIZATION_BRIEF.md.
You have never seen the code. Based only on this brief, propose ranked
optimizations for: (1) scan latency, (2) match quality, (3) scrape
reliability, (4) apply success rate, (5) OpenAI/API cost, (6) code/architecture
simplicity. For each item: problem, why it matters, concrete change, risk,
and expected impact. Prefer high-leverage, low-risk changes first.
```
