#!/bin/sh
# Seed config files onto a fresh persistent volume without overwriting user data.
# Also starts the isolated WhatsApp Monitor Node sidecar (localhost only).
set -eu

SEED_DIR="/app/ai-job-agent/seed-data"
DATA_DIR="/app/ai-job-agent/data"

mkdir -p "$DATA_DIR"

if [ -d "$SEED_DIR" ]; then
  for src in "$SEED_DIR"/*; do
    [ -e "$src" ] || continue
    name=$(basename "$src")
    dest="$DATA_DIR/$name"
    if [ ! -e "$dest" ]; then
      cp -a "$src" "$dest"
    fi
  done
fi

# Persist WhatsApp Monitor DB + session on the same volume as Resume Agent data.
WA_ROOT="${WHATSAPP_PERSIST_ROOT:-$DATA_DIR/whatsapp_monitor}"
mkdir -p "$WA_ROOT/data" "$WA_ROOT/session" "$WA_ROOT/logs"
export WHATSAPP_DATA_DIR="${WHATSAPP_DATA_DIR:-$WA_ROOT/data}"
export WHATSAPP_SESSION_DIR="${WHATSAPP_SESSION_DIR:-$WA_ROOT/session}"
export WHATSAPP_LOG_DIR="${WHATSAPP_LOG_DIR:-$WA_ROOT/logs}"
export WHATSAPP_MONITOR_HOST="${WHATSAPP_MONITOR_HOST:-127.0.0.1}"
export WHATSAPP_MONITOR_PORT="${WHATSAPP_MONITOR_PORT:-3100}"
export WHATSAPP_MONITOR_BASE_PATH="${WHATSAPP_MONITOR_BASE_PATH:-/whatsapp-monitor}"
export WHATSAPP_MONITOR_UPSTREAM="${WHATSAPP_MONITOR_UPSTREAM:-http://127.0.0.1:3100}"

WA_BACKEND="/app/whatsapp_monitor/backend"
if [ -f "$WA_BACKEND/src/index.js" ] && command -v node >/dev/null 2>&1; then
  echo "[entrypoint] starting WhatsApp Monitor on ${WHATSAPP_MONITOR_HOST}:${WHATSAPP_MONITOR_PORT}"
  (
    cd "$WA_BACKEND"
    # Prefer system Chromium when available (Docker image).
    if [ -z "${PUPPETEER_EXECUTABLE_PATH:-}" ]; then
      if [ -f /app/whatsapp_monitor/.chrome_path ]; then
        export PUPPETEER_EXECUTABLE_PATH="$(cat /app/whatsapp_monitor/.chrome_path)"
      else
        for candidate in /usr/bin/chromium /usr/bin/chromium-browser /usr/bin/google-chrome /usr/bin/google-chrome-stable; do
          if [ -x "$candidate" ]; then
            export PUPPETEER_EXECUTABLE_PATH="$candidate"
            break
          fi
        done
        if [ -z "${PUPPETEER_EXECUTABLE_PATH:-}" ]; then
          FOUND="$(find /ms-playwright -type f -name chrome -path '*/chrome-linux*/chrome' 2>/dev/null | head -n1 || true)"
          if [ -n "$FOUND" ]; then
            export PUPPETEER_EXECUTABLE_PATH="$FOUND"
          fi
        fi
      fi
    fi
    exec node src/index.js
  ) >>"$WHATSAPP_LOG_DIR/monitor.stdout.log" 2>&1 &
  echo $! > /tmp/whatsapp-monitor.pid
else
  echo "[entrypoint] WhatsApp Monitor backend not present — skipping sidecar"
fi

exec "$@"
