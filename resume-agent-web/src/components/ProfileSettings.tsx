import { useCallback, useEffect, useState } from "react";
import {
  getSiteCredentials,
  saveSiteCredentials,
  type SiteCredentialPublic,
} from "../lib/api";

interface Props {
  cvId: string;
}

interface SiteFormState {
  email: string;
  password: string;
  passwordSet: boolean;
  configured: boolean;
}

function emptySiteState(): SiteFormState {
  return { email: "", password: "", passwordSet: false, configured: false };
}

function toFormState(site: SiteCredentialPublic): SiteFormState {
  return {
    email: site.email,
    password: "",
    passwordSet: site.password_set,
    configured: site.configured,
  };
}

function passwordPlaceholder(site: SiteFormState): string {
  if (site.password) return "";
  if (site.passwordSet) return "••••••••";
  return "";
}

export default function ProfileSettings({ cvId }: Props) {
  const [linkedin, setLinkedin] = useState<SiteFormState>(emptySiteState);
  const [drushim, setDrushim] = useState<SiteFormState>(emptySiteState);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getSiteCredentials(cvId);
      setLinkedin(toFormState(data.credentials.linkedin));
      setDrushim(toFormState(data.credentials.drushim));
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינת ההגדרות");
    } finally {
      setLoading(false);
    }
  }, [cvId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const result = await saveSiteCredentials(cvId, {
        linkedin: {
          email: linkedin.email.trim(),
          password: linkedin.password || undefined,
        },
        drushim: {
          email: drushim.email.trim(),
          password: drushim.password || undefined,
        },
      });
      setLinkedin(toFormState(result.credentials.linkedin));
      setDrushim(toFormState(result.credentials.drushim));
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בשמירת ההגדרות");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="profile-settings loading">טוען הגדרות…</div>;
  }

  return (
    <div className="profile-settings">
      <div className="profile-settings-header">
        <h2>הגדרות פרופיל</h2>
        <p className="profile-settings-intro">
          הזן את פרטי ההתחברות שלך לאתרי דרושים ולינקדאין. המערכת תשתמש בהם
          אוטומטית כשתלחץ על <b>הגש קורות חיים</b> — בלי צורך בהתחברות ידנית.
        </p>
      </div>

      {error && <div className="error-box">{error}</div>}
      {saved && (
        <div className="success-box" role="status">
          ההגדרות נשמרו. אפשר להגיש מועמדות בלחיצת כפתור.
        </div>
      )}

      <div className="profile-settings-grid">
        <section className="profile-site-card">
          <div className="profile-site-card-head">
            <h3>לינקדאין</h3>
            <span
              className={`profile-site-status ${
                linkedin.configured ? "configured" : "missing"
              }`}
            >
              {linkedin.configured ? "מוגדר" : "נדרש להגדרה"}
            </span>
          </div>
          <p className="profile-site-hint">
            נדרש למשרות Easy Apply ולפתיחת קישורי הגשה חיצוניים בלינקדאין.
          </p>
          <label className="profile-field">
            <span>אימייל</span>
            <input
              type="email"
              autoComplete="username"
              dir="ltr"
              value={linkedin.email}
              onChange={(e) =>
                setLinkedin((prev) => ({ ...prev, email: e.target.value }))
              }
              placeholder="you@example.com"
            />
          </label>
          <label className="profile-field">
            <span>סיסמה</span>
            <input
              type="password"
              autoComplete="current-password"
              dir="ltr"
              value={linkedin.password}
              placeholder={passwordPlaceholder(linkedin)}
              onChange={(e) =>
                setLinkedin((prev) => ({ ...prev, password: e.target.value }))
              }
            />
          </label>
        </section>

        <section className="profile-site-card">
          <div className="profile-site-card-head">
            <h3>דרושים</h3>
            <span
              className={`profile-site-status ${
                drushim.configured ? "configured" : "missing"
              }`}
            >
              {drushim.configured ? "מוגדר" : "נדרש להגדרה"}
            </span>
          </div>
          <p className="profile-site-hint">
            נדרש להגשה בלחיצה אחת במשרות מ-drushim.co.il.
          </p>
          <label className="profile-field">
            <span>אימייל / טלפון</span>
            <input
              type="text"
              autoComplete="username"
              dir="ltr"
              value={drushim.email}
              onChange={(e) =>
                setDrushim((prev) => ({ ...prev, email: e.target.value }))
              }
              placeholder="you@example.com"
            />
          </label>
          <label className="profile-field">
            <span>סיסמה</span>
            <input
              type="password"
              autoComplete="current-password"
              dir="ltr"
              value={drushim.password}
              placeholder={passwordPlaceholder(drushim)}
              onChange={(e) =>
                setDrushim((prev) => ({ ...prev, password: e.target.value }))
              }
            />
          </label>
        </section>
      </div>

      <p className="profile-settings-note">
        הסיסמאות נשמרות בשרת באופן פרטי לקובץ קורות החיים שלך ולא מוצגות שוב
        לאחר השמירה. השאר שדה סיסמה ריק כדי לשמור את הסיסמה הקיימת.
      </p>

      <div className="profile-settings-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={saving}
          onClick={handleSave}
        >
          {saving ? "שומר…" : "שמור הגדרות"}
        </button>
      </div>
    </div>
  );
}
