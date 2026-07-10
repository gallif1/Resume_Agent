# Resume Agent — single container: FastAPI backend + built React frontend + Playwright
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY resume-agent-web/package.json resume-agent-web/package-lock.json ./
RUN npm ci
COPY resume-agent-web/ ./
# Same-origin API in production (no separate frontend URL)
ENV VITE_API_URL=
RUN npm run build

FROM mcr.microsoft.com/playwright/python:v1.51.0-noble
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    LOGS_DIR=/data/logs \
    STATIC_DIR=/app/static \
    API_HOST=0.0.0.0 \
    HEADLESS=true

COPY ai-job-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai-job-agent/ .
COPY --from=frontend-build /frontend/dist /app/static

RUN mkdir -p /data /data/logs

EXPOSE 8000
CMD ["python", "src/api_server.py"]
