# WhatsApp Monitor

Isolated subsystem that connects to **your** WhatsApp account via WhatsApp Web,
monitors selected groups, and stores **new** messages for display.

It does **not** analyze jobs, use AI, send messages, or modify the existing
Resume Agent job pipeline.

## Layout

```
whatsapp_monitor/
  backend/     Node.js API + whatsapp-web.js listener
  frontend/    React dashboard (base path /whatsapp-monitor/)
  data/        SQLite DB (whatsapp_messages.db)
  session/     LocalAuth session (gitignored — never commit)
  logs/        monitor.log
```

## Requirements

- Node.js **≥ 18** (tested with Node 22)
- Chromium/Chrome for Puppeteer (whatsapp-web.js)

## Local development

```bash
# Terminal 1 — WhatsApp Monitor backend
cd whatsapp_monitor/backend
npm install
npm start
# listens on 127.0.0.1:3100

# Terminal 2 — WhatsApp Monitor UI (optional direct)
cd whatsapp_monitor/frontend
npm install
npm run build   # production assets served by backend
# or: npm run dev  # http://127.0.0.1:5175/whatsapp-monitor/

# Terminal 3 — Resume Agent (proxies /whatsapp-monitor → :3100)
cd ai-job-agent
pip install -r requirements.txt
python src/api_server.py

# Terminal 4 — main frontend (nav link)
cd resume-agent-web
npm run dev
```

Open **WhatsApp Monitor** from the Resume Agent header, or go to
`/whatsapp-monitor`.

## Usage

1. Open WhatsApp Monitor → status shows NOT CONNECTED / QR.
2. Scan the QR with WhatsApp → Linked devices.
3. Groups load automatically after CONNECTED.
4. Select groups → **Save Selection**.
5. Press **START** to record **new** messages only (no history scrape).
6. **PAUSE** keeps the WhatsApp session but stops recording.
7. **STOP** stops recording (session remains on disk).
8. **CLEAR** deletes stored messages after confirmation (session kept).

After a full process restart, monitoring starts as **STOPPED** — press START again.

## Docker

The main `Dockerfile` builds the WhatsApp frontend/backend and the entrypoint
starts the Node sidecar on `127.0.0.1:3100`. FastAPI reverse-proxies
`/whatsapp-monitor`.

Session + DB persist under the existing data volume:

`/app/ai-job-agent/data/whatsapp_monitor/{data,session,logs}`

## Security

- Session files stay server-side under `session/` and are gitignored.
- APIs never return auth tokens, cookies, or session file contents.
- The Node service binds to localhost inside Docker; only `/whatsapp-monitor`
  is exposed through the main app.
