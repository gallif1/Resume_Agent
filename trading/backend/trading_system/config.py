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

# Symbols and tick interval for the simulated market feed.
DEFAULT_SYMBOLS = tuple(
    s.strip().upper()
    for s in os.getenv("TRADING_SYMBOLS", "BTC-USD,ETH-USD,SOL-USD,AAPL,NVDA").split(",")
    if s.strip()
)
TICK_INTERVAL_SEC = float(os.getenv("TRADING_TICK_INTERVAL_SEC", "1.0"))

# Starting cash for paper portfolio.
STARTING_CASH = float(os.getenv("TRADING_STARTING_CASH", "100000"))
