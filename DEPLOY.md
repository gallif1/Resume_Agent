# פריסת האתר — בלי טרמינל

## פריסה ראשונה (פעם אחת)

1. פתח: **[לחץ כאן לפריסה ב-Render](https://render.com/deploy?repo=https://github.com/gallif1/Resume_Agent)**
2. התחבר עם GitHub ולחץ **Deploy**.
3. בסיום תקבל כתובת קבועה, למשל `https://resume-agent-xxxx.onrender.com`.

## הגדרה חד-פעמית לפריסה אוטומטית

כדי שאחרי **כל מיזוג PR ל-`master`** האתר יתעדכן לבד — בצע פעם אחת את שני השלבים:

### 1. Render — הפעל Auto-Deploy

1. Render → **resume-agent** → **Settings**
2. תחת **Build & Deploy**:
   - **Auto-Deploy**: `On`
   - **Branch**: `master`
3. שמור.

### 2. GitHub — הוסף Deploy Hook

1. Render → **resume-agent** → **Settings** → **Deploy Hook** → העתק את ה-URL.
2. GitHub → **Resume_Agent** → **Settings** → **Secrets and variables** → **Actions**
3. **New repository secret**:
   - Name: `RENDER_DEPLOY_HOOK`
   - Value: ה-URL שהעתקת מ-Render

זהו. מעכשיו הזרימה אוטומטית:

```
PR נפתח → בדיקות (CI)
PR ממוזג ל-master → בדיקות → פריסה אוטומטית ל-Render
```

אין צורך ב-Manual Deploy.

## מפתחות API (אופציונלי)

ב-Render → **Environment**:

| משתנה | תיאור |
|--------|--------|
| `OPENAI_API_KEY` | ניתוח חכם של קו"ח והתאמות |
| `DRUSHIM_EMAIL` | התחברות לדרושים |
| `DRUSHIM_PASSWORD` | סיסמת דרושים |

## הערות

- בתוכנית החינמית השרת «נרדם» אחרי דקות ללא שימוש — הטעינה הראשונה אחרי הפסקה לוקחת ~30 שניות.
- אם הפריסה נכשלת ב-GitHub Actions, בדוק ש-`RENDER_DEPLOY_HOOK` הוגדר נכון.
- אפשר לעקוב אחרי הפריסה ב-GitHub → **Actions** וב-Render → **Events**.
- איסוף משרות מדרושים משתמש ב-Playwright Chromium. ה-Dockerfile מתקין אותו אוטומטית; אם מופיעה שגיאה `Executable doesn't exist`, פרוס מחדש מ-`master` אחרי עדכון Docker.

## Zeabur — פריסה מחדש (חובה אחרי merge)

הקוד ב-GitHub **לא** מתעדכן לבד ב-Zeabur. אחרי כל merge ל-`master`:

1. Zeabur Dashboard → הפרויקט → השירות → **Redeploy**
2. ודא שיש אייקון **Docker** בלוג הבנייה (לא Python/Node auto-detect)
3. Root Directory = שורש הריפו (שם נמצא `Dockerfile`)
4. אחרי הפריסה, בדוק: `https://YOUR-APP.zeabur.app/api/health`
   - חייב להופיע: `"playwright_ready": true`
   - אם `playwright_ready: false` — הבנייה לא השתמשה ב-Dockerfile

אם Zeabur לא משתמש ב-Dockerfile, הוסף משתנה סביבה:
`ZBPACK_DOCKERFILE_PATH=Dockerfile`

## איך לדעת שהשינויים עלו?

| מה לבדוק | לפני התיקון | אחרי התיקון |
|-----------|-------------|-------------|
| `/api/health` | אין `playwright_ready` | `"playwright_ready": true` |
| אחרי סריקה | אין הודעות אזהרה | תיבה צהובה «בעיות באיסוף משרות» |
| שגיאת דרושים | `Executable doesn't exist` | משרות נאספות / הודעה ברורה בעברית |
