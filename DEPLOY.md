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
