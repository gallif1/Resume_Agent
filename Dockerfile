# Resume Agent — full stack (React frontend + FastAPI backend)
FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY resume-agent-web/package.json resume-agent-web/package-lock.json ./
RUN npm ci
COPY resume-agent-web/ ./
ENV VITE_API_URL=
RUN npm run build

FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy
WORKDIR /app/ai-job-agent

COPY ai-job-agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ai-job-agent/ ./
COPY --from=frontend /web/dist /app/resume-agent-web/dist

ENV API_HOST=0.0.0.0
ENV HEADLESS=true
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "src/api_server.py", "--host", "0.0.0.0"]
