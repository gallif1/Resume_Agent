# Resume Agent

מערכת מלאה לניהול קורות חיים, התאמת משרות והגשת מועמדויות.

## מבנה הפרויקט

```
Resume_Agent/
  ai-job-agent/      # Backend — Python, FastAPI, Playwright, matching pipeline
  resume-agent-web/  # Frontend — React + TypeScript + Vite
```

## הרצה מהירה

### Backend (API)

```bash
cd ai-job-agent
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # הוסף מפתחות API לפי הצורך
python src/api_server.py   # http://localhost:8000
```

### Frontend

```bash
cd resume-agent-web
npm install
npm run dev   # http://localhost:5173
```

## תיעוד מפורט

- [ai-job-agent/README.md](ai-job-agent/README.md) — פייפליין איסוף משרות, ניתוח קו"ח והגשה
- [resume-agent-web/README.md](resume-agent-web/README.md) — ממשק המשתמש בעברית
