# Resume Agent — full stack (React frontend + FastAPI backend)
#
# Persistence: SQLite DBs and uploaded CVs live under ai-job-agent/data/.
# Mount that directory as a Docker volume so user accounts, CVs, and job
# matches survive container restarts, for example:
#   docker run -v ./data:/app/ai-job-agent/data -p 8000:8000 resume-agent
# Or in Compose:
#   volumes:
#     - ./data:/app/ai-job-agent/data
# WhatsApp Monitor session/DB also persist under:
#   /app/ai-job-agent/data/whatsapp_monitor/
#
FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY resume-agent-web/package.json resume-agent-web/package-lock.json ./
RUN npm ci
COPY resume-agent-web/ ./
ENV VITE_API_URL=
RUN npm run build

# Isolated AI Trading System UI (served under /trading)
FROM node:22-bookworm-slim AS trading-frontend
WORKDIR /trading-web
COPY trading/frontend/package.json trading/frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY trading/frontend/ ./
RUN npm run build

# Isolated WhatsApp Monitor UI (served under /whatsapp-monitor via Node sidecar)
FROM node:22-bookworm-slim AS whatsapp-frontend
WORKDIR /wa-web
COPY whatsapp_monitor/frontend/package.json whatsapp_monitor/frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY whatsapp_monitor/frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim AS whatsapp-backend
WORKDIR /wa
COPY whatsapp_monitor/backend/package.json whatsapp_monitor/backend/package-lock.json* ./
# Skip bundled Chrome download — runtime uses Playwright/system Chromium.
ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi
COPY whatsapp_monitor/backend/ ./

# Playwright Python image version must match the pinned playwright package.
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy
WORKDIR /app/ai-job-agent

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Node.js for the WhatsApp Monitor sidecar (whatsapp-web.js).
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY ai-job-agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY ai-job-agent/ ./
COPY job-apply-automation/ /app/job-apply-automation/
COPY trading/ /app/trading/
COPY whatsapp_monitor/ /app/whatsapp_monitor/
COPY --from=whatsapp-backend /wa/ /app/whatsapp_monitor/backend/
COPY --from=whatsapp-frontend /wa-web/dist /app/whatsapp_monitor/frontend/dist
COPY --from=frontend /web/dist /app/resume-agent-web/dist
COPY --from=trading-frontend /trading-web/dist /app/trading/frontend/dist
ENV PYTHONPATH=/app/job-apply-automation/src:/app/trading/backend:${PYTHONPATH}

# Point puppeteer at Playwright's Chromium when available.
ENV PUPPETEER_SKIP_DOWNLOAD=true
ENV WHATSAPP_MONITOR_HOST=127.0.0.1
ENV WHATSAPP_MONITOR_PORT=3100
ENV WHATSAPP_MONITOR_BASE_PATH=/whatsapp-monitor
ENV WHATSAPP_MONITOR_UPSTREAM=http://127.0.0.1:3100

# Seed files to copy onto an empty persistent volume on first boot.
# (Mounting a volume hides image contents under data/.)
RUN mkdir -p /app/ai-job-agent/seed-data \
    && for f in synonym_dictionary.json; do \
         if [ -f "data/$f" ]; then cp "data/$f" "/app/ai-job-agent/seed-data/$f"; fi; \
       done \
    && mkdir -p /app/whatsapp_monitor/data /app/whatsapp_monitor/session /app/whatsapp_monitor/logs \
    && CHROME_BIN="$(find /ms-playwright -type f -name chrome -path '*/chrome-linux*/chrome' 2>/dev/null | head -n1 || true)" \
    && if [ -n "$CHROME_BIN" ]; then printf '%s\n' "$CHROME_BIN" > /app/whatsapp_monitor/.chrome_path; fi

COPY scripts/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Declare the data directory as a volume mount point (SQLite + uploads + WA session).
VOLUME ["/app/ai-job-agent/data"]

ENV API_HOST=0.0.0.0
ENV HEADLESS=true
ENV APPLY_HEADLESS=true
ENV PYTHONUNBUFFERED=1
ENV DRUSHIM_HTTP_FIRST=true
ENV DRUSHIM_BROWSER_FALLBACK=false
ENV COLLECT_MAX_QUERIES=6
ENV COLLECT_MAX_CATEGORIES=5
ENV LINKEDIN_MAX_PAGES=5
ENV GOTFRIENDS_ENABLED=false
ENV TRADING_BASE_PATH=/trading
ENV TRADING_DATA_DIR=/app/trading/data
# Set a strong JWT_SECRET in production so auth tokens cannot be forged.
# ENV JWT_SECRET=

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "src/api_server.py", "--host", "0.0.0.0"]
