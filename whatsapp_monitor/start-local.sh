#!/usr/bin/env bash
# Start the WhatsApp Monitor local agent on THIS computer (client-side).
# The cloud/EC2 UI will talk to http://127.0.0.1:3100 from your browser.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT/backend"
if [[ ! -d node_modules ]]; then
  if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
fi
export WHATSAPP_MONITOR_HOST="${WHATSAPP_MONITOR_HOST:-127.0.0.1}"
export WHATSAPP_MONITOR_PORT="${WHATSAPP_MONITOR_PORT:-3100}"
export WHATSAPP_MONITOR_BASE_PATH="${WHATSAPP_MONITOR_BASE_PATH:-/whatsapp-monitor}"
echo "WhatsApp local agent → http://${WHATSAPP_MONITOR_HOST}:${WHATSAPP_MONITOR_PORT}${WHATSAPP_MONITOR_BASE_PATH}"
echo "Keep this process running, then open WhatsApp Monitor in the Resume Agent UI."
exec npm start
