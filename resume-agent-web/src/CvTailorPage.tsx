import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronUp, Download, FileText, Loader2 } from "lucide-react";
import AuthView from "./components/AuthView";
import {
  clearAuthSession,
  getCurrentUser,
  getStoredToken,
  saveMvpTailoredCvToJob,
  type AuthUser,
} from "./lib/api";
import {
  downloadTailoredCv,
  fetchJobContext,
  fetchStoredCvFile,
  generateTailoredCv,
  regenerateTailoredCv,
  restoreMvpTailoredSession,
  type CvJobContext,
  type CvTailorGenerateResponse,
  type RequirementGap,
} from "./lib/cvTailorApi";
import { APP_VERSION } from "./lib/version";

const ACCEPTED_TYPES = ".pdf,.docx";
const MAX_CV_FILE_BYTES = 10 * 1024 * 1024;

function readLaunchParams(): {
  cvId: string | null;
  jobId: number | null;
  autoRun: boolean;
  restore: boolean;
  versionId: number | null;
} {
  const params = new URLSearchParams(window.location.search);
  const cvId = params.get("cv_id");
  const jobIdRaw = params.get("job_id");
  const jobId = jobIdRaw ? Number.parseInt(jobIdRaw, 10) : null;
  const versionRaw = params.get("version_id");
  const versionId = versionRaw ? Number.parseInt(versionRaw, 10) : null;
  const restore = params.get("restore") === "1" || versionId != null;
  return {
    cvId,
    jobId: jobId != null && Number.isFinite(jobId) ? jobId : null,
    autoRun: !restore && params.get("auto") === "1",
    restore,
    versionId: versionId != null && Number.isFinite(versionId) ? versionId : null,
  };
}

function validateCvFile(file: File): string | null {
  const ext = file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
  if (![".pdf", ".docx"].includes(ext)) {
    return "יש להעלות קובץ PDF או DOCX בלבד.";
  }
  if (file.size > MAX_CV_FILE_BYTES) {
    return "הקובץ גדול מדי (מקסימום 10 MB).";
  }
  if (file.size === 0) {
    return "הקובץ ריק.";
  }
  return null;
}

type GapFormState = {
  confirmed: boolean;
  details: string;
  showDetails: boolean;
};

function initialGapState(gaps: RequirementGap[]): Record<string, GapFormState> {
  return Object.fromEntries(
    gaps.map((gap) => [
      gap.gap_id,
      { confirmed: false, details: "", showDetails: false },
    ])
  );
}

export default function CvTailorPage() {
  const launchParams = useMemo(() => readLaunchParams(), []);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authChecking, setAuthChecking] = useState(() => Boolean(getStoredToken()));
  const [cvFile, setCvFile] = useState<File | null>(null);
  const [jobDescription, setJobDescription] = useState("");
  const [jobContext, setJobContext] = useState<CvJobContext | null>(null);
  const [prefillLoading, setPrefillLoading] = useState(
    () => Boolean(launchParams.cvId && launchParams.jobId)
  );
  const [loading, setLoading] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CvTailorGenerateResponse | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [gapForm, setGapForm] = useState<Record<string, GapFormState>>({});
  const [generalAdditionalInfo, setGeneralAdditionalInfo] = useState("");
  const autoRunTriggeredRef = useRef(false);
  const restoreTriggeredRef = useRef(false);
  const [savedToJobMessage, setSavedToJobMessage] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(
    () => Boolean(launchParams.restore && launchParams.cvId && launchParams.jobId)
  );

  const tailorJobContext = useMemo(
    () =>
      launchParams.cvId && launchParams.jobId
        ? { cvId: launchParams.cvId, jobId: launchParams.jobId }
        : undefined,
    [launchParams.cvId, launchParams.jobId]
  );

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

  const syncGapForm = useCallback((data: CvTailorGenerateResponse) => {
    setGapForm(initialGapState(data.job_analysis?.gaps ?? []));
    setGeneralAdditionalInfo("");
  }, []);

  const persistToJobHistory = useCallback(
    async (data: CvTailorGenerateResponse) => {
      const { cvId, jobId } = launchParams;
      if (!cvId || !jobId) return false;
      if (data.saved_to_job) {
        setSavedToJobMessage("נשמר בהיסטוריית המשרה — אפשר לחזור לרשימה ולראות את הקובץ.");
        return true;
      }
      const markdown = (data.preview_text || "").trim();
      if (!markdown) return false;
      try {
        await saveMvpTailoredCvToJob(cvId, jobId, markdown);
        setSavedToJobMessage("נשמר בהיסטוריית המשרה — אפשר לחזור לרשימה ולראות את הקובץ.");
        return true;
      } catch (e) {
        setSavedToJobMessage(
          e instanceof Error
            ? `לא הצלחנו לשמור למשרה: ${e.message}`
            : "לא הצלחנו לשמור למשרה"
        );
        return false;
      }
    },
    [launchParams]
  );

  const handleGenerate = useCallback(async () => {
    setError(null);
    setSavedToJobMessage(null);
    if (!cvFile) {
      setError("יש להעלות קובץ קורות חיים (PDF או DOCX).");
      return;
    }
    const fileError = validateCvFile(cvFile);
    if (fileError) {
      setError(fileError);
      return;
    }
    if (jobDescription.trim().length < 20) {
      setError("יש להדביק תיאור משרה (לפחות 20 תווים).");
      return;
    }

    setLoading(true);
    setResult(null);
    try {
      const data = await generateTailoredCv(
        cvFile,
        jobDescription.trim(),
        tailorJobContext
      );
      setResult(data);
      syncGapForm(data);
      await persistToJobHistory(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "יצירת קורות החיים נכשלה");
    } finally {
      setLoading(false);
    }
  }, [cvFile, jobDescription, syncGapForm, persistToJobHistory, tailorJobContext]);

  useEffect(() => {
    const { cvId, jobId } = launchParams;
    if (!authUser || !cvId || !jobId) {
      if (!cvId || !jobId) setPrefillLoading(false);
      return;
    }

    let cancelled = false;
    setPrefillLoading(true);
    setError(null);

    Promise.all([fetchStoredCvFile(cvId), fetchJobContext(cvId, jobId)])
      .then(([file, context]) => {
        if (cancelled) return;
        setCvFile(file);
        setJobContext(context);
        setJobDescription(context.description);
        const fileError = validateCvFile(file);
        if (fileError) setError(fileError);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "טעינת נתוני המשרה נכשלה");
      })
      .finally(() => {
        if (!cancelled) setPrefillLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [authUser, launchParams]);

  useEffect(() => {
    if (
      !launchParams.restore ||
      restoreTriggeredRef.current ||
      !authUser ||
      !launchParams.cvId ||
      !launchParams.jobId ||
      prefillLoading
    ) {
      if (!launchParams.restore) setRestoring(false);
      return;
    }

    restoreTriggeredRef.current = true;
    let cancelled = false;
    setRestoring(true);
    setError(null);
    setSavedToJobMessage(null);

    restoreMvpTailoredSession(
      launchParams.cvId,
      launchParams.jobId,
      launchParams.versionId ?? undefined
    )
      .then((data) => {
        if (cancelled) return;
        setResult(data);
        syncGapForm(data);
        setSavedToJobMessage("נטענה הגרסה השמורה — אפשר להמשיך לערוך ולעדכן.");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(
          e instanceof Error ? e.message : "שחזור עריכת קורות החיים נכשל"
        );
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });

    return () => {
      cancelled = true;
    };
  }, [authUser, launchParams, prefillLoading, syncGapForm]);

  useEffect(() => {
    if (
      !launchParams.autoRun ||
      autoRunTriggeredRef.current ||
      prefillLoading ||
      restoring ||
      loading ||
      result ||
      !cvFile ||
      jobDescription.trim().length < 20
    ) {
      return;
    }
    const fileError = validateCvFile(cvFile);
    if (fileError) return;

    autoRunTriggeredRef.current = true;
    void handleGenerate();
  }, [
    launchParams.autoRun,
    prefillLoading,
    restoring,
    loading,
    result,
    cvFile,
    jobDescription,
    handleGenerate,
  ]);

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
      setError(e instanceof Error ? e.message : "הורדת הקובץ נכשלה");
    } finally {
      setDownloading(false);
    }
  }, [result]);

  const hasGapInput = useMemo(() => {
    const anyConfirmed = Object.values(gapForm).some((item) => item.confirmed);
    const anyDetails = Object.values(gapForm).some((item) => item.details.trim().length > 0);
    return anyConfirmed || anyDetails || generalAdditionalInfo.trim().length > 0;
  }, [gapForm, generalAdditionalInfo]);

  const handleRegenerate = useCallback(async () => {
    if (!result?.result_id || !hasGapInput) return;
    setRegenerating(true);
    setError(null);
    setSavedToJobMessage(null);
    try {
      const gap_confirmations = (result.job_analysis?.gaps ?? []).map((gap) => {
        const state = gapForm[gap.gap_id] ?? {
          confirmed: false,
          details: "",
          showDetails: false,
        };
        return {
          gap_id: gap.gap_id,
          confirmed: state.confirmed,
          details: state.details.trim(),
        };
      });
      const data = await regenerateTailoredCv(
        result.result_id,
        {
          gap_confirmations,
          general_additional_info: generalAdditionalInfo.trim(),
        },
        tailorJobContext
      );
      setResult(data);
      syncGapForm(data);
      await persistToJobHistory(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "עדכון קורות החיים נכשל");
    } finally {
      setRegenerating(false);
    }
  }, [
    result,
    gapForm,
    generalAdditionalInfo,
    hasGapInput,
    syncGapForm,
    persistToJobHistory,
    tailorJobContext,
  ]);

  const updateGapForm = useCallback(
    (gapId: string, patch: Partial<GapFormState>) => {
      setGapForm((prev) => ({
        ...prev,
        [gapId]: { ...prev[gapId], ...patch },
      }));
    },
    []
  );

  if (authChecking || prefillLoading || restoring) {
    return (
      <div className="app">
        <main className="main cv-tailor-main">
          <p className="muted">
            {restoring
              ? "טוען את עריכת קורות החיים השמורה…"
              : prefillLoading
                ? "טוען קורות חיים ופרטי משרה…"
                : "Loading…"}
          </p>
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

  const gaps = result?.job_analysis?.gaps ?? [];
  const strongMatches = result?.job_analysis?.strong_matches ?? [];
  const resolved = result?.job_analysis?.resolved_requirements ?? [];

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
          {jobContext?.title && (
            <p className="cv-tailor-subtitle">
              {jobContext.title}
              {jobContext.company ? ` · ${jobContext.company}` : ""}
            </p>
          )}
          <p className="cv-tailor-subtitle">
            Upload your CV and paste a job description. The AI will rewrite your CV using only
            information from the original — no fabricated experience.
          </p>
          <p className="cv-tailor-section-note">
            מומלץ DOCX מ-Word. PDF סרוק עלול להיכשל. העיבוד לוקח 1–2 דקות — אל תסגור את הדף.
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
              const file = e.target.files?.[0] ?? null;
              setCvFile(file);
              if (file) {
                const fileError = validateCvFile(file);
                setError(fileError);
              } else {
                setError(null);
              }
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
                יוצר קורות חיים מותאמים… (1–2 דקות)
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
          {savedToJobMessage && (
            <div className="alert alert-success" role="status">
              {savedToJobMessage}
            </div>
          )}
        </section>

        {result && (
          <>
            {tailorJobContext && (
              <p className="cv-meta cv-tailor-back-hint">
                לצפייה בהיסטוריה: חזרו לרשימת המשרות ופתחו את כרטיס המשרה.
              </p>
            )}
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
            </section>

            {(strongMatches.length > 0 || resolved.length > 0) && (
              <section className="card cv-tailor-matches">
                <h2>Strong Matches</h2>
                <p className="cv-tailor-section-note">
                  Requirements the system found relevant and supported by your CV or confirmed
                  information.
                </p>
                <ul className="cv-tailor-match-list">
                  {strongMatches.map((match) => (
                    <li key={`match-${match}`}>
                      <span className="cv-tailor-status-icon supported" aria-hidden="true">
                        ✓
                      </span>
                      {match}
                    </li>
                  ))}
                  {resolved.map((item) => (
                    <li key={`resolved-${item.requirement}`}>
                      <span
                        className={`cv-tailor-status-icon ${item.status === "USER_CONFIRMED" ? "confirmed" : "supported"}`}
                        aria-hidden="true"
                      >
                        ✓
                      </span>
                      {item.title || item.requirement}
                      {item.status === "USER_CONFIRMED" ? " — supported by your confirmation" : ""}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {gaps.length > 0 && (
              <section className="card cv-tailor-gaps-panel">
                <h2>Critical Missing Information</h2>
                <p className="cv-tailor-section-note">
                  These job requirements are not fully supported by your source CV. Confirm only
                  what is true, or add details so the system can update your tailored CV
                  conservatively.
                </p>

                <div className="cv-tailor-gap-list">
                  {gaps.map((gap) => {
                    const state = gapForm[gap.gap_id] ?? {
                      confirmed: false,
                      details: "",
                      showDetails: false,
                    };
                    const detailsId = `gap-details-${gap.gap_id}`;
                    const checkboxId = `gap-confirm-${gap.gap_id}`;
                    return (
                      <article key={gap.gap_id} className="cv-tailor-gap-card">
                        <h3>{gap.title || gap.requirement}</h3>
                        {gap.job_requirement_text && (
                          <p className="cv-tailor-gap-job-req">{gap.job_requirement_text}</p>
                        )}
                        {gap.cv_evidence && (
                          <p className="cv-tailor-gap-cv-evidence">
                            <strong>Your CV:</strong> {gap.cv_evidence}
                          </p>
                        )}
                        {!gap.cv_evidence && gap.explanation && (
                          <p className="cv-tailor-gap-cv-evidence">
                            <strong>Your CV:</strong> {gap.explanation}
                          </p>
                        )}

                        {gap.confirmation_text && (
                          <label className="cv-tailor-gap-checkbox" htmlFor={checkboxId}>
                            <input
                              id={checkboxId}
                              type="checkbox"
                              checked={state.confirmed}
                              onChange={(e) =>
                                updateGapForm(gap.gap_id, { confirmed: e.target.checked })
                              }
                            />
                            <span>{gap.confirmation_text}</span>
                          </label>
                        )}

                        <button
                          type="button"
                          className="btn btn-ghost btn-sm cv-tailor-add-details-btn"
                          onClick={() =>
                            updateGapForm(gap.gap_id, { showDetails: !state.showDetails })
                          }
                        >
                          {state.showDetails ? (
                            <>
                              <ChevronUp size={14} aria-hidden="true" />
                              Hide details
                            </>
                          ) : (
                            <>
                              <ChevronDown size={14} aria-hidden="true" />
                              Add details
                            </>
                          )}
                        </button>

                        {state.showDetails && (
                          <div className="cv-tailor-gap-details">
                            <label className="field-label" htmlFor={detailsId}>
                              Tell us about your {gap.title || gap.requirement} experience:
                            </label>
                            <textarea
                              id={detailsId}
                              className="cv-tailor-textarea cv-tailor-gap-textarea"
                              rows={4}
                              value={state.details}
                              onChange={(e) =>
                                updateGapForm(gap.gap_id, { details: e.target.value })
                              }
                            />
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>

                <div className="cv-tailor-general-additional">
                  <label className="field-label" htmlFor="general-additional">
                    Anything else missing from your CV?
                  </label>
                  <textarea
                    id="general-additional"
                    className="cv-tailor-textarea cv-tailor-gap-textarea"
                    rows={4}
                    placeholder="Add any experience, skills, projects or tools that are relevant to this job but are not included in your original CV."
                    value={generalAdditionalInfo}
                    onChange={(e) => setGeneralAdditionalInfo(e.target.value)}
                  />
                </div>

                <button
                  type="button"
                  className="btn btn-primary cv-tailor-regenerate-btn"
                  disabled={regenerating || !hasGapInput}
                  onClick={() => void handleRegenerate()}
                >
                  {regenerating ? (
                    <>
                      <Loader2 size={18} className="spin" aria-hidden="true" />
                      Regenerating tailored CV…
                    </>
                  ) : (
                    "Add Information & Regenerate CV"
                  )}
                </button>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
