import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyToJob,
  DuplicateApplicationError,
  getCvMatches,
  getCvScanStatus,
  getJobApplication,
  parseScanSummary,
  updateMatchStatus,
  type ApplicationStatus,
  type Cv,
  type CvMatch,
  type CvScanStatus,
  type JobApplication,
  type JobApplicationStatus,
} from "../lib/api";
import PipelineProgress from "./PipelineProgress";
import ProfileSettings from "./ProfileSettings";

interface Props {
  cvId: string;
  cv: Cv | undefined;
  scanCvId: string | null;
  scanStatus: CvScanStatus | null;
  onBack: () => void;
  onRun: (id: string) => void;
}

const STATUS_OPTIONS: { value: ApplicationStatus; label: string }[] = [
  { value: "not_sent", label: "לא נשלחו קו\"ח" },
  { value: "sent", label: "נשלחו קו\"ח" },
  { value: "interested", label: "מעניין" },
  { value: "not_relevant", label: "לא רלוונטי" },
  { value: "applied_manually", label: "הוגש ידנית" },
];

const STATUS_LABEL: Record<ApplicationStatus, string> = Object.fromEntries(
  STATUS_OPTIONS.map((o) => [o.value, o.label])
) as Record<ApplicationStatus, string>;

const JOB_APP_STATUS_LABEL: Record<JobApplicationStatus, string> = {
  pending: "ממתין להגשה",
  in_progress: "מגיש…",
  submitted: "קורות החיים נשלחו",
  failed: "ההגשה נכשלה",
  requires_user_action: "נדרשת השלמה ידנית",
};

interface ConfirmState {
  match: CvMatch;
  force?: boolean;
}

function scoreClass(score: number | null): string {
  if (score == null) return "";
  if (score >= 85) return "score-high";
  if (score >= 70) return "score-mid";
  return "score-low";
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("he-IL", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

function isActiveApplication(status: JobApplicationStatus | undefined): boolean {
  return status === "pending" || status === "in_progress";
}

export default function CvDetails({
  cvId,
  cv,
  scanCvId,
  scanStatus,
  onBack,
  onRun,
}: Props) {
  const [matches, setMatches] = useState<CvMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [applyingId, setApplyingId] = useState<number | null>(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [logApplication, setLogApplication] = useState<JobApplication | null>(null);
  const [activeTab, setActiveTab] = useState<"jobs" | "profile">("jobs");
  const [lastScanInfo, setLastScanInfo] = useState(() =>
    parseScanSummary(null)
  );
  const prevRunning = useRef(false);
  const running = scanCvId === cvId && (scanStatus?.running ?? false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getCvMatches(cvId, { latest: true });
      setMatches(data.matches);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינת ההתאמות");
    } finally {
      setLoading(false);
    }
  }, [cvId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    getCvScanStatus(cvId)
      .then((status) => {
        if (cancelled) return;
        const parsed = parseScanSummary(status.latest_scan?.summary);
        if ((status.warnings?.length ?? 0) > 0) {
          parsed.warnings = status.warnings ?? parsed.warnings;
        }
        if (status.collection) {
          parsed.collection = status.collection;
        }
        setLastScanInfo(parsed);
      })
      .catch(() => {
        /* scan status optional */
      });
    return () => {
      cancelled = true;
    };
  }, [cvId, running, scanStatus?.warnings, scanStatus?.collection]);

  useEffect(() => {
    if (prevRunning.current && !running && scanCvId === cvId) {
      load();
    }
    prevRunning.current = running;
  }, [running, scanCvId, cvId, load]);

  // Poll while any application is in progress.
  useEffect(() => {
    const hasActive = matches.some((m) =>
      isActiveApplication(m.job_application?.status)
    );
    if (!hasActive) return;

    const timer = window.setInterval(() => {
      load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [matches, load]);

  const handleStatusChange = async (
    match: CvMatch,
    status: ApplicationStatus
  ) => {
    setSavingId(match.match_id);
    setMatches((prev) =>
      prev.map((m) =>
        m.match_id === match.match_id ? { ...m, application_status: status } : m
      )
    );
    try {
      await updateMatchStatus(cvId, match.match_id, status);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בעדכון הסטטוס");
      load();
    } finally {
      setSavingId(null);
    }
  };

  const openConfirm = (match: CvMatch, force = false) => {
    setConfirmState({ match, force });
  };

  const handleApply = async (match: CvMatch, force = false) => {
    setApplyingId(match.job_id);
    setError(null);
    try {
      const result = await applyToJob(cvId, match.job_id, { force });
      setMatches((prev) =>
        prev.map((m) =>
          m.job_id === match.job_id
            ? { ...m, job_application: result.application }
            : m
        )
      );
      setConfirmState(null);
    } catch (e) {
      if (e instanceof DuplicateApplicationError) {
        openConfirm(match, true);
        setError(e.message);
      } else {
        setError(e instanceof Error ? e.message : "שגיאה בהגשת קורות החיים");
      }
    } finally {
      setApplyingId(null);
    }
  };

  const openApplicationLog = async (app: JobApplication) => {
    try {
      const full = await getJobApplication(cvId, app.application_id);
      setLogApplication(full);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינת יומן ההגשה");
    }
  };

  const renderApplyButton = (match: CvMatch) => {
    const app = match.job_application;
    const status = app?.status;
    const busy = applyingId === match.job_id || isActiveApplication(status);

    if (status === "submitted") {
      return (
        <div className="apply-status-group">
          <span className="apply-status apply-status-success">
            קורות החיים נשלחו
          </span>
          {app?.submitted_at && (
            <span className="apply-status-date">{formatDate(app.submitted_at)}</span>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => openConfirm(match, true)}
          >
            הגש שוב
          </button>
        </div>
      );
    }

    if (status === "failed") {
      return (
        <div className="apply-status-group">
          <span className="apply-status apply-status-failed">
            ההגשה נכשלה – נסה שוב
          </span>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy}
            onClick={() => openConfirm(match)}
          >
            נסה שוב
          </button>
        </div>
      );
    }

    if (status === "requires_user_action") {
      return (
        <div className="apply-status-group">
          <span className="apply-status apply-status-warning">
            נדרשת השלמה ידנית
          </span>
          {(app?.current_step_url || match.job_url) && (
            <a
              className="btn btn-primary btn-sm"
              href={app?.current_step_url || match.job_url || "#"}
              target="_blank"
              rel="noreferrer"
            >
              המשך ידנית ↗
            </a>
          )}
        </div>
      );
    }

    if (busy) {
      return (
        <button type="button" className="btn btn-primary btn-sm" disabled>
          מגיש…
        </button>
      );
    }

    return (
      <button
        type="button"
        className="btn btn-primary btn-sm"
        onClick={() => openConfirm(match)}
      >
        הגש קורות חיים
      </button>
    );
  };

  const title = cv?.display_name || cv?.file_name || "קורות חיים";
  const liveWarnings =
    scanCvId === cvId ? scanStatus?.warnings ?? [] : [];
  const liveCollection =
    scanCvId === cvId ? scanStatus?.collection ?? null : null;
  const activeScan =
    scanCvId === cvId &&
    (scanStatus?.running ||
      scanStatus?.error ||
      liveWarnings.length > 0 ||
      Boolean(liveCollection))
      ? scanStatus
      : null;
  const displayWarnings =
    liveWarnings.length > 0 ? liveWarnings : lastScanInfo.warnings;
  const displayCollection = liveCollection ?? lastScanInfo.collection;
  const showScanPanel =
    activeScan ??
    (displayWarnings.length > 0 || displayCollection
      ? ({
          running: false,
          started_at: null,
          finished_at: null,
          error: null,
          warnings: displayWarnings,
          collection: displayCollection,
          current_step: null,
          detail: null,
          steps: [],
          log: [],
          latest_scan: null,
        } satisfies CvScanStatus)
      : null);

  return (
    <section>
      <div className="details-topbar">
        <button className="btn btn-ghost" onClick={onBack}>
          ← חזרה
        </button>
        <div className="details-title">
          <h2>{title}</h2>
          {cv?.last_scan_at && (
            <span className="cv-meta">סריקה אחרונה: {formatDate(cv.last_scan_at)}</span>
          )}
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={scanStatus?.running ?? false}
          onClick={() => onRun(cvId)}
        >
          {running ? "רץ עכשיו…" : "▶ הרץ סוכן"}
        </button>
      </div>

      <div className="details-tabs" role="tablist" aria-label="תצוגת קורות חיים">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "jobs"}
          className={`details-tab ${activeTab === "jobs" ? "active" : ""}`}
          onClick={() => setActiveTab("jobs")}
        >
          משרות
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "profile"}
          className={`details-tab ${activeTab === "profile" ? "active" : ""}`}
          onClick={() => setActiveTab("profile")}
        >
          פרופיל
        </button>
      </div>

      {activeTab === "profile" ? (
        <ProfileSettings cvId={cvId} />
      ) : (
        <>
      <PipelineProgress scanStatus={showScanPanel} />

      {error && <div className="error-box">{error}</div>}

      {confirmState && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal-card apply-confirm-modal">
            <h3>אישור הגשת קורות חיים</h3>
            <p>
              המערכת עומדת לפתוח את אתר המשרה החיצוני ולהגיש את פרטיך וקורות החיים
              השמורים.
            </p>
            <div className="apply-confirm-details">
              <p><b>משרה:</b> {confirmState.match.title}</p>
              <p><b>חברה:</b> {confirmState.match.company || "—"}</p>
            </div>
            {confirmState.force && (
              <p className="apply-confirm-warning">
                כבר הוגשו קורות חיים למשרה זו. האם להמשיך בהגשה חוזרת?
              </p>
            )}
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setConfirmState(null)}
              >
                ביטול
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={applyingId !== null}
                onClick={() => handleApply(confirmState.match, confirmState.force)}
              >
                {confirmState.force ? "הגש שוב" : "הגש קורות חיים"}
              </button>
            </div>
          </div>
        </div>
      )}

      {logApplication && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal-card apply-log-modal">
            <h3>יומן הגשה</h3>
            <p className="cv-meta">
              סטטוס: {JOB_APP_STATUS_LABEL[logApplication.status]} · ניסיון{" "}
              {logApplication.attempt_number ?? 1}
            </p>
            {logApplication.failure_reason && (
              <p className="apply-log-error">{logApplication.failure_reason}</p>
            )}
            <ul className="apply-log-steps">
              {(logApplication.steps ?? []).map((step) => (
                <li key={step.id} className={`apply-log-step step-${step.status}`}>
                  <span className="apply-log-step-name">{step.step_name}</span>
                  <span className="apply-log-step-status">{step.status}</span>
                  {step.message && (
                    <span className="apply-log-step-message">{step.message}</span>
                  )}
                  <span className="apply-log-step-time">{formatDate(step.created_at)}</span>
                </li>
              ))}
            </ul>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setLogApplication(null)}
              >
                סגור
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="history-header">
        <h2>התאמות מהסריקה האחרונה</h2>
        <span className="history-count">
          {loading ? "טוען..." : `${matches.length} משרות`}
        </span>
      </div>

      {matches.length === 0 && !loading ? (
        <div className="empty-state">
          <div className="empty-icon">🔍</div>
          <p>אין עדיין התאמות לקובץ הזה.</p>
          {displayWarnings.length > 0 ? (
            <p className="empty-hint">
              הסריקה הסתיימה, אך לא נמצאו משרות חדשות. ראו את ההודעות למעלה לפרטים.
            </p>
          ) : (
            <p className="empty-hint">
              לחץ על "הרץ סוכן" כדי לאסוף ולדרג משרות עבור קורות החיים האלה.
            </p>
          )}
        </div>
      ) : (
        <ul className="cv-list">
          {matches.map((m) => {
            const expanded = expandedId === m.match_id;
            const app = m.job_application;
            return (
              <li key={m.match_id} className="cv-item job-item">
                <div
                  className="job-row"
                  onClick={() => setExpandedId(expanded ? null : m.match_id)}
                >
                  <div className="job-row-main">
                    <span className={`job-score ${scoreClass(m.match_score)}`}>
                      <span className="job-score-value">{m.match_score ?? "—"}</span>
                      {m.score_label && (
                        <span className="score-label">{m.score_label}</span>
                      )}
                    </span>
                    <div className="cv-info">
                      <div className="cv-name">{m.title}</div>
                      <div className="cv-meta">
                        {[m.company, m.location, m.source].filter(Boolean).join(" · ")}
                      </div>
                      <span className={`status-pill status-${m.application_status}`}>
                        {STATUS_LABEL[m.application_status]}
                      </span>
                      {app && (
                        <span className={`apply-pill apply-pill-${app.status}`}>
                          {JOB_APP_STATUS_LABEL[app.status]}
                        </span>
                      )}
                      {app?.updated_at && (
                        <span className="cv-meta apply-attempt-date">
                          ניסיון אחרון: {formatDate(app.updated_at)}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="cv-actions" onClick={(e) => e.stopPropagation()}>
                    {renderApplyButton(m)}
                    {app && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => openApplicationLog(app)}
                      >
                        פרטי הגשה
                      </button>
                    )}
                    <select
                      className="status-select"
                      value={m.application_status}
                      disabled={savingId === m.match_id}
                      onChange={(e) =>
                        handleStatusChange(m, e.target.value as ApplicationStatus)
                      }
                    >
                      {STATUS_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                    {m.job_url && (
                      <a
                        className="btn btn-ghost"
                        href={m.job_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        למשרה ↗
                      </a>
                    )}
                  </div>
                </div>

                {expanded && (
                  <div className="job-details">
                    {m.explanation && (
                      <p>
                        <b>הסבר התאמה:</b> {m.explanation}
                      </p>
                    )}
                    {m.score_reasons.length > 0 && (
                      <p>
                        <b>סיבות לציון:</b> {m.score_reasons.join(" · ")}
                      </p>
                    )}
                    {m.matched_skills.length > 0 && (
                      <p>
                        <b>כישורים תואמים:</b> {m.matched_skills.join(", ")}
                      </p>
                    )}
                    {m.missing_skills.length > 0 && (
                      <p>
                        <b>כישורים חסרים:</b> {m.missing_skills.join(", ")}
                      </p>
                    )}
                    {m.missing_mandatory.length > 0 && (
                      <p>
                        <b>דרישות חובה חסרות:</b> {m.missing_mandatory.join(", ")}
                      </p>
                    )}
                    {m.relevant_experience.length > 0 && (
                      <p>
                        <b>ניסיון רלוונטי:</b> {m.relevant_experience.join(", ")}
                      </p>
                    )}
                    {m.cv_improvements.length > 0 && (
                      <p>
                        <b>שיפורים מומלצים לקו&quot;ח:</b> {m.cv_improvements.join(" · ")}
                      </p>
                    )}
                    {app?.failure_reason && (
                      <p className="apply-log-error">
                        <b>שגיאת הגשה:</b> {app.failure_reason}
                      </p>
                    )}
                    {m.updated_at && (
                      <p className="cv-meta">עודכן: {formatDate(m.updated_at)}</p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
        </>
      )}
    </section>
  );
}
