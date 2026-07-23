# AI Job Agent

A beginner-friendly Python project for collecting and tracking job applications.

## Project structure

```
ai-job-agent/
  data/           # profile.json, cv_profile.json, jobs.db
  resumes/        # Your CV (cv.pdf)
  logs/           # Screenshots and logs
  src/
    collect_jobs.py  # scrape jobs from Drushim
    parse_cv.py      # read CV -> skills (+ OpenAI analysis if configured)
    cv_analyzer.py   # OpenAI resume analysis
    enrich_jobs.py   # fetch full job descriptions
    match_jobs.py    # score jobs vs profile + CV
    list_jobs.py     # view results
    apply_jobs.py    # send your CV to matched jobs (Drushim)
    db.py            # PostgreSQL (DATABASE_URL) or SQLite helpers
    skills.py        # known skill keywords
    config.py        # paths and settings
```

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. (Optional) Set `DATABASE_URL` in `.env` for PostgreSQL / AWS RDS:

```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

When unset, the agent uses local SQLite files under `data/` (default for tests and offline use).

3. Install Playwright browsers:

```bash
python -m playwright install chromium
```

4. Edit `data/profile.json` with your details and place your resume at `resumes/cv.pdf`.

5. **(Optional)** Add your OpenAI API key to `.env` for smart CV analysis:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

Without a key, resume parsing still works using local rule-based extraction.

Optional: set `HEADLESS=false` in `.env` to always open a visible browser.

## Typical workflow

**One command (runs everything):**

```bash
python src/run_all.py
```

On Windows you can also double-click `run.bat` or run:

```bat
run.bat
```

`run_all.py` collects jobs, parses your CV, scores matches, **shows the relevant
jobs, and then asks whether to send your CV**. Type `Y` and it fills in your
details and sends your CV to every matched job automatically.

Or step by step:

```bash
python src/collect_jobs.py   # 1. collect jobs from Drushim
python src/parse_cv.py       # 2. read your CV (skills + text)
python src/enrich_jobs.py    # 3. fetch full job descriptions
python src/match_jobs.py     # 4. score jobs against profile + CV
python src/list_jobs.py      # 5. view the best matches
python src/apply_jobs.py     # 6. send your CV to the matches
```

Options for `run_all.py`:

```bash
python src/run_all.py --min-score 60      # custom score threshold
python src/run_all.py --skip-collect      # skip Drushim scraping
python src/run_all.py --skip-enrich       # skip full description fetch
python src/run_all.py --skip-apply        # don't offer to send CVs
python src/run_all.py --dry-run-apply     # fill the form but don't send
```

## 1. Collect jobs

```bash
python src/collect_jobs.py
```

Searches Drushim for every role in `profile.json` `target_roles`, saves jobs to the database, and prints how many were found and inserted. If extraction fails, screenshots and logs are saved to `logs/`.

## 2. Parse your CV

```bash
python src/parse_cv.py
```

Reads `resumes/cv.pdf`, extracts the text and a list of known skills, and saves them to `data/cv_profile.json`. Use a text-based PDF (not a scanned image).

When `OPENAI_API_KEY` is set in `.env`, OpenAI also:
- Extracts contact info, experience, education and skills more accurately
- Suggests best-fit roles and strengths
- Adds `ai_insights`: professional summary, achievements, gaps and improvement tips

```bash
python src/parse_cv.py --no-ai   # skip OpenAI, rule-based only
```

## 3. Enrich with full descriptions

```bash
python src/enrich_jobs.py            # all jobs missing a description
python src/enrich_jobs.py --limit 10 # only the first 10
python src/enrich_jobs.py --redo     # re-fetch everything
```

Opens each job page and stores the full description. This makes matching much more accurate.

## 4. Match jobs (profile + CV)

```bash
python src/match_jobs.py
```

Each job gets a `match_score` (0-100) that blends:

- **Profile fit** — target roles, location, junior/senior keywords
- **CV fit** — overlap between your CV skills and the job description

If no CV is loaded, it falls back to profile-only scoring.

## 5. List saved jobs

```bash
python src/list_jobs.py                # matches >= min_match_score (75)
python src/list_jobs.py --all          # all jobs
python src/list_jobs.py --min-score 50 # custom threshold
python src/list_jobs.py --url          # include job links
python src/list_jobs.py --why          # show why each job scored
```

## 6. Send your CV (auto-apply)

```bash
python src/apply_jobs.py                 # send to matches >= min_match_score
python src/apply_jobs.py --min-score 65  # custom threshold
python src/apply_jobs.py --limit 5       # only the first 5 matches
python src/apply_jobs.py --dry-run       # fill the form but don't send
python src/apply_jobs.py --yes           # skip the Y/N confirmation
```

What it does:

1. Lists the relevant matched jobs and asks for confirmation (type `Y`).
2. Opens Drushim in a **real browser window**. If you aren't signed in, it asks
   for your login details (from `.env` or typed in), or lets you sign in
   manually in the window — the session is remembered in `data/browser_profile/`
   so you only do this once.
3. For each job it clicks "שלח/י קורות חיים", fills your name, email and phone
   (from `profile.json` / `cv_profile.json`), attaches `resumes/cv.pdf`, and
   sends it.
4. Every result is recorded in the `applications` table, so already-sent jobs
   are skipped next time. Screenshots of each submission are saved to `logs/`.

Relevant `.env` settings:

```
APPLY_HEADLESS=false   # show the browser while applying (recommended)
AUTO_SUBMIT=true       # set false to fill the form without sending
DRUSHIM_EMAIL=         # optional — otherwise sign in manually once
DRUSHIM_PASSWORD=
```

> Note: Drushim's exact form/login can change and may use SMS codes. The
> browser stays visible so you can complete any step manually if needed.

## HTTP API (for the web client)

```bash
python src/api_server.py     # starts on http://localhost:8000
```

Exposes the pipeline to the separate web client (`resume-agent-web` repo):

- `POST /api/pipeline/run` — run parse/analyze/collect/enrich/match in the background
- `GET /api/pipeline/status` — live progress, per-step status and log tail
- `GET /api/jobs?min_score=55` — matched jobs with scores and AI explanations
- `POST /api/cv` — upload a new resume (replaces `resumes/cv.*`)
- `GET /api/cv` — info about the current resume and its analysis
- `GET /api/health` — server liveness

The apply step (sending CVs through Drushim) is intentionally not exposed —
it needs a visible browser and possible manual login, so keep using
`python src/apply_jobs.py` for that.

## Initialize the database

```bash
python src/db.py
```

Creates the required tables. With `DATABASE_URL` set this initializes PostgreSQL;
otherwise it creates `data/jobs.db` (SQLite) with the `jobs` and `applications` tables.
