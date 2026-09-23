# WhatsApp Monitor

Isolated subsystem that connects to **your** WhatsApp account via WhatsApp Web,
monitors selected groups, and stores **new** messages for display.

**WhatsApp auth runs on your PC (client-side local agent)** — not on the EC2/cloud
server. The hosted UI only shows the dashboard; Chromium + QR + session stay local.

## Layout

```
whatsapp_monitor/
  backend/     Node.js local agent (whatsapp-web.js) on 127.0.0.1:3100
  frontend/    React dashboard (hosted under /whatsapp-monitor/)
  data/        SQLite DB on your PC
  session/     LocalAuth session on your PC (gitignored)
  logs/        monitor.log
  start-local.sh
```

## How it works

1. Open **WhatsApp Monitor** in Resume Agent (even on the cloud host).
2. On **your computer**, start the local agent:

```bash
./whatsapp_monitor/start-local.sh
# or: cd whatsapp_monitor/backend && npm install && npm start
```

3. The browser talks to `http://127.0.0.1:3100` (your machine).
4. Scan the QR, select groups, press **START**.

## Requirements

- Node.js **≥ 18** on the PC that runs the agent
- Chrome/Chromium for Puppeteer

## Notes

- Session files never leave your PC.
- The cloud server does **not** need Node.js for WhatsApp.
- Do not analyze jobs / send WhatsApp messages in this MVP.
