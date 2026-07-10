#!/usr/bin/env bash
# מפעיל את Backend + Frontend ומייצר קישור ציבורי זמני (Cloudflare Tunnel).
# שימוש: ./scripts/share-dev.sh
# עצירה: Ctrl+C

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
API_PID=""
WEB_PID=""
TUNNEL_PID=""

cleanup() {
  echo ""
  echo "עוצר שרתים..."
  [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" 2>/dev/null || true
  [[ -n "$WEB_PID" ]] && kill "$WEB_PID" 2>/dev/null || true
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

port_open() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn | grep -q ":${port} "
  else
    curl -s -o /dev/null "http://127.0.0.1:${port}/" 2>/dev/null || \
    curl -s -o /dev/null "http://127.0.0.1:${port}/api/health" 2>/dev/null
  fi
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local i
  for i in $(seq 1 30); do
    if curl -s -o /dev/null -w "" "$url" 2>/dev/null; then
      echo "✓ ${label} מוכן"
      return 0
    fi
    sleep 1
  done
  echo "✗ ${label} לא עלה בזמן — בדוק שגיאות למעלה"
  return 1
}

echo "=== Resume Agent — שיתוף לפלאפון ==="
echo ""

# Backend
if port_open "$API_PORT"; then
  echo "✓ Backend כבר רץ על פורט ${API_PORT}"
else
  echo "→ מפעיל Backend על פורט ${API_PORT}..."
  (
    cd "$ROOT/ai-job-agent"
    export PATH="${HOME}/.local/bin:${PATH}"
    python3 src/api_server.py --port "$API_PORT"
  ) &
  API_PID=$!
  wait_for_url "http://127.0.0.1:${API_PORT}/api/health" "Backend"
fi

# Frontend
if port_open "$WEB_PORT"; then
  echo "✓ Frontend כבר רץ על פורט ${WEB_PORT}"
else
  echo "→ מפעיל Frontend על פורט ${WEB_PORT}..."
  (
    cd "$ROOT/resume-agent-web"
    npm run dev -- --host 127.0.0.1 --port "$WEB_PORT"
  ) &
  WEB_PID=$!
  wait_for_url "http://127.0.0.1:${WEB_PORT}/" "Frontend"
fi

echo ""
echo "→ יוצר קישור ציבורי (Cloudflare Tunnel)..."
echo "  (זה לוקח כמה שניות)"
echo ""

TUNNEL_LOG="$(mktemp)"
npx --yes cloudflared tunnel --url "http://127.0.0.1:${WEB_PORT}" 2>&1 | tee "$TUNNEL_LOG" &
TUNNEL_PID=$!

PUBLIC_URL=""
for _ in $(seq 1 40); do
  PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)"
  if [[ -n "$PUBLIC_URL" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "✗ לא הצלחתי לייצר קישור. בדוק את הלוג:"
  tail -20 "$TUNNEL_LOG"
  exit 1
fi

echo ""
echo "════════════════════════════════════════"
echo "  הקישור שלך לפלאפון:"
echo ""
echo "  $PUBLIC_URL"
echo ""
echo "════════════════════════════════════════"
echo ""
echo "מקומי במחשב: http://127.0.0.1:${WEB_PORT}"
echo "השאר את הטרמינל פתוח — Ctrl+C לעצירה"
echo ""

wait "$TUNNEL_PID"
