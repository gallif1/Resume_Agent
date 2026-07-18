import { useState, type FormEvent } from "react";
import { FileText } from "lucide-react";
import {
  loginUser,
  registerUser,
  setStoredToken,
  type AuthUser,
} from "../lib/api";

interface Props {
  onAuthenticated: (user: AuthUser) => void;
}

type Mode = "login" | "register";

export default function AuthView({ onAuthenticated }: Props) {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result =
        mode === "login"
          ? await loginUser(email.trim(), password)
          : await registerUser(email.trim(), password);
      setStoredToken(result.access_token);
      onAuthenticated(result.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "ההתחברות נכשלה");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="auth-view" aria-label="התחברות">
      <div className="auth-panel">
        <div className="auth-brand">
          <span className="logo-icon" aria-hidden="true">
            <FileText size={26} strokeWidth={2} />
          </span>
          <h1 className="auth-title">
            Resume<b>Agent</b>
          </h1>
          <p className="auth-subtitle">
            {mode === "login"
              ? "התחבר כדי לצפות בקורות החיים והמשרות שלך"
              : "צור חשבון חדש — הנתונים שלך נשמרים בנפרד"}
          </p>
        </div>

        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={`auth-tab ${mode === "login" ? "active" : ""}`}
            onClick={() => {
              setMode("login");
              setError(null);
            }}
          >
            התחברות
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "register"}
            className={`auth-tab ${mode === "register" ? "active" : ""}`}
            onClick={() => {
              setMode("register");
              setError(null);
            }}
          >
            הרשמה
          </button>
        </div>

        <form className="auth-form" onSubmit={submit} dir="rtl">
          <label className="auth-label">
            אימייל
            <input
              className="auth-input"
              type="email"
              name="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@example.com"
              dir="ltr"
            />
          </label>
          <label className="auth-label">
            סיסמה
            <input
              className="auth-input"
              type="password"
              name="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="לפחות 6 תווים"
              dir="ltr"
            />
          </label>

          {error && (
            <div className="error-box auth-error" role="alert">
              {error}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary auth-submit"
            disabled={busy}
          >
            {busy
              ? "רגע…"
              : mode === "login"
                ? "התחבר"
                : "צור חשבון"}
          </button>
        </form>
      </div>
    </section>
  );
}
