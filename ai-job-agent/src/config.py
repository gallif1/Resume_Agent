import os
from pathlib import Path

from dotenv import load_dotenv

# Project root is one level above src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load environment variables from .env in the project root
load_dotenv(PROJECT_ROOT / ".env")

# Paths
DATA_DIR = PROJECT_ROOT / "data"
RESUMES_DIR = PROJECT_ROOT / "resumes"
LOGS_DIR = PROJECT_ROOT / "logs"
SYNONYM_DICTIONARY_PATH = DATA_DIR / "synonym_dictionary.json"

# Per-CV working data lives under data/cvs/<cv_id>/.
CVS_DIR = DATA_DIR / "cvs"

PROFILE_PATH = DATA_DIR / "profile.json"
LEGACY_PROFILE_PATH = PROFILE_PATH
ENRICH_BLOCKED_DEBUG_DIR = DATA_DIR / "debug" / "enrich_blocked"

# Global CV registry (metadata only). Each CV's jobs/scans/matches live in its own DB.
REGISTRY_DB_PATH = DATA_DIR / "registry.db"
# Legacy single-CV database (kept for the old global pipeline tab).
LEGACY_DB_PATH = DATA_DIR / "jobs.db"
DB_PATH = LEGACY_DB_PATH


def cv_db_path(cv_id: str) -> Path:
    """SQLite database for one CV's jobs, scans, and matches."""
    return cv_data_dir(cv_id) / "jobs.db"


def cv_profile_prefs_path(cv_id: str) -> Path:
    """Per-CV search/match preferences derived from the parsed resume."""
    return cv_data_dir(cv_id) / "profile.json"


def cv_ai_cache_dir(cv_id: str) -> Path:
    return cv_data_dir(cv_id) / "ai_cache"

# ---------------------------------------------------------------------------
# CV-scoped paths
# ---------------------------------------------------------------------------
# The pipeline scripts (parse_cv, analyze_roles, collect_jobs, match_jobs, ...)
# run as subprocesses. When AGENT_CV_ID is set, every per-CV artifact is stored
# under data/cvs/<cv_id>/ so running the agent for one CV never touches another
# CV's parsed profile, strategy, or pipeline state. When it is unset the legacy
# global paths in data/ are used, preserving the original single-CV behavior.
AGENT_CV_ID = os.getenv("AGENT_CV_ID", "").strip()
# When the agent runs as part of a recorded scan, matches are linked to it.
AGENT_SCAN_ID = os.getenv("AGENT_SCAN_ID", "").strip()

# Legacy (global) single-CV locations — kept for backward compatibility.
LEGACY_CV_PROFILE_PATH = DATA_DIR / "cv_profile.json"
LEGACY_AI_ROLES_PATH = DATA_DIR / "ai_roles.json"
LEGACY_AI_MATCHING_STRATEGY_PATH = DATA_DIR / "ai_matching_strategy.json"
LEGACY_PIPELINE_STATE_PATH = DATA_DIR / "pipeline_state.json"
LEGACY_CV_PATH = RESUMES_DIR / "cv.pdf"


def cv_data_dir(cv_id: str) -> Path:
    """Directory holding all per-CV artifacts for the given CV id."""
    return CVS_DIR / cv_id


def _find_cv_file(directory: Path) -> Path | None:
    """Return the stored resume file inside a per-CV directory, if present."""
    if not directory.exists():
        return None
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.stem.lower() == "resume":
            return path
    return None


if AGENT_CV_ID:
    _CV_DIR = cv_data_dir(AGENT_CV_ID)
    CV_PROFILE_PATH = _CV_DIR / "cv_profile.json"
    PROFILE_PATH = cv_profile_prefs_path(AGENT_CV_ID)
    AI_ROLES_PATH = _CV_DIR / "ai_roles.json"
    AI_MATCHING_STRATEGY_PATH = _CV_DIR / "ai_matching_strategy.json"
    PIPELINE_STATE_PATH = _CV_DIR / "pipeline_state.json"
    AI_CACHE_DIR = cv_ai_cache_dir(AGENT_CV_ID)
    DB_PATH = cv_db_path(AGENT_CV_ID)
    # Resolve the resume file that was saved for this CV (resume.<ext>).
    CV_PATH = _find_cv_file(_CV_DIR) or (_CV_DIR / "resume.pdf")
else:
    CV_PROFILE_PATH = LEGACY_CV_PROFILE_PATH
    AI_ROLES_PATH = LEGACY_AI_ROLES_PATH
    AI_MATCHING_STRATEGY_PATH = LEGACY_AI_MATCHING_STRATEGY_PATH
    PIPELINE_STATE_PATH = LEGACY_PIPELINE_STATE_PATH
    AI_CACHE_DIR = DATA_DIR / "ai_cache"
    CV_PATH = LEGACY_CV_PATH

# Persistent browser profile — keeps the Drushim login session between runs
# so you only have to sign in once.
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
GOTFRIENDS_BROWSER_PROFILE_DIR = BROWSER_PROFILE_DIR / "gotfriends"

# Optional environment variables (add more as needed)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("1", "true", "yes")

# Auto-apply (sending your CV through Drushim)
# Apply runs in a visible browser by default so you can watch / log in if needed.
APPLY_HEADLESS = os.getenv("APPLY_HEADLESS", "false").lower() in ("1", "true", "yes")
# When false, the form is filled but the final "send" button is NOT clicked
# (useful for a dry test). Defaults to true so applications are actually sent.
AUTO_SUBMIT = os.getenv("AUTO_SUBMIT", "true").lower() in ("1", "true", "yes")
# When true, generate a job-specific cover letter from saved CV data if none is saved.
AUTO_GENERATE_COVER_LETTER = os.getenv(
    "AUTO_GENERATE_COVER_LETTER", "false"
).lower() in ("1", "true", "yes")
# Optional Drushim login credentials. If left empty, you'll be asked to log in
# manually in the browser window the first time (the session is then remembered).
DRUSHIM_EMAIL = os.getenv("DRUSHIM_EMAIL", "").strip()
DRUSHIM_PASSWORD = os.getenv("DRUSHIM_PASSWORD", "")

# LinkedIn apply automation — required for Easy Apply jobs on LinkedIn.
LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "").strip()
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# OpenAI — smart CV analysis (falls back to rule-based parsing if unset)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_CV_MAX_CHARS = int(os.getenv("OPENAI_CV_MAX_CHARS", "24000"))
OPENAI_CV_SUMMARY_MAX_CHARS = int(os.getenv("OPENAI_CV_SUMMARY_MAX_CHARS", "3500"))
OPENAI_JOB_MAX_CHARS = int(os.getenv("OPENAI_JOB_MAX_CHARS", "4000"))
OPENAI_MAX_COLLECTION_ROLES = int(os.getenv("OPENAI_MAX_COLLECTION_ROLES", "4"))

# AI rerank — after local scoring, send only the top-N candidates to OpenAI for
# an accurate semantic evaluation (results are cached, so re-runs are free).
AI_RERANK_ENABLED = os.getenv("AI_RERANK_ENABLED", "false").lower() in ("1", "true", "yes")
AI_RERANK_TOP_N = int(os.getenv("AI_RERANK_TOP_N", "25"))
AI_RERANK_MIN_LOCAL_SCORE = int(os.getenv("AI_RERANK_MIN_LOCAL_SCORE", "40"))

DRUSHIM_BASE_URL = "https://www.drushim.co.il"

# LinkedIn job search (public guest endpoints — no login required)
LINKEDIN_BASE_URL = "https://www.linkedin.com"
LINKEDIN_ENABLED = os.getenv("LINKEDIN_ENABLED", "true").lower() in ("1", "true", "yes")
LINKEDIN_LOCATION = os.getenv("LINKEDIN_LOCATION", "Israel").strip()
# How many result pages (25 jobs each) to fetch per search query.
# Slightly deeper when running from the web UI so scans get a real pool.
_DEFAULT_LINKEDIN_MAX_PAGES = "3" if AGENT_CV_ID else "2"
LINKEDIN_MAX_PAGES = int(os.getenv("LINKEDIN_MAX_PAGES", _DEFAULT_LINKEDIN_MAX_PAGES))

# GotFriends job search (public HTML — no login required)
GOTFRIENDS_BASE_URL = "https://www.gotfriends.co.il"
GOTFRIENDS_ENABLED = os.getenv("GOTFRIENDS_ENABLED", "true").lower() in ("1", "true", "yes")
# How many listing pages (~10 jobs each) to fetch per profession/category URL.
_DEFAULT_GOTFRIENDS_MAX_PAGES = "3" if AGENT_CV_ID else "2"
GOTFRIENDS_MAX_PAGES = int(os.getenv("GOTFRIENDS_MAX_PAGES", _DEFAULT_GOTFRIENDS_MAX_PAGES))

# Drushim JSON search API (paginated; HTML scrape only returns the first SSR page).
DRUSHIM_API_BASE_URL = os.getenv("DRUSHIM_API_BASE_URL", "https://webapi.drushim.co.il").rstrip("/")
# How many API pages (~10 jobs each) to fetch per query. Page 0 is the SSR-sized first page.
_DEFAULT_DRUSHIM_MAX_PAGES = "4" if AGENT_CV_ID else "5"
DRUSHIM_MAX_PAGES = int(os.getenv("DRUSHIM_MAX_PAGES", _DEFAULT_DRUSHIM_MAX_PAGES))

# Job collection limits — web UI defaults aim for ~50–100 jobs/scan while staying polite.
_DEFAULT_COLLECT_MAX_QUERIES = "5" if AGENT_CV_ID else "8"
COLLECT_MAX_QUERIES = int(os.getenv("COLLECT_MAX_QUERIES", _DEFAULT_COLLECT_MAX_QUERIES))
_DEFAULT_COLLECT_MAX_CATEGORIES = "3" if AGENT_CV_ID else ""
_collect_max_categories_raw = os.getenv("COLLECT_MAX_CATEGORIES", _DEFAULT_COLLECT_MAX_CATEGORIES)
COLLECT_MAX_CATEGORIES = (
    int(_collect_max_categories_raw) if _collect_max_categories_raw.strip() else None
)

# Drushim collection mode — on server, plain HTTP is fast and avoids Chromium RAM usage.
_DEFAULT_DRUSHIM_HTTP_FIRST = "true" if AGENT_CV_ID else "false"
DRUSHIM_HTTP_FIRST = os.getenv("DRUSHIM_HTTP_FIRST", _DEFAULT_DRUSHIM_HTTP_FIRST).lower() in (
    "1", "true", "yes",
)
_DEFAULT_DRUSHIM_BROWSER_FALLBACK = "false" if AGENT_CV_ID else "true"
DRUSHIM_BROWSER_FALLBACK = os.getenv(
    "DRUSHIM_BROWSER_FALLBACK", _DEFAULT_DRUSHIM_BROWSER_FALLBACK
).lower() in ("1", "true", "yes")

# Drushim Playwright timing (ms) — shorter waits on server to keep scans fast.
DRUSHIM_PAGE_WAIT_MS = int(os.getenv("DRUSHIM_PAGE_WAIT_MS", "1500"))
DRUSHIM_SELECTOR_TIMEOUT_MS = int(os.getenv("DRUSHIM_SELECTOR_TIMEOUT_MS", "10000"))
DRUSHIM_GOTO_TIMEOUT_MS = int(os.getenv("DRUSHIM_GOTO_TIMEOUT_MS", "45000"))
DRUSHIM_HTTP_TIMEOUT_SEC = int(os.getenv("DRUSHIM_HTTP_TIMEOUT_SEC", "25"))

# HTTP API server (api_server.py)
API_HOST = os.getenv("API_HOST", "127.0.0.1").strip()
API_PORT = int(os.getenv("API_PORT", "8000"))
