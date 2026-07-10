#!/usr/bin/env bash
# הפעלת Resume Agent עם מנהרת Cloudflare לגישה מהטלפון
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CLOUDFLARED="${CLOUDFLARED:-cloudflared}"

if ! command -v "$CLOUDFLARED" >/dev/null 2>&1; then
  echo "cloudflared לא מותקן. התקנה:"
  echo "  Linux:  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared && chmod +x cloudflared"
  echo "  macOS:  brew install cloudflared"
  exit 1
fi

cleanup() {
  echo ""
  echo "עוצר שרתים..."
  kill "$API_PID" "$WEB_PID" "$TUNNEL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "מפעיל API..."
(cd "$ROOT/ai-job-agent" && python3 src/api_server.py) &
API_PID=$!
sleep 2

echo "מפעיל אתר..."
(cd "$ROOT/resume-agent-web" && npm run dev -- --host 127.0.0.1 --port 5173) &
WEB_PID=$!
sleep 3

echo "פותח מנהרת Cloudflare..."
"$CLOUDFLARED" tunnel --url http://127.0.0.1:5173 2>&1 | tee /tmp/resume-agent-tunnel.log &
TUNNEL_PID=$!

for i in $(seq 1 15); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/resume-agent-tunnel.log 2>/dev/null | head -1 || true)
  if [ -n "$URL" ]; then
    echo ""
    echo "============================================"
    echo "  פתח בטלפון: $URL"
    echo "============================================"
    echo ""
    break
  fi
  sleep 1
done

wait
