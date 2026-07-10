# Resume Agent — Web Client

צד לקוח (Frontend) עבור פרויקט Resume Agent, בנוי ב-**React + TypeScript + Vite**.
נפרד לחלוטין מהבק-אנד ומנוהל כריפוזיטורי עצמאי.

## תכונות

- **מרובה קבצי קו"ח** — כל קובץ עם פרופיל, סריקות והתאמות נפרדות
- **התאמות לפי קובץ** — כל משרה שייכת לקובץ קורות חיים ספציפי
- **הרצת סוכן** — סריקה ודירוג משרות לכל קובץ בנפרד
- ממשק בעברית מלא (RTL)

## הרצה

1. הפעל את שרת ה-API של הסוכן (בריפו `ai-job-agent`):

```bash
python src/api_server.py     # http://localhost:8000
```

2. הפעל את האתר:

```bash
npm install
npm run dev
```

האתר יעלה בכתובת http://localhost:5173.

### שיתוף לפלאפון

```bash
npm run dev:public
```

מפעיל Backend + Frontend ומדפיס קישור ציבורי זמני (`trycloudflare.com`).
ראה גם `../scripts/share-dev.sh` ו-`../README.md`.

## בנייה לפרודקשן

```bash
npm run build
npm run preview
```

## מבנה הפרויקט

```
src/
  App.tsx                  # מסך ראשי + חיווי חיבור לשרת
  components/
    CvManager.tsx          # העלאה ורשימת קבצי קו"ח
    CvDetails.tsx          # התאמות וסטטוס הגשה לקובץ ספציפי
    PipelineProgress.tsx   # התקדמות סריקה חיה
  lib/
    api.ts                 # קריאות ל-API של הסוכן (FastAPI)
  index.css                # עיצוב
```
