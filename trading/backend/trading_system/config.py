"""Deployment-agnostic configuration via environment variables."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

# Load the same .env files Resume Agent uses so OPENAI_API_KEY / FINNHUB_API_KEY
# are visible when trading is mounted inside api_server.
_CONFIG_DIR = Path(__file__).resolve().parent
_ENV_CANDIDATES = [
    Path("/app/ai-job-agent/.env"),  # Docker
    _CONFIG_DIR.parents[2] / "ai-job-agent" / ".env",  # repo: ai-job-agent/.env
    _CONFIG_DIR.parents[2] / ".env",
    _CONFIG_DIR.parents[1] / ".env",  # trading/backend/.env
    _CONFIG_DIR.parents[2] / "trading" / ".env",
    Path.cwd() / ".env",
]
if load_dotenv is not None:
    for _env_path in _ENV_CANDIDATES:
        if _env_path.is_file():
            load_dotenv(_env_path, override=False)

# Public URL path prefix when hosted under Resume Agent (or alone at root).
PUBLIC_BASE_PATH = os.getenv("TRADING_BASE_PATH", "/trading").rstrip("/") or ""

# Where paper-trading state is stored (never under Resume Agent data/).
_DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data"
DATA_DIR = Path(os.getenv("TRADING_DATA_DIR", str(_DEFAULT_DATA))).expanduser()

# Resume Agent home (for "back" link). Empty = same origin "/".
RESUME_AGENT_HOME_URL = os.getenv("RESUME_AGENT_HOME_URL", "/")

DEFAULT_SYMBOLS = tuple(
    s.strip().upper()
    for s in os.getenv("TRADING_SYMBOLS", "BTC-USD,ETH-USD,SOL-USD,AAPL,NVDA").split(",")
    if s.strip()
)

# Internal decision-loop cadence (reads cache — does NOT hit market APIs).
TICK_INTERVAL_SEC = float(os.getenv("TRADING_TICK_INTERVAL_SEC", "2.0"))

# Minimum seconds between paper fills on the same symbol (prevents 2%-of-cash spam).
FILL_COOLDOWN_SEC = float(os.getenv("TRADING_FILL_COOLDOWN_SEC", "45"))

# Rolling structured decision logs kept in memory for UI / copy-export.
DECISION_LOG_LIMIT = int(os.getenv("TRADING_DECISION_LOG_LIMIT", "200"))

# Minimum seconds between full log rows when nothing meaningful changed.
DECISION_LOG_HEARTBEAT_SECONDS = float(os.getenv("DECISION_LOG_HEARTBEAT_SECONDS", "45"))

STARTING_CASH = float(os.getenv("TRADING_STARTING_CASH", "100000"))

# Forward outcome horizons (label=seconds). Used for accuracy tracking only.
_OUTCOME_RAW = os.getenv("OUTCOME_HORIZONS_SEC", "5m:300,15m:900,60m:3600")
OUTCOME_HORIZONS_SEC: list[tuple[str, float]] = []
for _part in _OUTCOME_RAW.split(","):
    _part = _part.strip()
    if not _part or ":" not in _part:
        continue
    _label, _secs = _part.split(":", 1)
    try:
        OUTCOME_HORIZONS_SEC.append((_label.strip(), float(_secs)))
    except ValueError:
        continue
if not OUTCOME_HORIZONS_SEC:
    OUTCOME_HORIZONS_SEC = [("5m", 300.0), ("15m", 900.0), ("60m", 3600.0)]

OUTCOME_RESOLVE_INTERVAL_SEC = float(os.getenv("OUTCOME_RESOLVE_INTERVAL_SEC", "30"))

# --- Real market data ---
# Set TRADING_USE_SIMULATED_FEED=true only for offline unit tests.
USE_SIMULATED_FEED = os.getenv("TRADING_USE_SIMULATED_FEED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CRYPTO_POLL_INTERVAL_SECONDS = float(os.getenv("CRYPTO_POLL_INTERVAL_SECONDS", "30"))
STOCK_POLL_INTERVAL_SECONDS = float(os.getenv("STOCK_POLL_INTERVAL_SECONDS", "60"))
STALE_AFTER_SECONDS = float(os.getenv("TRADING_STALE_AFTER_SECONDS", "120"))
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
DEFAULT_CHART_TIMEFRAME = os.getenv("TRADING_CHART_TIMEFRAME", "5m").strip() or "5m"

# --- AI Market Analyst (LLM) ---
# Reuses the same OPENAI_API_KEY as Resume Agent.
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower()
AI_MODEL = os.getenv("AI_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
AI_MIN_INTERVAL_SECONDS = float(os.getenv("AI_MIN_INTERVAL_SECONDS", "90"))
# 0 (or negative) = unlimited hourly AI calls
AI_MAX_CALLS_PER_HOUR = int(os.getenv("AI_MAX_CALLS_PER_HOUR", "0"))
AI_PRICE_TRIGGER_PERCENT = float(os.getenv("AI_PRICE_TRIGGER_PERCENT", "0.35"))
AI_AGENT_WEIGHT = float(os.getenv("AI_AGENT_WEIGHT", "1.0"))
AI_CACHE_TTL_SECONDS = float(os.getenv("AI_CACHE_TTL_SECONDS", "180"))
# Max age of an AI analysis that can be reused for pre-trade validation.
AI_PRETRADE_MAX_AGE_SECONDS = float(os.getenv("AI_PRETRADE_MAX_AGE_SECONDS", "90"))

# --- Analysis timeframe (agents) vs chart timeframe ---
# Agents use this TF for FeatureSnapshot / decisions. Chart may differ.
ANALYSIS_TIMEFRAME = os.getenv("TRADING_ANALYSIS_TIMEFRAME", "5m").strip() or "5m"

# DecisionEngine: reject executable BUY/SELL below this calibrated confidence.
MIN_EXECUTION_CONFIDENCE = float(os.getenv("TRADING_MIN_EXECUTION_CONFIDENCE", "0.55"))

# Max age (seconds) for a vote's market_timestamp vs decision snapshot.
VOTE_SNAPSHOT_TOLERANCE_SEC = float(os.getenv("TRADING_VOTE_SNAPSHOT_TOLERANCE_SEC", "120"))

# Heuristic confidence cap (never claim near-certainty from indicators alone).
HEURISTIC_CONFIDENCE_CAP = float(os.getenv("TRADING_HEURISTIC_CONFIDENCE_CAP", "0.92"))

# --- Momentum agent scoring weights (must sum ≈ 1.0) ---
MOMENTUM_W_PRICE = float(os.getenv("TRADING_MOMENTUM_W_PRICE", "0.30"))
MOMENTUM_W_EMA = float(os.getenv("TRADING_MOMENTUM_W_EMA", "0.25"))
MOMENTUM_W_MACD = float(os.getenv("TRADING_MOMENTUM_W_MACD", "0.20"))
MOMENTUM_W_RSI = float(os.getenv("TRADING_MOMENTUM_W_RSI", "0.15"))
MOMENTUM_W_VOLUME = float(os.getenv("TRADING_MOMENTUM_W_VOLUME", "0.10"))
MOMENTUM_BUY_THRESHOLD = float(os.getenv("TRADING_MOMENTUM_BUY_THRESHOLD", "0.35"))
MOMENTUM_SELL_THRESHOLD = float(os.getenv("TRADING_MOMENTUM_SELL_THRESHOLD", "-0.35"))
MOMENTUM_PRICE_SCALE_PCT = float(os.getenv("TRADING_MOMENTUM_PRICE_SCALE_PCT", "0.50"))

# --- Mean reversion agent ---
MEANREV_W_SMA20 = float(os.getenv("TRADING_MEANREV_W_SMA20", "0.30"))
MEANREV_W_BB = float(os.getenv("TRADING_MEANREV_W_BB", "0.30"))
MEANREV_W_RSI = float(os.getenv("TRADING_MEANREV_W_RSI", "0.20"))
MEANREV_W_VWAP = float(os.getenv("TRADING_MEANREV_W_VWAP", "0.20"))
MEANREV_BUY_THRESHOLD = float(os.getenv("TRADING_MEANREV_BUY_THRESHOLD", "0.35"))
MEANREV_SELL_THRESHOLD = float(os.getenv("TRADING_MEANREV_SELL_THRESHOLD", "-0.35"))
MEANREV_SMA_DEV_SCALE_PCT = float(os.getenv("TRADING_MEANREV_SMA_DEV_SCALE_PCT", "1.2"))
MEANREV_VWAP_DEV_SCALE_PCT = float(os.getenv("TRADING_MEANREV_VWAP_DEV_SCALE_PCT", "0.8"))
MEANREV_TREND_DAMPEN = float(os.getenv("TRADING_MEANREV_TREND_DAMPEN", "0.45"))

# --- Volatility agent regimes (ATR % of price) ---
VOL_ATR_LOW = float(os.getenv("TRADING_VOL_ATR_LOW", "0.35"))
VOL_ATR_HIGH = float(os.getenv("TRADING_VOL_ATR_HIGH", "1.5"))
VOL_ATR_EXTREME = float(os.getenv("TRADING_VOL_ATR_EXTREME", "3.0"))
VOL_BB_WIDTH_HIGH = float(os.getenv("TRADING_VOL_BB_WIDTH_HIGH", "3.5"))
VOL_BB_WIDTH_EXTREME = float(os.getenv("TRADING_VOL_BB_WIDTH_EXTREME", "7.0"))

# --- Risk engine ---
RISK_DEFAULT_ALLOCATION_PCT = float(os.getenv("TRADING_RISK_ALLOCATION_PCT", "0.02"))
RISK_ATR_STOP_MULT = float(os.getenv("TRADING_RISK_ATR_STOP_MULT", "2.0"))
RISK_REWARD_RATIO = float(os.getenv("TRADING_RISK_REWARD_RATIO", "2.0"))
RISK_MIN_NOTIONAL = float(os.getenv("TRADING_RISK_MIN_NOTIONAL", "1.0"))
