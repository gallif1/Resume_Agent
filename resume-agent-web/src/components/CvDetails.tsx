import { useCallback, useEffect, useRef, useState } from "react";
import {
  getCvMatches,
  getCvScanStatus,
  parseScanSummary,
  updateMatchStatus,
  type ApplicationStatus,
  type Cv,
  type CvMatch,
  type CvScanStatus,
} from "../lib/api";
import PipelineProgress from "./PipelineProgress";

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

  // Reload matches when a scan for this CV finishes.
  useEffect(() => {
    if (prevRunning.current && !running && scanCvId === cvId) {
      load();
    }
    prevRunning.current = running;
  }, [running, scanCvId, cvId, load]);

  const handleStatusChange = async (
    match: CvMatch,
    status: ApplicationStatus
  ) => {
    setSavingId(match.match_id);
    // Optimistic update.
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

      <PipelineProgress scanStatus={showScanPanel} />

      {error && <div className="error-box">{error}</div>}

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
                    </div>
                  </div>
                  <div className="cv-actions" onClick={(e) => e.stopPropagation()}>
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
    </section>
  );
}
