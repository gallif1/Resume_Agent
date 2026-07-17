import { useCallback, useEffect, useRef, useState } from "react";
import CvManager from "./components/CvManager";
import CvDetails from "./components/CvDetails";
import RunAgentModal from "./components/RunAgentModal";
import {
  checkHealth,
  deleteServerCv,
  DuplicateCvError,
  getJobMatchStatus,
  listJobSites,
  listServerCvs,
  runJobMatcher,
  uploadCv,
  type Cv,
  type CvScanStatus,
  type JobSite,
} from "./lib/api";

export default function App() {
  const [serverUp, setServerUp] = useState(false);
  const [healthChecking, setHealthChecking] = useState(true);
  const [toast, setToast] = useState<string | null>(null);

  const [cvs, setCvs] = useState<Cv[]>([]);
  const [cvsLoading, setCvsLoading] = useState(false);
  const [cvsError, setCvsError] = useState<string | null>(null);
  const [workspaceMatchCount, setWorkspaceMatchCount] = useState(0);
  const [showMatches, setShowMatches] = useState(false);

  const [scanStatus, setScanStatus] = useState<CvScanStatus | null>(null);
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

  const showToast = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 4000);
  };

  const refreshCvs = useCallback(async () => {
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
  }, []);

  useEffect(() => {
    scanRunningRef.current = scanStatus?.running ?? false;
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
    if (up) refreshCvs();
    setHealthChecking(false);
    return up;
  }, [refreshCvs, applyHealthResult]);

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
          if (next) refreshCvs();
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
      if (up) refreshCvs();
      schedule(up || scanRunningRef.current);
    });

    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [refreshCvs, applyHealthResult]);

  useEffect(() => {
    if (serverUp) refreshCvs();
  }, [serverUp, refreshCvs]);

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
        }
      } catch {
        /* server temporarily unreachable — keep polling */
      }
    }, 2500);
  }, [stopPolling, refreshCvs]);

  useEffect(() => stopPolling, [stopPolling]);

  const handleUpload = async (files: File[]) => {
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
    try {
      await deleteServerCv(id);
      await refreshCvs();
      showToast("קובץ קורות החיים נמחק");
    } catch (e) {
      showToast(`מחיקה נכשלה: ${e instanceof Error ? e.message : ""}`);
    }
  };

  const handleRun = () => {
    if (cvs.length === 0) {
      showToast("יש להעלות לפחות קובץ קורות חיים אחד");
      return;
    }
    window.setTimeout(() => setRunModalOpen(true), 0);
  };

  const confirmRun = async (siteIds: string[]) => {
    setRunModalOpen(false);
    try {
      await runJobMatcher({ job_sites: siteIds });
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
    <div className="app">
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

          <span
            className={`server-status ${serverUp ? "up" : "down"}`}
            title={
              serverUp
                ? "השרת מחובר"
                : healthChecking
                  ? "בודק חיבור..."
                  : "השרת לא זמין — לחץ לניסיון חוזר"
            }
            onClick={() => pingServer()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
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
      </header>

      <main className="main">
        {!serverUp && !scanActive ? (
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
        ) : showMatches && primaryCv ? (
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
            onUpload={handleUpload}
            onDelete={handleDelete}
            onRunAgent={handleRun}
            onOpenMatches={() => setShowMatches(true)}
          />
        )}
      </main>

      <footer className="footer">
        <span>Resume Agent</span>
        <span className="footer-sep">·</span>
        <span>סוכן חיפוש עבודה חכם</span>
      </footer>

      {toast && <div className="toast">{toast}</div>}

      {runModalOpen && (
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
