import { useCallback, useEffect, useRef, useState } from "react";
import {
  getJobs,
  getPipelineStatus,
  runPipeline,
  type Job,
  type PipelineStatus,
} from "../lib/api";

interface Props {
  serverUp: boolean;
}

const STEP_ICONS: Record<string, string> = {
  pending: "○",
  running: "◐",
  success: "✓",
  failed: "✗",
  skipped: "−",
};

function scoreClass(score: number | null): string {
  if (score == null) return "";
  if (score >= 85) return "score-high";
  if (score >= 70) return "score-mid";
  return "score-low";
}

export default function JobsPage({ serverUp }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [minScore, setMinScore] = useState(55);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const pollRef = useRef<number | null>(null);

  const loadJobs = useCallback(async (score: number) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getJobs(score);
      setJobs(data.jobs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינת המשרות");
    } finally {
      setLoading(false);
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await getPipelineStatus();
        setStatus(s);
        if (!s.running) {
          stopPolling();
          loadJobs(minScore);
        }
      } catch {
        /* server temporarily unreachable — keep polling */
      }
    }, 2500);
  }, [stopPolling, loadJobs, minScore]);

  useEffect(() => {
    if (!serverUp) return;
    loadJobs(minScore);
    // Resume progress view if a pipeline is already running on the server.
    getPipelineStatus()
      .then((s) => {
        if (s.steps.length > 0) setStatus(s);
        if (s.running) startPolling();
      })
      .catch(() => {});
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverUp]);

  const handleRun = async () => {
    setError(null);
    try {
      await runPipeline();
      const s = await getPipelineStatus();
      setStatus(s);
      startPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בהרצת הפייפליין");
    }
  };

  if (!serverUp) {
    return (
      <div className="empty-state">
        <div className="empty-icon">🔌</div>
        <p>השרת של הסוכן לא זמין.</p>
        <p className="empty-hint">
          הרץ בתיקיית <code>ai-job-agent</code>:{" "}
          <code>python src/api_server.py</code>
        </p>
      </div>
    );
  }

  const running = status?.running ?? false;

  return (
    <section>
      <div className="jobs-toolbar">
        <button className="btn btn-primary" onClick={handleRun} disabled={running}>
          {running ? "רץ עכשיו..." : "▶ הרץ חיפוש עבודה"}
        </button>

        <label className="score-filter">
          ציון מינימלי:
          <input
            type="number"
            min={0}
            max={100}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            onBlur={() => loadJobs(minScore)}
            onKeyDown={(e) => {
              if (e.key === "Enter") loadJobs(minScore);
            }}
          />
        </label>

        <button
          className="btn btn-ghost"
          onClick={() => loadJobs(minScore)}
          disabled={loading}
        >
          רענן
        </button>
      </div>

      {status && status.steps.length > 0 && (running || status.error) && (
        <div className="pipeline-panel">
          <div className="pipeline-steps">
            {status.steps.map((step) => (
              <div key={step.key} className={`pipeline-step ${step.status}`}>
                <span className="step-icon">{STEP_ICONS[step.status]}</span>
                <span>{step.name}</span>
              </div>
            ))}
          </div>
          {status.error && <div className="error-box">{status.error}</div>}
          <button
            className="btn btn-ghost log-toggle"
            onClick={() => setShowLog((v) => !v)}
          >
            {showLog ? "הסתר יומן" : "הצג יומן"}
          </button>
          {showLog && (
            <pre className="pipeline-log" dir="ltr">
              {status.log.join("\n") || "..."}
            </pre>
          )}
        </div>
      )}

      {error && <div className="error-box">{error}</div>}

      <div className="history-header">
        <h2>משרות מתאימות</h2>
        <span className="history-count">
          {loading ? "טוען..." : `${jobs.length} משרות עם ציון ${minScore}+`}
        </span>
      </div>

      {jobs.length === 0 && !loading ? (
        <div className="empty-state">
          <div className="empty-icon">🔍</div>
          <p>אין עדיין משרות מעל הציון הזה.</p>
          <p className="empty-hint">
            לחץ על "הרץ חיפוש עבודה" כדי לאסוף ולדרג משרות חדשות.
          </p>
        </div>
      ) : (
        <ul className="cv-list">
          {jobs.map((job) => {
            const expanded = expandedId === job.id;
            return (
              <li key={job.id} className="cv-item job-item">
                <div
                  className="job-row"
                  onClick={() => setExpandedId(expanded ? null : job.id)}
                >
                  <div className="job-row-main">
                    <span className={`job-score ${scoreClass(job.match_score)}`}>
                      <span className="job-score-value">{job.match_score ?? "—"}</span>
                    </span>
                    <div className="cv-info">
                      <div className="cv-name">{job.title}</div>
                      <div className="cv-meta">
                        {[job.company, job.location, job.source]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                      {job.application_status === "sent" && (
                        <span className="badge">נשלחו קו"ח</span>
                      )}
                    </div>
                  </div>
                  <div className="cv-actions">
                    <a
                      className="btn btn-ghost"
                      href={job.job_url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      למשרה ↗
                    </a>
                  </div>
                </div>

                {expanded && (
                  <div className="job-details">
                    {job.ai_explanation && (
                      <p>
                        <b>הסבר:</b> {job.ai_explanation}
                      </p>
                    )}
                    {job.ai_strengths && (
                      <p>
                        <b>חוזקות:</b> {job.ai_strengths}
                      </p>
                    )}
                    {job.ai_missing_skills && (
                      <p>
                        <b>פערים:</b> {job.ai_missing_skills}
                      </p>
                    )}
                    {!job.ai_explanation && job.match_reason && (
                      <p>
                        <b>נימוק:</b> {job.match_reason}
                      </p>
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
