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

### שיתוף לפלאפון (קישור ציבורי זמני)

מהשורש של הפרויקט, פקודה אחת מפעילה הכל ומדפיסה קישור:

```bash
./scripts/share-dev.sh
```

או מתוך `resume-agent-web`:

```bash
npm run dev:public
```

הסקריפט מפעיל את ה-Backend, את ה-Frontend, ויוצר קישור `trycloudflare.com` שאפשר לפתוח בפלאפון.  
השאר את הטרמינל פתוח — `Ctrl+C` עוצר הכל.

> **הערה:** הקישור זמני ומשתנה בכל הפעלה. לשימוש קבוע כדאי לפרוס ל-Vercel/Netlify או להגדיר tunnel קבוע ב-Cloudflare.

## תיעוד מפורט

- [ai-job-agent/README.md](ai-job-agent/README.md) — פייפליין איסוף משרות, ניתוח קו"ח והגשה
- [resume-agent-web/README.md](resume-agent-web/README.md) — ממשק המשתמש בעברית
