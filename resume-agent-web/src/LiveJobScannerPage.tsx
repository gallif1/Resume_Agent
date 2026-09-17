import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  Loader2,
  Pause,
  Play,
  Square,
  Trash2,
} from "lucide-react";
import AuthView from "./components/AuthView";
import {
  clearAuthSession,
  getCurrentUser,
  getStoredToken,
  type AuthUser,
} from "./lib/api";
import {
  addLiveSource,
  deleteLiveSource,
  getLiveScannerStatus,
  liveScannerClear,
  liveScannerPause,
  liveScannerPlay,
  liveScannerStop,
  updateLiveSource,
  type LiveActivity,
  type LiveScannerSnapshot,
  type LiveScannerState,
  type LiveSessionJob,
  type LiveSource,
} from "./lib/liveScannerApi";
import { APP_VERSION } from "./lib/version";

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

function statusLabel(status?: string): { text: string; className: string } {
  switch (status) {
    case "RUNNING":
      return { text: "RUNNING", className: "live-status-running" };
    case "BUILDING_BASELINE":
      return { text: "BUILDING BASELINE", className: "live-status-baseline" };
    case "PAUSED":
      return { text: "PAUSED", className: "live-status-paused" };
    case "STOPPED":
    default:
      return { text: "STOPPED", className: "live-status-stopped" };
  }
}

function sourceStatusBadge(status?: string): { text: string; className: string } {
  switch (status) {
    case "LIVE":
      return { text: "LIVE", className: "live-badge-live" };
    case "BASELINE_SCANNING":
      return { text: "BUILDING BASELINE", className: "live-badge-baseline" };
    case "ERROR":
      return { text: "ERROR", className: "live-badge-error" };
    case "DISABLED":
      return { text: "DISABLED", className: "live-badge-disabled" };
    default:
      return { text: "NOT INITIALIZED", className: "live-badge-pending" };
  }
}

const emptyState: LiveScannerState = {
  status: "STOPPED",
  jobs_checked: 0,
  new_jobs: 0,
  duplicates_skipped: 0,
  relevant_jobs: 0,
  baseline_jobs: 0,
  sources_monitored: 0,
  sources_initialized: 0,
  sources_failed: 0,
};

export default function LiveJobScannerPage() {
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authChecking, setAuthChecking] = useState(() => Boolean(getStoredToken()));
  const [snapshot, setSnapshot] = useState<LiveScannerSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showAdd, setShowAdd] = useState(false);
  const [newCompany, setNewCompany] = useState("");
  const [newProvider, setNewProvider] = useState("lever");
  const [newBoard, setNewBoard] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [newInterval, setNewInterval] = useState(300);

  const refresh = useCallback(async () => {
    try {
      const data = await getLiveScannerStatus();
      setSnapshot(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load scanner status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!getStoredToken()) {
      setAuthChecking(false);
      return;
    }
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

  useEffect(() => {
    if (!authUser) return;
    void refresh();
    const id = window.setInterval(() => {
      void refresh();
    }, 2500);
    return () => window.clearInterval(id);
  }, [authUser, refresh]);

  const state = snapshot?.state ?? emptyState;
  const sources: LiveSource[] = snapshot?.sources ?? [];
  const activity: LiveActivity[] = snapshot?.activity ?? [];
  const newJobs: LiveSessionJob[] = snapshot?.new_jobs ?? [];
  const badge = statusLabel(state.status);

  const runAction = async (fn: () => Promise<unknown>) => {
    setActionBusy(true);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActionBusy(false);
    }
  };

  const handleClear = () => {
    const ok = window.confirm(
      "Clear the Live Job Scanner activity log and session counters?\n\n" +
        "This will NOT delete baseline data, known job IDs, or jobs in your database."
    );
    if (!ok) return;
    void runAction(() => liveScannerClear());
  };

  const handleAddSource = async () => {
    setActionBusy(true);
    setError(null);
    try {
      await addLiveSource({
        company_name: newCompany.trim(),
        provider: newProvider,
        board_identifier: newBoard.trim(),
        careers_url: newUrl.trim() || undefined,
        scan_interval_seconds: newInterval,
        enabled: true,
      });
      setShowAdd(false);
      setNewCompany("");
      setNewBoard("");
      setNewUrl("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add source");
    } finally {
      setActionBusy(false);
    }
  };

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
              Live <b>Job Scanner</b>
            </span>
            <span className="app-version" dir="ltr">
              {APP_VERSION}
            </span>
          </div>
          <div className="header-actions">
            <a href="/cv-tailor" className="btn btn-secondary btn-sm">
              Create Tailored CV
            </a>
            <a href="/job-apply" className="btn btn-secondary btn-sm">
              Auto Apply
            </a>
            <a href="/" className="btn btn-ghost btn-sm">
              <ArrowLeft size={16} aria-hidden="true" />
              Job search
            </a>
          </div>
        </div>
      </header>

      <main className="main cv-tailor-main live-scanner-main">
        <section className="card cv-tailor-card live-scanner-card">
          <div className="live-scanner-title-row">
            <h1 className="cv-tailor-title">Live Job Scanner</h1>
            <span className={`live-status-pill ${badge.className}`} dir="ltr">
              {badge.text}
            </span>
          </div>
          <p className="cv-tailor-subtitle">
            Continuously monitor company career boards. First scan builds a baseline;
            only jobs published after that appear as new.
          </p>

          <div className="live-controls" dir="ltr">
            <button
              type="button"
              className="btn btn-primary"
              disabled={actionBusy}
              onClick={() => void runAction(() => liveScannerPlay())}
            >
              <Play size={16} aria-hidden="true" /> PLAY
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={actionBusy}
              onClick={() => void runAction(() => liveScannerPause())}
            >
              <Pause size={16} aria-hidden="true" /> PAUSE
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={actionBusy}
              onClick={() => void runAction(() => liveScannerStop())}
            >
              <Square size={16} aria-hidden="true" /> STOP
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={actionBusy}
              onClick={handleClear}
            >
              <Trash2 size={16} aria-hidden="true" /> CLEAR
            </button>
            {actionBusy || loading ? (
              <Loader2 className="spin" size={18} aria-hidden="true" />
            ) : null}
          </div>

          {error ? <div className="error-banner">{error}</div> : null}

          <div className="live-stats-grid" dir="ltr">
            <div className="live-stat">
              <span className="live-stat-label">Sources</span>
              <span className="live-stat-value">{state.sources_monitored}</span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Initialized</span>
              <span className="live-stat-value">
                {state.sources_initialized} / {state.sources_monitored}
              </span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Jobs checked</span>
              <span className="live-stat-value">{state.jobs_checked}</span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Baseline jobs</span>
              <span className="live-stat-value">{state.baseline_jobs}</span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">New jobs</span>
              <span className="live-stat-value">{state.new_jobs}</span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Duplicates skipped</span>
              <span className="live-stat-value">{state.duplicates_skipped}</span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Relevant</span>
              <span className="live-stat-value">{state.relevant_jobs}</span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Last scan</span>
              <span className="live-stat-value">{formatTime(state.last_scan_at)}</span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Next scan</span>
              <span className="live-stat-value">{formatTime(state.next_scan_at)}</span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Session start</span>
              <span className="live-stat-value">
                {formatTime(state.session_started_at)}
              </span>
            </div>
            <div className="live-stat">
              <span className="live-stat-label">Failed sources</span>
              <span className="live-stat-value">{state.sources_failed}</span>
            </div>
          </div>

          {state.status === "BUILDING_BASELINE" ? (
            <p className="live-hint" dir="ltr">
              Building baseline — active jobs are indexed but not treated as newly
              published.
            </p>
          ) : null}
          {state.status === "RUNNING" && state.new_jobs === 0 ? (
            <p className="live-hint" dir="ltr">
              Waiting for newly published jobs…
            </p>
          ) : null}
        </section>

        <section className="card cv-tailor-card live-scanner-card">
          <h2 className="live-section-title" dir="ltr">
            Live Activity
          </h2>
          <div className="live-activity" dir="ltr">
            {activity.length === 0 ? (
              <p className="muted">No activity yet. Press PLAY to start.</p>
            ) : (
              <ul className="live-activity-list">
                {activity.map((item) => (
                  <li
                    key={item.id}
                    className={`live-activity-item live-activity-${item.level}`}
                  >
                    <span className="live-activity-time">
                      {formatTime(item.created_at)}
                    </span>
                    <span className="live-activity-msg">{item.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <section className="card cv-tailor-card live-scanner-card">
          <h2 className="live-section-title" dir="ltr">
            New Jobs
          </h2>
          <p className="muted" dir="ltr">
            Only jobs discovered after each source’s baseline. Open the original
            link, or use Job search / Tailored CV / Auto Apply with the job URL.
          </p>
          {newJobs.length === 0 ? (
            <p className="muted">No new live jobs this session.</p>
          ) : (
            <div className="live-table-wrap" dir="ltr">
              <table className="live-table">
                <thead>
                  <tr>
                    <th>Found</th>
                    <th>Title</th>
                    <th>Company</th>
                    <th>Location</th>
                    <th>Provider</th>
                    <th>Match</th>
                    <th>Link</th>
                  </tr>
                </thead>
                <tbody>
                  {newJobs.map((job) => (
                    <tr key={job.id}>
                      <td>{formatTime(job.discovered_at)}</td>
                      <td>{job.title}</td>
                      <td>{job.company}</td>
                      <td>{job.location || "—"}</td>
                      <td>{job.provider}</td>
                      <td>{job.match_score ?? "—"}</td>
                      <td>
                        {job.job_url ? (
                          <a href={job.job_url} target="_blank" rel="noreferrer">
                            Open
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card cv-tailor-card live-scanner-card">
          <div className="live-section-header" dir="ltr">
            <h2 className="live-section-title">Sources</h2>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setShowAdd((v) => !v)}
            >
              {showAdd ? "Cancel" : "Add source"}
            </button>
          </div>

          {showAdd ? (
            <div className="live-add-form" dir="ltr">
              <label className="field-label">
                Company
                <input
                  className="job-apply-input"
                  value={newCompany}
                  onChange={(e) => setNewCompany(e.target.value)}
                />
              </label>
              <label className="field-label">
                Provider
                <select
                  className="job-apply-input"
                  value={newProvider}
                  onChange={(e) => setNewProvider(e.target.value)}
                >
                  <option value="lever">Lever</option>
                  <option value="greenhouse">Greenhouse</option>
                  <option value="ashby">Ashby</option>
                  <option value="workday">Workday</option>
                  <option value="comeet">Comeet</option>
                </select>
              </label>
              <label className="field-label">
                Board identifier
                <input
                  className="job-apply-input"
                  placeholder="e.g. leverdemo / airbnb / nvidia.wd5…/Site"
                  value={newBoard}
                  onChange={(e) => setNewBoard(e.target.value)}
                />
              </label>
              <label className="field-label">
                Careers URL (optional)
                <input
                  className="job-apply-input"
                  value={newUrl}
                  onChange={(e) => setNewUrl(e.target.value)}
                />
              </label>
              <label className="field-label">
                Interval (seconds)
                <input
                  className="job-apply-input"
                  type="number"
                  min={60}
                  value={newInterval}
                  onChange={(e) => setNewInterval(Number(e.target.value) || 300)}
                />
              </label>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={actionBusy || !newCompany.trim()}
                onClick={() => void handleAddSource()}
              >
                Save source
              </button>
            </div>
          ) : null}

          <div className="live-table-wrap" dir="ltr">
            <table className="live-table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Provider</th>
                  <th>Interval</th>
                  <th>Status</th>
                  <th>Last scan</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sources.map((src) => {
                  const sb = sourceStatusBadge(String(src.status));
                  return (
                    <tr key={src.id}>
                      <td>{src.company_name}</td>
                      <td>{src.provider}</td>
                      <td>{Math.round((src.scan_interval_seconds || 0) / 60)}m</td>
                      <td>
                        <span className={`live-source-badge ${sb.className}`}>
                          {sb.text}
                        </span>
                        {src.last_error ? (
                          <div className="live-source-error">{src.last_error}</div>
                        ) : null}
                      </td>
                      <td>{formatTime(src.last_scan_at)}</td>
                      <td className="live-source-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={actionBusy}
                          onClick={() =>
                            void runAction(() =>
                              updateLiveSource(src.id, {
                                enabled: !src.enabled,
                              })
                            )
                          }
                        >
                          {src.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={actionBusy}
                          onClick={() => {
                            const next = window.prompt(
                              "Scan interval (seconds)",
                              String(src.scan_interval_seconds || 300)
                            );
                            if (!next) return;
                            const seconds = Number(next);
                            if (!Number.isFinite(seconds) || seconds < 60) {
                              setError("Interval must be at least 60 seconds");
                              return;
                            }
                            void runAction(() =>
                              updateLiveSource(src.id, {
                                scan_interval_seconds: seconds,
                              })
                            );
                          }}
                        >
                          Interval
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={actionBusy}
                          onClick={() => {
                            if (
                              !window.confirm(
                                `Remove source ${src.company_name}? Known IDs for this source will be removed; jobs stay in the main database.`
                              )
                            ) {
                              return;
                            }
                            void runAction(() => deleteLiveSource(src.id));
                          }}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}
