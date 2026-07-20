# פריסת האתר — בלי טרמינל

## למה הדיפלוי מוחק נתונים?

האפליקציה שומרת משתמשים, קורות חיים ומשרות ב־**SQLite** תחת `ai-job-agent/data/`.
ב־Render / Zeabur **בלי דיסק קבוע (Volume)** מערכת הקבצים זמנית — כל Deploy יוצר קונטיינר חדש והנתונים נעלמים.

| בדיקה | בלי תיקון | אחרי תיקון |
|--------|-----------|------------|
| `/api/health` → `data_persistent` | `false` | `true` |
| אחרי Redeploy | משתמשים/קו״ח נמחקים | נשארים |

## פריסה ראשונה (פעם אחת)

1. פתח: **[לחץ כאן לפריסה ב-Render](https://render.com/deploy?repo=https://github.com/gallif1/Resume_Agent)**
2. התחבר עם GitHub ולחץ **Deploy**.
3. בסיום תקבל כתובת קבועה, למשל `https://resume-agent-xxxx.onrender.com`.

> **חשוב:** שמירת נתונים דורשת תוכנית **Starter** (או גבוהה יותר) + Persistent Disk.
> התוכנית החינמית ב-Render **לא** שומרת קבצים בין דיפלויים.

### Render — דיסק קבוע (חובה אם כבר פרסת)

אם השירות כבר רץ בלי דיסק:

1. Render → **resume-agent** → **Disks** → **Add disk**
2. **Mount path:** `/app/ai-job-agent/data`
3. **Size:** 1 GB מספיק להתחלה
4. שנה את ה־Plan ל־**Starter** (או גבוה יותר)
5. שמור — Render יפרוס מחדש

ב־`render.yaml` זה כבר מוגדר (`plan: starter` + `disk`). אם Blueprint מסונכרן, העדכון יחול אוטומטית אחרי merge ל־`master`.

אחרי הפריסה ודא:
`https://YOUR-APP.onrender.com/api/health` → `"data_persistent": true`

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
| `JWT_SECRET` | מפתח לחתימת JWT (מומלץ חזק בפרודקשן) |
| `DRUSHIM_EMAIL` | התחברות לדרושים |
| `DRUSHIM_PASSWORD` | סיסמת דרושים |

## שמירת נתונים (Docker מקומי)

קבצי SQLite והעלאות נשמרים תחת `ai-job-agent/data/` (כולל `registry.db` עם משתמשי ההתחברות).
כשמריצים עם Docker, **חובה** לעשות mount לתיקייה הזו כ־volume קבוע, אחרת כל הנתונים יימחקו ב־restart:

```bash
docker run -v ./data:/app/ai-job-agent/data -p 8000:8000 resume-agent
```

ב-Compose:

```yaml
volumes:
  - ./data:/app/ai-job-agent/data
```

## הערות

- בתוכנית החינמית השרת «נרדם» אחרי דקות ללא שימוש — הטעינה הראשונה אחרי הפסקה לוקחת ~30 שניות. **בנוסף**, בלי דיסק קבוע הנתונים לא נשמרים.
- אם הפריסה נכשלת ב-GitHub Actions, בדוק ש-`RENDER_DEPLOY_HOOK` הוגדר נכון.
- אפשר לעקוב אחרי הפריסה ב-GitHub → **Actions** וב-Render → **Events**.
- איסוף משרות מדרושים משתמש ב-Playwright Chromium. ה-Dockerfile מתקין אותו אוטומטית; אם מופיעה שגיאה `Executable doesn't exist`, פרוס מחדש מ-`master` אחרי עדכון Docker.

## Zeabur — פריסה מחדש + Volume (חובה)

הקוד ב-GitHub **לא** מתעדכן לבד ב-Zeabur. אחרי כל merge ל-`master`:

1. Zeabur Dashboard → הפרויקט → השירות → **Redeploy**
2. ודא שיש אייקון **Docker** בלוג הבנייה (לא Python/Node auto-detect)
3. Root Directory = שורש הריפו (שם נמצא `Dockerfile`)
4. **Volumes** → Mount Volumes:
   - Volume ID: `data` (או כל שם)
   - Mount Directory: `/app/ai-job-agent/data`
5. אחרי הפריסה, בדוק: `https://YOUR-APP.zeabur.app/api/health`
   - חייב להופיע: `"playwright_ready": true` ו־`"data_persistent": true`
   - אם `playwright_ready: false` — הבנייה לא השתמשה ב-Dockerfile
   - אם `data_persistent: false` — ה־Volume לא מחובר לנתיב הנכון

אם Zeabur לא משתמש ב-Dockerfile, הוסף משתנה סביבה:
`ZBPACK_DOCKERFILE_PATH=Dockerfile`

> אחרי חיבור Volume בפעם הראשונה התיקייה מתרוקנת — צריך להירשם/להעלות קו״ח מחדש. מכאן והלאה Redeploy **לא** ימחק נתונים.

## איך לדעת שהשינויים עלו?

| מה לבדוק | לפני התיקון | אחרי התיקון |
|-----------|-------------|-------------|
| `/api/health` | אין `playwright_ready` / `data_persistent: false` | `"playwright_ready": true`, `"data_persistent": true` |
| אחרי סריקה | אין הודעות אזהרה | תיבה צהובה «בעיות באיסוף משרות» |
| שגיאת דרושים | `Executable doesn't exist` | משרות נאספות / הודעה ברורה בעברית |
| אחרי Redeploy | משתמשים וקו״ח נמחקים | נשארים |
