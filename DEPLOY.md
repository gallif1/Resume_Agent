# פריסת האתר — בלי טרמינל

אם אין לך גישה לטרמינל (למשל אתה בפלאפון), אפשר לפרוס את האתר **בלחיצה אחת** ולקבל קישור קבוע.

## שלב 1 — מיזוג השינויים

ודא שה-PR האחרון ממוזג ל-`master` ב-GitHub.

## שלב 2 — פריסה ב-Render (חינם)

1. פתח בדפדפן (גם מהפלאפון):

   **[לחץ כאן לפריסה ב-Render](https://render.com/deploy?repo=https://github.com/gallif1/Resume_Agent)**

2. התחבר עם חשבון GitHub (פעם אחת).
3. לחץ **Deploy** — Render יבנה ויעלה את האתר (לוקח כ-5–10 דקות בפעם הראשונה).
4. בסיום תקבל כתובת קבועה, למשל:
   `https://resume-agent-xxxx.onrender.com`

שמור את הקישור — זה האתר שלך.

## עדכונים אוטומטיים

אחרי החיבור הראשון, כל מיזוג ל-`master` מעדכן את האתר אוטומטית — **בלי לבקש קישור חדש**.

הפרויקט כולל:

- `render.yaml` עם `autoDeploy: true` ו-`branch: master` — Render מפריס אוטומטית בכל push ל-master (כשהריפו מחובר ב-Render).
- `.github/workflows/render-deploy.yml` — אופציונלי: אם תוסיף ב-GitHub את הסוד `RENDER_DEPLOY_HOOK` (מ-Render → השירות → Settings → Deploy Hook), ה-workflow יפעיל פריסה גם דרך GitHub Actions.

### הגדרת Deploy Hook (אופציונלי)

1. ב-Render: **resume-agent** → **Settings** → **Deploy Hook** → העתק את ה-URL.
2. ב-GitHub: **Settings** → **Secrets and variables** → **Actions** → הוסף `RENDER_DEPLOY_HOOK` עם ה-URL.

## מפתחות API (אופציונלי)

בלוח הבקרה של Render → **Environment** → הוסף לפי הצורך:

| משתנה | תיאור |
|--------|--------|
| `OPENAI_API_KEY` | ניתוח חכם של קו"ח והתאמות |
| `DRUSHIM_EMAIL` | התחברות לדרושים |
| `DRUSHIM_PASSWORD` | סיסמת דרושים |

## הערות

- בתוכנית החינמית השרת «נרדם» אחרי דקות ללא שימוש — הטעינה הראשונה אחרי הפסקה לוקחת ~30 שניות.
- אם אתה עובד עם סוכן Cursor, פשוט בקש ממנו שינויים — אחרי מיזוג ה-PR האתר יתעדכן לבד.
