# Resume Agent — full stack (React frontend + FastAPI backend)
#
# Persistence: SQLite DBs and uploaded CVs live under ai-job-agent/data/.
# Mount that directory as a Docker volume so user accounts, CVs, and job
# matches survive container restarts, for example:
#   docker run -v ./data:/app/ai-job-agent/data -p 8000:8000 resume-agent
# Or in Compose:
#   volumes:
#     - ./data:/app/ai-job-agent/data
#
FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY resume-agent-web/package.json resume-agent-web/package-lock.json ./
RUN npm ci
COPY resume-agent-web/ ./
ENV VITE_API_URL=
RUN npm run build

# Playwright Python image version must match the pinned playwright package.
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy
WORKDIR /app/ai-job-agent

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY ai-job-agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY ai-job-agent/ ./
COPY --from=frontend /web/dist /app/resume-agent-web/dist

# Declare the data directory as a volume mount point (SQLite + uploads).
VOLUME ["/app/ai-job-agent/data"]

ENV API_HOST=0.0.0.0
ENV HEADLESS=true
ENV APPLY_HEADLESS=true
ENV PYTHONUNBUFFERED=1
ENV DRUSHIM_HTTP_FIRST=true
ENV DRUSHIM_BROWSER_FALLBACK=false
ENV COLLECT_MAX_QUERIES=2
ENV COLLECT_MAX_CATEGORIES=1
ENV GOTFRIENDS_ENABLED=false
# Set a strong JWT_SECRET in production so auth tokens cannot be forged.
# ENV JWT_SECRET=

EXPOSE 8000
CMD ["python", "src/api_server.py", "--host", "0.0.0.0"]
