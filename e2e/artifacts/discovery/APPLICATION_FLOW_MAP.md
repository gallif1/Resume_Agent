# Application Flow Map

Generated: 2026-07-19T08:59:13.044Z

## Stack
- Frontend: React + TypeScript + Vite SPA (no client-side router)
- Backend: FastAPI on same origin (`/api`, `/cvs`, `/jobs`)
- Auth: JWT in `localStorage` key `resume_agent_jwt`
- UI language: Hebrew RTL (`html[dir=rtl][lang=he]`)

## Accessible routes
- / (SPA root — AuthView when logged out)
- /jobs → SPA fallback (hasUi=false)
- /cvs → SPA fallback (hasUi=false)
- /dashboard → SPA fallback (hasUi=true)
- /settings → SPA fallback (hasUi=true)
- / (CvManager when logged in)
- in-app view: CvDetails (showMatches state, no URL change)

## Auth
```json
{
  "requiresAuth": true,
  "modes": [
    "login",
    "register"
  ],
  "fields": [
    "email",
    "password"
  ],
  "passwordMinLength": 6,
  "passwordConfirmField": false,
  "jwtStorageKey": "resume_agent_jwt",
  "htmlDir": "rtl",
  "htmlLang": "he"
}
```

## Main user workflows
1. Register / Login
2. Upload one or more resumes
3. Select job sites + launch agent scan (workspace aggregates all CVs)
4. View matches, sort, update status, tailor CV, open apply confirm
5. Logout

## Resume controls
- dropzone file upload (pdf/doc/docx/txt/images)
- cv list with delete + confirm modal
- cv picker (marks primary display; workspace aggregates all CVs)
- אפס תוצאות / אפס קבצים reset modals

## Job scan controls
- site toggles: Drushim / LinkedIn / GotFriends
- שגר סוכן לסריקה
- עצור סריקה (while running)
- PipelineProgress live steps

## Job result controls
- צפה בתוצאות → CvDetails workspace mode
- sort: score / date / site
- expand job card: description, skills, status select
- הגש קורות חיים (confirm modal — do not submit in QA)
- ייצר קורות חיים (AI tailor — expensive, skip in discovery)
- NO client-side search / filter / pagination UI observed in source

## Modals
- delete CV confirm
- reset results / reset files
- apply confirm
- application log
- tailored CV preview

## Buttons observed (sample)
- התחברות
- הרשמה
- התחבר
- התנתק

## Notes
- none
