# WhatsApp Monitor

Isolated subsystem that connects to **your** WhatsApp account via WhatsApp Web,
monitors selected groups, and stores **new** messages for display.

**Runs on the cloud server** (EC2 / Docker). The browser opens `/whatsapp-monitor`;
FastAPI reverse-proxies to a Node sidecar on `127.0.0.1:3100` inside the container.

## Layout

```
whatsapp_monitor/
  backend/     Node.js API + whatsapp-web.js listener
  frontend/    React dashboard (served under /whatsapp-monitor/)
  data/        SQLite (persisted under ai-job-agent/data/whatsapp_monitor on Docker)
  session/     LocalAuth session (gitignored)
  logs/
```

## Cloud behaviour

1. Open **WhatsApp Monitor** in the Resume Agent UI.
2. The server starts the Node sidecar automatically if needed (downloads Node.js
   when missing, installs npm deps, launches Chromium via Playwright browsers).
3. Scan the QR code → select groups → **START**.

Session and DB persist on the server volume:
`/app/ai-job-agent/data/whatsapp_monitor/`.

## Local development

```bash
cd whatsapp_monitor/frontend && npm install && npm run build
cd ../backend && npm install && npm start   # :3100
# then start Resume Agent API — it proxies /whatsapp-monitor
```

## Notes

- No job AI / outbound WhatsApp messages in this MVP.
- Session files are never exposed via HTTP APIs.
