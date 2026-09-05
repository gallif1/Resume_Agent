# Job Apply Automation (Standalone)

מערכת **נפרדת לגמרי** שמקבלת קישור למשרה + קובץ קו״ח + פרטים אישיים, ממלאת את טופס ההגשה ולוחצת Submit.

אין תלות ב־`ai-job-agent` או ב־`resume-agent-web`. בהמשך אפשר לחבר אותה למוצר הראשי דרך ה־API.

## קלט

| שדה | תיאור |
|------|--------|
| `job_url` | קישור לעמוד המשרה / טופס ההגשה |
| `cv` | קובץ קורות חיים (PDF / DOCX וכו׳) |
| `first_name` | שם פרטי |
| `last_name` | שם משפחה |
| `email` | אימייל |
| `phone` | טלפון |

## התקנה

```bash
cd job-apply-automation
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

הוסף את `src` ל־`PYTHONPATH` (או הרץ מהתיקייה כפי שמופיע למטה).

## הרצה מ־CLI

```bash
cd job-apply-automation
PYTHONPATH=src python -m job_apply \
  --url "https://example.com/jobs/123/apply" \
  --cv ./path/to/cv.pdf \
  --first-name "Israel" \
  --last-name "Israeli" \
  --email "israel@example.com" \
  --phone "0501234567"
```

אפשרויות שימושיות:

```bash
--dry-run     # ממלא בלי ללחוץ Submit
--headed      # מציג חלון דפדפן
--json        # מדפיס תוצאה ב-JSON
```

## API נפרד (פורט 8010)

```bash
cd job-apply-automation
PYTHONPATH=src python -m job_apply.api
```

```bash
curl -X POST http://localhost:8010/apply \
  -F "job_url=https://example.com/jobs/123/apply" \
  -F "first_name=Israel" \
  -F "last_name=Israeli" \
  -F "email=israel@example.com" \
  -F "phone=0501234567" \
  -F "cv=@./cv.pdf" \
  -F "dry_run=false"
```

`GET /health` — בדיקת חיות.

## מה המערכת עושה

1. פותחת את קישור המשרה בדפדפן Chromium (Playwright)
2. אם צריך — לוחצת על כפתור Apply / הגש מועמדות
3. מזהה שדות (שם, אימייל, טלפון, העלאת קו״ח) בעברית ובאנגלית
4. ממלאת את הפרטים ומעלה את קובץ הקו״ח
5. לוחצת Submit (אלא אם `--dry-run`)
6. שומרת צילום מסך תחת `logs/`

פרופיל דפדפן נשמר ב־`data/browser_profile/` (שימושי להתחברות ידנית חד־פעמית לאתרים שדורשים login).

## מגבלות

- טפסים עם CAPTCHA / אימות SMS דורשים השלמה ידנית
- אתרים שדורשים התחברות: הריצו עם `--headed`, התחברו פעם אחת, ואז נסו שוב
- חלק מאתרי ATS מורכבים (Workday וכו׳) עשויים לדרוש התאמות ספציפיות בהמשך

## בדיקות

```bash
cd job-apply-automation
PYTHONPATH=src pytest -q
```

## מבנה

```
job-apply-automation/
  src/job_apply/
    cli.py          # ממשק שורת פקודה
    api.py          # FastAPI נפרד
    engine.py       # זרימת ההגשה
    form_filler.py  # מילוי שדות + Submit
    fields.py       # מיפוי תוויות EN/HE
    browser.py      # Playwright
    models.py       # Applicant / ApplyRequest / ApplyResult
  tests/
  logs/
  data/
```
