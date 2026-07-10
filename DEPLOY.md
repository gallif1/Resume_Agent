# פריסה ל-Render (כתובת קבועה + אחסון בענן)

מדריך זה מפרוס את **Resume Agent** כשירות אחד ב-Render:
- ממשק משתמש (React) + API (FastAPI) בכתובת אחת
- דאטאבייס SQLite על **דיסק קבוע** (persistent disk) — הנתונים נשמרים בין פריסות

## דרישות

- חשבון [Render](https://render.com)
- חשבון GitHub עם הריפו `Resume_Agent`
- תוכנית **Starter** ($7/חודש) — נדרשת לדיסק קבוע (ב-Free tier הנתונים נמחקים בכל deploy)
- (אופציונלי) מפתח OpenAI לניתוח חכם של קו"ח

## שלב 1 — חבר את הריפו ל-Render

1. היכנס ל-[Render Dashboard](https://dashboard.render.com)
2. לחץ **New +** → **Blueprint**
3. חבר את GitHub ובחר את הריפו `Resume_Agent`
4. Render יזהה את `render.yaml` ויציע ליצור שירות `resume-agent`
5. הוסף משתנה סודי:
   - `OPENAI_API_KEY` — מפתח OpenAI (אופציונלי אבל מומלץ)
6. לחץ **Apply**

## שלב 2 — קבל כתובת קבועה

אחרי הבנייה (5–10 דקות בפעם הראשונה), תקבל כתובת קבועה:

```
https://resume-agent-xxxx.onrender.com
```

זו הכתובת לפתיחה מהטלפון או מהמחשב.

## איפה הנתונים נשמרים?

| נתון | מיקום בענן |
|------|------------|
| רישום קו"ח | `/data/registry.db` |
| משרות והתאמות (לכל קו"ח) | `/data/cvs/<cv_id>/jobs.db` |
| קבצי קו"ח שהועלו | `/data/cvs/<cv_id>/resume.*` |
| ניתוחי AI | `/data/cvs/<cv_id>/cv_profile.json` וכו' |
| לוגים | `/data/logs/` |

הכל על **Persistent Disk** (1GB) שמחובר לשירות — הנתונים שורדים גם אחרי deploy מחדש.

> **למה לא PostgreSQL?** הפרויקט בנוי על SQLite עם קובץ DB נפרד לכל קו"ח. מעבר ל-PostgreSQL דורש שכתוב מלא של שכבת הנתונים. לשימוש אישי/צוות קטן, SQLite על דיסק קבוע הוא הפתרון הפשוט והיציב.

## עלויות משוערות (Render)

| רכיב | מחיר |
|------|------|
| Web Service (Starter) | ~$7/חודש |
| Persistent Disk (1GB) | ~$0.25/חודש |
| **סה"כ** | **~$7.25/חודש** |

> שירותי Render ב-Free tier «נרדמים» אחרי חוסר פעילות (הטעינה הראשונה איטית). ב-Starter השירות תמיד פעיל.

## משתני סביבה

| משתנה | ברירת מחדל | תיאור |
|--------|------------|--------|
| `DATA_DIR` | `/data` | תיקיית נתונים על הדיסק הקבוע |
| `OPENAI_API_KEY` | — | ניתוח קו"ח והתאמות AI |
| `HEADLESS` | `true` | דפדפן ללא ממשק (נדרש בשרת) |
| `LINKEDIN_ENABLED` | `true` | איסוף מ-LinkedIn |
| `GOTFRIENDS_ENABLED` | `true` | איסוף מ-GotFriends |

## מגבלות ידועות

1. **דרושים** — איסוף מדרושים דרך Playwright עלול להיחסם (captcha). LinkedIn ו-GotFriends עובדים טוב יותר בענן.
2. **שליחת קו"ח אוטומטית** (`apply_jobs.py`) — לא זמינה בענן (דורשת דפדפן אינטראקטיבי). הרץ מקומית במחשב.
3. **סריקות ארוכות** — איסוף משרות יכול לקחת כמה דקות; השאר את הדף פתוח.

## פריסה ידנית (ללא Blueprint)

אם מעדיפים ליצור שירות ידנית:

1. **New +** → **Web Service**
2. Runtime: **Docker**
3. Dockerfile path: `./Dockerfile`
4. הוסף **Disk** → Mount path: `/data`, Size: 1GB
5. Environment: כמו ב-`render.yaml`
6. Plan: **Starter**

## בדיקה מקומית עם Docker

```bash
docker build -t resume-agent .
docker run -p 8000:8000 -v resume-agent-data:/data resume-agent
```

פתח http://localhost:8000

## עדכון גרסה

כל `git push` ל-`master` מפעיל deploy אוטומטי (אם הפעלת Auto-Deploy ב-Render). הנתונים על הדיסק הקבוע נשמרים.
