import { useCallback, useEffect, useRef, useState } from "react";
import AuthView from "./components/AuthView";
import CvManager from "./components/CvManager";
import CvDetails from "./components/CvDetails";
import RunAgentModal from "./components/RunAgentModal";
import {
  checkHealth,
  clearAuthSession,
  deleteServerCv,
  DuplicateCvError,
  getCurrentUser,
  getJobMatchStatus,
  getStoredToken,
  listJobSites,
  listServerCvs,
  resetAllCvs,
  resetJobMatches,
  runJobMatcher,
  setUnauthorizedHandler,
  stopJobMatcher,
  uploadCv,
  type AuthUser,
  type Cv,
  type CvScanStatus,
  type JobSite,
} from "./lib/api";

export default function App() {
  const [serverUp, setServerUp] = useState(false);
  const [healthChecking, setHealthChecking] = useState(true);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authChecking, setAuthChecking] = useState(() => Boolean(getStoredToken()));
  const [toast, setToast] = useState<string | null>(null);

  const [cvs, setCvs] = useState<Cv[]>([]);
  const [cvsLoading, setCvsLoading] = useState(false);
  const [cvsError, setCvsError] = useState<string | null>(null);
  const [workspaceMatchCount, setWorkspaceMatchCount] = useState(0);
  const [showMatches, setShowMatches] = useState(false);

  const [scanStatus, setScanStatus] = useState<CvScanStatus | null>(null);
  const [stopping, setStopping] = useState(false);
  const [jobSites, setJobSites] = useState<JobSite[]>([
    {
      id: "drushim",
      label: "Drushim",
      label_he: "דרושים",
      description_he: "drushim.co.il",
      enabled: true,
    },
    {
      id: "linkedin",
      label: "LinkedIn",
      label_he: "לינקדאין",
      description_he: "משרות ציבוריות בישראל",
      enabled: true,
    },
    {
      id: "gotfriends",
      label: "GotFriends",
      label_he: "גוטפרנדס",
      description_he: "gotfriends.co.il",
      enabled: true,
    },
  ]);
  const [jobSitesLoading, setJobSitesLoading] = useState(false);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const pollRef = useRef<number | null>(null);
  const scanRunningRef = useRef(false);
  const healthFailCount = useRef(0);
  const resumedRef = useRef(false);

  const showToast = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 4000);
  };

  const handleLogout = useCallback(() => {
    clearAuthSession();
    setAuthUser(null);
    setCvs([]);
    setWorkspaceMatchCount(0);
    setShowMatches(false);
    setScanStatus(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      handleLogout();
      showToast("פג תוקף ההתחברות — התחבר מחדש");
    });
    return () => setUnauthorizedHandler(null);
  }, [handleLogout]);

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

  const refreshCvs = useCallback(async () => {
    if (!authUser) return;
    setCvsLoading(true);
    setCvsError(null);
    try {
      const data = await listServerCvs();
      setCvs(data.cvs);
      setWorkspaceMatchCount(data.workspace_match_count ?? 0);
    } catch (e) {
      setCvsError(e instanceof Error ? e.message : "שגיאה בטעינת קורות החיים");
    } finally {
      setCvsLoading(false);
    }
  }, [authUser]);

  useEffect(() => {
    scanRunningRef.current = scanStatus?.running ?? false;
    if (!(scanStatus?.running ?? false)) {
      setStopping(false);
    }
  }, [scanStatus?.running]);

  const applyHealthResult = useCallback(
    (up: boolean) => {
      if (up) {
        healthFailCount.current = 0;
        setServerUp(true);
        return;
      }
      if (scanRunningRef.current) return;
      healthFailCount.current += 1;
      if (healthFailCount.current >= 3) setServerUp(false);
    },
    []
  );

  const pingServer = useCallback(async () => {
    setHealthChecking(true);
    const up = await checkHealth();
    applyHealthResult(up);
    if (up && authUser) refreshCvs();
    setHealthChecking(false);
    return up;
  }, [refreshCvs, applyHealthResult, authUser]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const schedule = (up: boolean) => {
      if (cancelled) return;
      timer = window.setTimeout(async () => {
        if (cancelled) return;
        const next = await checkHealth();
        if (!cancelled) {
          applyHealthResult(next);
          if (next && authUser) refreshCvs();
          setHealthChecking(false);
          schedule(next || scanRunningRef.current);
        }
      }, up ? 10000 : 3000);
    };

    setHealthChecking(true);
    checkHealth().then((up) => {
      if (cancelled) return;
      applyHealthResult(up);
      setHealthChecking(false);
      if (up && authUser) refreshCvs();
      schedule(up || scanRunningRef.current);
    });

    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [refreshCvs, applyHealthResult, authUser]);

  useEffect(() => {
    if (serverUp && authUser) refreshCvs();
  }, [serverUp, authUser, refreshCvs]);

  const refreshJobSites = useCallback(async () => {
    setJobSitesLoading(true);
    try {
      const data = await listJobSites();
      setJobSites(data.sites);
    } catch {
      setJobSites([
        {
          id: "drushim",
          label: "Drushim",
          label_he: "דרושים",
          description_he: "drushim.co.il",
          enabled: true,
        },
        {
          id: "linkedin",
          label: "LinkedIn",
          label_he: "לינקדאין",
          description_he: "משרות ציבוריות בישראל",
          enabled: true,
        },
        {
          id: "gotfriends",
          label: "GotFriends",
          label_he: "גוטפרנדס",
          description_he: "gotfriends.co.il",
          enabled: true,
        },
      ]);
    } finally {
      setJobSitesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (serverUp) refreshJobSites();
  }, [serverUp, refreshJobSites]);

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
        const s = await getJobMatchStatus();
        setScanStatus(s);
        if (!s.running) {
          stopPolling();
          refreshCvs();
          if ((s.match_count ?? 0) > 0) {
            setWorkspaceMatchCount(s.match_count ?? 0);
          }
          if (s.error) {
            showToast(s.error);
          }
        }
      } catch {
        /* server temporarily unreachable — keep polling */
      }
    }, 2500);
  }, [stopPolling, refreshCvs]);

  useEffect(() => stopPolling, [stopPolling]);

  // Resume UI polling after refresh if a scan is still running on the server.
  useEffect(() => {
    if (!serverUp || !authUser || resumedRef.current) return;
    resumedRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        const s = await getJobMatchStatus();
        if (cancelled) return;
        setScanStatus(s);
        if (s.running) {
          startPolling();
        } else if ((s.match_count ?? 0) > 0) {
          setWorkspaceMatchCount(s.match_count ?? 0);
        }
      } catch {
        /* ignore — health poll will retry */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [serverUp, authUser, startPolling]);

  useEffect(() => {
    // Allow resume polling again after a new login.
    resumedRef.current = false;
  }, [authUser?.id]);

  const handleUpload = async (files: File[]) => {
    if (scanRunningRef.current) {
      showToast("לא ניתן להעלות קבצים בזמן סריקה");
      return;
    }
    let uploaded = 0;
    for (const file of files) {
      try {
        await uploadCv(file);
        uploaded += 1;
      } catch (e) {
        if (e instanceof DuplicateCvError) {
          const asNew = window.confirm(
            `הקובץ "${file.name}" כבר הועלה בעבר. להעלות אותו כגרסה נפרדת?`
          );
          if (asNew) {
            await uploadCv(file, { asNewVersion: true });
            uploaded += 1;
          }
        } else {
          showToast(
            `העלאת "${file.name}" נכשלה: ${e instanceof Error ? e.message : ""}`
          );
        }
      }
    }
    await refreshCvs();
    if (uploaded > 0) {
      showToast(
        uploaded === 1 ? "קורות החיים הועלו" : `${uploaded} קבצים הועלו`
      );
    }
  };

  const handleDelete = async (id: string) => {
    if (scanRunningRef.current) {
      showToast("לא ניתן למחוק קבצים בזמן סריקה");
      return;
    }
    try {
      await deleteServerCv(id);
      await refreshCvs();
      showToast("קובץ קורות החיים נמחק — אפשר להעלות חדש ולסרוק מחדש");
    } catch (e) {
      showToast(`מחיקה נכשלה: ${e instanceof Error ? e.message : ""}`);
    }
  };

  const handleResetResults = async () => {
    if (scanRunningRef.current) {
      showToast("לא ניתן לאפס בזמן סריקה");
      return;
    }
    try {
      await resetJobMatches();
      setScanStatus(null);
      setWorkspaceMatchCount(0);
      setShowMatches(false);
      await refreshCvs();
      showToast("התוצאות אופסו — אפשר לסרוק מחדש");
    } catch (e) {
      showToast(`איפוס תוצאות נכשל: ${e instanceof Error ? e.message : ""}`);
    }
  };

  const handleResetFiles = async () => {
    if (scanRunningRef.current) {
      showToast("לא ניתן לאפס בזמן סריקה");
      return;
    }
    try {
      await resetAllCvs();
      setScanStatus(null);
      setWorkspaceMatchCount(0);
      setShowMatches(false);
      setCvs([]);
      await refreshCvs();
      showToast("כל הקבצים והתוצאות נמחקו");
    } catch (e) {
      showToast(`איפוס קבצים נכשל: ${e instanceof Error ? e.message : ""}`);
    }
  };

  const handleRun = () => {
    if (scanRunningRef.current) return;
    if (cvs.length === 0) {
      showToast("יש להעלות לפחות קובץ קורות חיים אחד");
      return;
    }
    window.setTimeout(() => setRunModalOpen(true), 0);
  };

  const handleStop = async () => {
    setStopping(true);
    try {
      await stopJobMatcher();
      showToast("עוצר את הסריקה…");
      startPolling();
    } catch (e) {
      setStopping(false);
      showToast(
        `עצירת הסריקה נכשלה: ${e instanceof Error ? e.message : ""}`
      );
    }
  };

  const confirmRun = async (siteIds: string[]) => {
    setRunModalOpen(false);
    try {
      await runJobMatcher({ job_sites: siteIds });
      setShowMatches(false);
      setScanStatus({
        running: true,
        started_at: new Date().toISOString(),
        finished_at: null,
        error: null,
        warnings: [],
        collection: null,
        current_step: null,
        detail: "מתחיל סריקה…",
        steps: [],
        log: [],
        latest_scan: null,
      });
      startPolling();
    } catch (e) {
      showToast(
        `הרצת הסוכן נכשלה: ${e instanceof Error ? e.message : ""}`
      );
    }
  };

  const primaryCv = cvs[0];
  const scanActive = scanStatus?.running ?? false;

  return (
    <div className={`app ${scanActive ? "app-scan-locked" : ""}`}>
      {scanActive && (
        <div className="scan-lock-banner" role="status">
          <span>הסוכן רץ — הממשק נעול. אפשר לעצור את הסריקה בלבד.</span>
          <button
            type="button"
            className="btn btn-danger btn-sm"
            disabled={stopping}
            onClick={handleStop}
          >
            {stopping ? "עוצר…" : "עצור סריקה"}
          </button>
        </div>
      )}

      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <span className="logo-icon" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                <path
                  d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinejoin="round"
                />
                <path
                  d="M14 2v6h6M8 13h8M8 17h5"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <span className="logo-text">
              Resume<b>Agent</b>
            </span>
          </div>

          <div className="header-actions">
            {authUser?.email && (
              <span className="user-chip" title={authUser.email}>
                {authUser.email}
              </span>
            )}
            {authUser && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={handleLogout}
                disabled={scanActive}
              >
                התנתק
              </button>
            )}
            <span
              className={`server-status ${serverUp ? "up" : "down"}`}
              title={
                serverUp
                  ? "השרת מחובר"
                  : healthChecking
                    ? "בודק חיבור..."
                    : "השרת לא זמין — לחץ לניסיון חוזר"
              }
              onClick={() => {
                if (!scanActive) pingServer();
              }}
              role="button"
              tabIndex={scanActive ? -1 : 0}
              onKeyDown={(e) => {
                if (scanActive) return;
                if (e.key === "Enter" || e.key === " ") pingServer();
              }}
            >
              <span className="status-dot" />
              <span className="server-status-text">
                {healthChecking
                  ? "בודק חיבור..."
                  : serverUp
                    ? "סוכן מחובר"
                    : "סוכן לא זמין"}
              </span>
            </span>
          </div>
        </div>
      </header>

      <main className="main">
        {authChecking ? (
          <div className="empty-state">
            <p>בודק התחברות…</p>
          </div>
        ) : !authUser ? (
          <AuthView
            onAuthenticated={(user) => {
              setAuthUser(user);
              showToast("התחברת בהצלחה");
            }}
          />
        ) : !serverUp && !scanActive ? (
          <div className="empty-state">
            <div className="empty-icon">🔌</div>
            <p>השרת של הסוכן לא זמין.</p>
            <p className="empty-hint">
              הרץ מהשורש: <code>./scripts/share-dev.sh</code>
              <br />
              או ידנית בתיקיית <code>ai-job-agent</code>:{" "}
              <code>python src/api_server.py</code>
            </p>
            <button
              className="btn btn-primary"
              disabled={healthChecking}
              onClick={() => pingServer()}
            >
              {healthChecking ? "בודק..." : "נסה שוב"}
            </button>
          </div>
        ) : showMatches && primaryCv && !scanActive ? (
          <CvDetails
            cvId={primaryCv.id}
            cv={primaryCv}
            scanStatus={scanStatus}
            workspaceMode
            onBack={() => setShowMatches(false)}
          />
        ) : (
          <CvManager
            cvs={cvs}
            loading={cvsLoading}
            error={cvsError}
            scanStatus={scanStatus}
            workspaceMatchCount={workspaceMatchCount}
            stopping={stopping}
            onUpload={handleUpload}
            onDelete={handleDelete}
            onRunAgent={handleRun}
            onStopAgent={handleStop}
            onOpenMatches={() => {
              if (!scanActive) setShowMatches(true);
            }}
            onResetResults={handleResetResults}
            onResetFiles={handleResetFiles}
          />
        )}
      </main>

      <footer className="footer">
        <span>Resume Agent</span>
        <span className="footer-sep">·</span>
        <span>סוכן חיפוש עבודה חכם</span>
      </footer>

      {toast && <div className="toast">{toast}</div>}

      {runModalOpen && !scanActive && (
        <RunAgentModal
          cvName={`${cvs.length} קבצי קורות חיים`}
          sites={jobSites}
          loading={jobSitesLoading}
          onConfirm={confirmRun}
          onCancel={() => setRunModalOpen(false)}
        />
      )}
    </div>
  );
}
