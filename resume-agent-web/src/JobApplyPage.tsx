import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Loader2, Send } from "lucide-react";
import AuthView from "./components/AuthView";
import {
  clearAuthSession,
  getCurrentUser,
  getStoredToken,
  type AuthUser,
} from "./lib/api";
import { submitJobApplication, type JobApplyResult } from "./lib/jobApplyApi";
import { APP_VERSION } from "./lib/version";

const ACCEPTED_TYPES = ".pdf,.doc,.docx";

export default function JobApplyPage() {
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authChecking, setAuthChecking] = useState(() => Boolean(getStoredToken()));

  const [jobUrl, setJobUrl] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [cvFile, setCvFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState(false);
  const [showBrowser, setShowBrowser] = useState(true);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<JobApplyResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!getStoredToken()) {
      setAuthChecking(false);
      return;
    }
    getCurrentUser()
      .then((data) => {
        if (cancelled) return;
        setAuthUser(data.user);
        if (data.user.email) {
          setEmail((prev) => prev || data.user.email || "");
        }
        if (data.user.display_name) {
          const parts = data.user.display_name.trim().split(/\s+/);
          setFirstName((prev) => prev || parts[0] || "");
          if (parts.length > 1) {
            setLastName((prev) => prev || parts.slice(1).join(" "));
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          clearAuthSession();
          setAuthUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) setAuthChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = useCallback(async () => {
    setError(null);
    setResult(null);

    if (!jobUrl.trim().startsWith("http")) {
      setError("יש להזין קישור משרה תקין (http/https).");
      return;
    }
    if (!cvFile) {
      setError("יש להעלות קובץ קורות חיים.");
      return;
    }
    if (!firstName.trim() || !lastName.trim() || !email.trim() || !phone.trim()) {
      setError("יש למלא שם פרטי, שם משפחה, אימייל וטלפון.");
      return;
    }

    setLoading(true);
    try {
      const data = await submitJobApplication({
        jobUrl,
        firstName,
        lastName,
        email,
        phone,
        cv: cvFile,
        dryRun,
        showBrowser,
      });
      setResult(data);
      if (!data.success) {
        setError(data.message || "ההגשה נכשלה");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "שגיאה לא צפויה");
    } finally {
      setLoading(false);
    }
  }, [jobUrl, cvFile, firstName, lastName, email, phone, dryRun, showBrowser]);

  if (authChecking) {
    return (
      <div className="app">
        <main className="main cv-tailor-main">
          <p className="muted">Loading…</p>
        </main>
      </div>
    );
  }

  if (!authUser) {
    return (
      <div className="app">
        <header className="header">
          <div className="header-inner">
            <div className="logo">
              <span className="logo-text">
                Resume<b>Agent</b>
              </span>
              <span className="app-version" dir="ltr">
                {APP_VERSION}
              </span>
            </div>
            <a href="/" className="btn btn-ghost btn-sm">
              Back to job search
            </a>
          </div>
        </header>
        <main className="main">
          <AuthView onAuthenticated={setAuthUser} />
        </main>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <span className="logo-text">
              Auto <b>Apply</b>
            </span>
            <span className="app-version" dir="ltr">
              {APP_VERSION}
            </span>
          </div>
          <div className="header-actions">
            <a href="/cv-tailor" className="btn btn-secondary btn-sm">
              Create Tailored CV
            </a>
            <a href="/" className="btn btn-ghost btn-sm">
              <ArrowLeft size={16} aria-hidden="true" />
              Job search
            </a>
          </div>
        </div>
      </header>

      <main className="main cv-tailor-main job-apply-main">
        <section className="card cv-tailor-card">
          <h1 className="cv-tailor-title">הגשה אוטומטית למשרה</h1>
          <p className="cv-tailor-subtitle">
            הזן קישור למשרה, העלה קורות חיים ומלא פרטי קשר — המערכת תמלא את הטופס
            ותלחץ Submit.
          </p>

          <label className="field-label" htmlFor="job-url">
            קישור למשרה
          </label>
          <input
            id="job-url"
            type="url"
            className="job-apply-input"
            dir="ltr"
            placeholder="https://…"
            value={jobUrl}
            onChange={(e) => {
              setJobUrl(e.target.value);
              setError(null);
            }}
          />

          <label className="field-label" htmlFor="cv-upload-apply">
            קובץ קורות חיים
          </label>
          <input
            id="cv-upload-apply"
            type="file"
            accept={ACCEPTED_TYPES}
            className="cv-tailor-file-input"
            onChange={(e) => {
              setCvFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />
          {cvFile && <p className="cv-tailor-file-name">{cvFile.name}</p>}

          <div className="job-apply-grid">
            <div>
              <label className="field-label" htmlFor="first-name">
                שם פרטי
              </label>
              <input
                id="first-name"
                className="job-apply-input"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="last-name">
                שם משפחה
              </label>
              <input
                id="last-name"
                className="job-apply-input"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="apply-email">
                אימייל
              </label>
              <input
                id="apply-email"
                type="email"
                className="job-apply-input"
                dir="ltr"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="apply-phone">
                טלפון
              </label>
              <input
                id="apply-phone"
                type="tel"
                className="job-apply-input"
                dir="ltr"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
          </div>

          <label className="job-apply-dry-run" htmlFor="show-browser">
            <input
              id="show-browser"
              type="checkbox"
              checked={showBrowser}
              onChange={(e) => setShowBrowser(e.target.checked)}
            />
            הצג דפדפן בלייב (Playwright) — חלון Chromium נפתח על המחשב שמריץ את השרת
          </label>

          <label className="job-apply-dry-run" htmlFor="dry-run">
            <input
              id="dry-run"
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
            />
            מצב ניסיון — מלא את הטופס בלי ללחוץ Submit
          </label>

          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}

          <button
            type="button"
            className="btn btn-primary job-apply-submit"
            disabled={loading}
            onClick={() => void handleSubmit()}
          >
            {loading ? (
              <>
                <Loader2 size={18} className="spin" aria-hidden="true" />
                ממלא ומגיש…
              </>
            ) : (
              <>
                <Send size={18} aria-hidden="true" />
                {dryRun ? "מלא טופס (ללא שליחה)" : "מלא ושלח מועמדות"}
              </>
            )}
          </button>
        </section>

        {result && (
          <section className="card cv-tailor-result" dir="rtl">
            <h2>תוצאה</h2>
            <p>
              <strong>סטטוס:</strong> {result.status}
              {result.success ? " ✓" : ""}
            </p>
            <p>{result.message}</p>
            {(result.filled_fields?.length ?? 0) > 0 && (
              <p className="muted">
                שדות שמולאו: {result.filled_fields!.join(", ")}
              </p>
            )}
            {result.confirmation_text && (
              <p className="muted">אישור: {result.confirmation_text}</p>
            )}
            {result.final_url && (
              <p className="muted" dir="ltr">
                URL: {result.final_url}
              </p>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
