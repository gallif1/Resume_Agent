#!/usr/bin/env bash
# Start the WhatsApp Monitor Node sidecar locally (dev / debugging).
# Production runs this same process on the cloud host behind /whatsapp-monitor.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT/backend"
if [[ ! -d node_modules ]]; then
  if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
fi
export WHATSAPP_MONITOR_HOST="${WHATSAPP_MONITOR_HOST:-127.0.0.1}"
export WHATSAPP_MONITOR_PORT="${WHATSAPP_MONITOR_PORT:-3100}"
export WHATSAPP_MONITOR_BASE_PATH="${WHATSAPP_MONITOR_BASE_PATH:-/whatsapp-monitor}"
echo "WhatsApp Monitor → http://${WHATSAPP_MONITOR_HOST}:${WHATSAPP_MONITOR_PORT}${WHATSAPP_MONITOR_BASE_PATH}"
exec npm start
