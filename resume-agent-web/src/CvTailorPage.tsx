import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Download, FileText, Loader2 } from "lucide-react";
import AuthView from "./components/AuthView";
import {
  clearAuthSession,
  getCurrentUser,
  getStoredToken,
  type AuthUser,
} from "./lib/api";
import {
  downloadTailoredCv,
  generateTailoredCv,
  type CvTailorGenerateResponse,
} from "./lib/cvTailorApi";
import { APP_VERSION } from "./lib/version";

const ACCEPTED_TYPES = ".pdf,.docx";

export default function CvTailorPage() {
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authChecking, setAuthChecking] = useState(() => Boolean(getStoredToken()));
  const [cvFile, setCvFile] = useState<File | null>(null);
  const [jobDescription, setJobDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CvTailorGenerateResponse | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      setAuthChecking(false);
      return;
    }
    let cancelled = false;
    setAuthChecking(true);
    getCurrentUser()
      .then((data) => {
        if (!cancelled) setAuthUser(data.user);
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

  const handleGenerate = useCallback(async () => {
    setError(null);
    if (!cvFile) {
      setError("Please upload a CV file (PDF or DOCX).");
      return;
    }
    if (jobDescription.trim().length < 20) {
      setError("Please paste a job description (at least 20 characters).");
      return;
    }

    setLoading(true);
    setResult(null);
    try {
      const data = await generateTailoredCv(cvFile, jobDescription.trim());
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setLoading(false);
    }
  }, [cvFile, jobDescription]);

  const handleDownload = useCallback(async () => {
    if (!result?.result_id) return;
    setDownloading(true);
    setError(null);
    try {
      const blob = await downloadTailoredCv(result.result_id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      const baseName = result.tailored_cv.name?.trim() || "tailored-cv";
      anchor.download = `${baseName.replace(/[^\w\-]+/g, "-")}.pdf`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }, [result]);

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
              <span className="logo-icon" aria-hidden="true">
                <FileText size={20} strokeWidth={2} />
              </span>
              <span className="logo-brand">
                <span className="logo-text">
                  Resume<b>Agent</b>
                </span>
                <span className="app-version" dir="ltr">
                  {APP_VERSION}
                </span>
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
            <span className="logo-icon" aria-hidden="true">
              <FileText size={20} strokeWidth={2} />
            </span>
            <span className="logo-brand">
              <span className="logo-text">
                CV <b>Tailor</b>
              </span>
              <span className="app-version" dir="ltr">
                {APP_VERSION}
              </span>
            </span>
          </div>
          <div className="header-actions">
            <a href="/" className="btn btn-ghost btn-sm">
              <ArrowLeft size={16} aria-hidden="true" />
              Job search
            </a>
          </div>
        </div>
      </header>

      <main className="main cv-tailor-main">
        <section className="card cv-tailor-card">
          <h1 className="cv-tailor-title">Create Tailored CV</h1>
          <p className="cv-tailor-subtitle">
            Upload your CV and paste a job description. The AI will rewrite your CV using only
            information from the original — no fabricated experience.
          </p>

          <label className="field-label" htmlFor="cv-upload">
            CV file (PDF or DOCX)
          </label>
          <input
            id="cv-upload"
            type="file"
            accept={ACCEPTED_TYPES}
            className="cv-tailor-file-input"
            onChange={(e) => {
              setCvFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />
          {cvFile && <p className="cv-tailor-file-name">{cvFile.name}</p>}

          <label className="field-label" htmlFor="job-description">
            Job description
          </label>
          <textarea
            id="job-description"
            className="cv-tailor-textarea"
            rows={12}
            placeholder="Paste the full job description here…"
            value={jobDescription}
            onChange={(e) => {
              setJobDescription(e.target.value);
              setError(null);
            }}
          />

          <button
            type="button"
            className="btn btn-primary"
            disabled={loading}
            onClick={() => void handleGenerate()}
          >
            {loading ? (
              <>
                <Loader2 size={18} className="spin" aria-hidden="true" />
                Generating tailored CV…
              </>
            ) : (
              "Generate Tailored CV"
            )}
          </button>

          {error && (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          )}
        </section>

        {result && (
          <section className="card cv-tailor-result">
            <div className="cv-tailor-result-header">
              <h2>Tailored CV preview</h2>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={downloading}
                onClick={() => void handleDownload()}
              >
                {downloading ? (
                  <>
                    <Loader2 size={16} className="spin" aria-hidden="true" />
                    Preparing…
                  </>
                ) : (
                  <>
                    <Download size={16} aria-hidden="true" />
                    Download PDF
                  </>
                )}
              </button>
            </div>
            <p className="cv-tailor-model-note" dir="ltr">
              Model: {result.model}
              {result.job_analysis?.target_job_title
                ? ` · Target role: ${result.job_analysis.target_job_title}`
                : ""}
            </p>
            <pre className="cv-tailor-preview">{result.preview_text}</pre>
            {result.job_analysis?.gaps?.length > 0 && (
              <div className="cv-tailor-gaps">
                <h3>Important gaps</h3>
                <p className="cv-tailor-gaps-note">
                  These job requirements are not fully supported by your source CV and were not
                  added to the tailored document.
                </p>
                <ul>
                  {result.job_analysis.gaps.map((gap) => (
                    <li key={gap.requirement}>
                      <strong>{gap.requirement}</strong>
                      {gap.explanation ? ` — ${gap.explanation}` : ""}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
