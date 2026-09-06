# AI Trading System

Isolated multi-agent paper-trading system. Temporarily hosted under the Resume Agent
deployment at `/trading`. Designed for later extraction into its own repository.

## Layout

```
trading/
  backend/trading_system/   # FastAPI router + engines (no Resume Agent imports)
  frontend/                 # React + Vite dashboard (base path /trading/)
  data/                     # Runtime state / paper trades (local SQLite JSON)
  tests/
```

## Components

| Module | Role |
|--------|------|
| Market Feed | Simulated live ticks for configured symbols |
| Event Engine | Turns ticks into market events |
| Agents | Momentum, mean-reversion, and volatility agents vote |
| Decision Engine | Aggregates votes into BUY / SELL / HOLD |
| Runtime | START / PAUSE control loop + WebSocket fan-out |
| Dashboard | Live UI at `/trading` |

## Standalone run (future extraction)

```bash
# Backend
cd trading/backend
pip install -r requirements.txt
PYTHONPATH=. uvicorn trading_system.standalone:app --host 0.0.0.0 --port 8100

# Frontend
cd trading/frontend
npm install
npm run dev
```

## Hosted under Resume Agent

The host FastAPI app mounts:

- `GET/POST /trading/api/*` — REST control + snapshot
- `WS /trading/ws` — live stream
- Static UI at `/trading/`

No Resume Agent business modules or database tables are used.
