"""Run the trading system as its own FastAPI app (post-extraction)."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import create_trading_router, mount_trading_frontend
from .config import PUBLIC_BASE_PATH

app = FastAPI(title="AI Trading System", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

base = PUBLIC_BASE_PATH
# When extracted, typically TRADING_BASE_PATH="" so routes sit at /api and /ws.
app.include_router(create_trading_router(), prefix=base)
mount_trading_frontend(app, base_path=base)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("TRADING_HOST", "0.0.0.0")
    port = int(os.getenv("TRADING_PORT", "8100"))
    uvicorn.run(app, host=host, port=port)
