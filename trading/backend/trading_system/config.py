"""Deployment-agnostic configuration via environment variables."""

from __future__ import annotations

import os
from pathlib import Path

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

STARTING_CASH = float(os.getenv("TRADING_STARTING_CASH", "100000"))

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
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower()
AI_MODEL = os.getenv("AI_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
AI_MIN_INTERVAL_SECONDS = float(os.getenv("AI_MIN_INTERVAL_SECONDS", "90"))
AI_MAX_CALLS_PER_HOUR = int(os.getenv("AI_MAX_CALLS_PER_HOUR", "20"))
AI_PRICE_TRIGGER_PERCENT = float(os.getenv("AI_PRICE_TRIGGER_PERCENT", "0.35"))
AI_AGENT_WEIGHT = float(os.getenv("AI_AGENT_WEIGHT", "1.0"))
AI_CACHE_TTL_SECONDS = float(os.getenv("AI_CACHE_TTL_SECONDS", "180"))
