import { useEffect, useRef, useState, type DragEvent } from "react";
import {
  Briefcase,
  Check,
  FileText,
  Globe,
  Rocket,
  Square,
  Trash2,
  Upload,
} from "lucide-react";
import { type Cv, type CvScanStatus, type JobSite } from "../lib/api";
import PipelineProgress, {
  computeScanMetrics,
  ScanSummaryCards,
} from "./PipelineProgress";

const ACCEPTED = [".pdf", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"];
const MAX_SIZE_MB = 15;

const DEFAULT_SITES: JobSite[] = [
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
];

interface Props {
  cvs: Cv[];
  loading: boolean;
  error: string | null;
  scanStatus: CvScanStatus | null;
  workspaceMatchCount: number;
  jobSites?: JobSite[];
  jobSitesLoading?: boolean;
  stopping?: boolean;
  onUpload: (files: File[]) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onRunAgent: (siteIds: string[]) => void;
  onStopAgent: () => void;
  onOpenMatches: () => void;
  onResetResults: () => Promise<void>;
  onResetFiles: () => Promise<void>;
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

function formatSize(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function SiteIcon({ siteId }: { siteId: string }) {
  if (siteId === "linkedin") {
    return <Globe size={20} strokeWidth={2} />;
  }
  if (siteId === "gotfriends") {
    return <Briefcase size={20} strokeWidth={2} />;
  }
  return <Briefcase size={20} strokeWidth={2} />;
}

export default function CvManager({
  cvs,
  loading,
  error,
  scanStatus,
  workspaceMatchCount,
  jobSites = [],
  jobSitesLoading = false,
  stopping = false,
  onUpload,
  onDelete,
  onRunAgent,
  onStopAgent,
  onOpenMatches,
  onResetResults,
  onResetFiles,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Cv | null>(null);
  const [confirmReset, setConfirmReset] = useState<"results" | "files" | null>(
    null
  );
  const [selectedCvId, setSelectedCvId] = useState<string | null>(null);
  const displaySites = jobSites.length > 0 ? jobSites : DEFAULT_SITES;
  const enabledIds = displaySites.filter((s) => s.enabled).map((s) => s.id);
  const [selectedSites, setSelectedSites] = useState<string[]>(enabledIds);

  const anyScanning = scanStatus?.running ?? false;
  const uiLocked = anyScanning;
  const hasResults =
    workspaceMatchCount > 0 ||
    Boolean(scanStatus?.error) ||
    (scanStatus?.steps?.length ?? 0) > 0;
  const scanFinished =
    !anyScanning &&
    Boolean(scanStatus) &&
    (Boolean(scanStatus?.finished_at) ||
      Boolean(scanStatus?.collection) ||
      (scanStatus?.steps?.length ?? 0) > 0);

  useEffect(() => {
    if (cvs.length === 0) {
      setSelectedCvId(null);
      return;
    }
    if (!selectedCvId || !cvs.some((c) => c.id === selectedCvId)) {
      setSelectedCvId(cvs[0].id);
    }
  }, [cvs, selectedCvId]);

  useEffect(() => {
    const sites = jobSites.length > 0 ? jobSites : DEFAULT_SITES;
    setSelectedSites(sites.filter((s) => s.enabled).map((s) => s.id));
  }, [jobSites]);

  const validate = (files: File[]): string | null => {
    for (const f of files) {
      const ext = "." + (f.name.split(".").pop() ?? "").toLowerCase();
      if (!ACCEPTED.includes(ext)) {
        return `סוג קובץ לא נתמך (${f.name}).`;
      }
      if (f.size > MAX_SIZE_MB * 1024 * 1024) {
        return `הקובץ ${f.name} גדול מדי (מקסימום ${MAX_SIZE_MB}MB).`;
      }
    }
    return null;
  };

  const handleFiles = async (files: File[]) => {
    if (anyScanning || files.length === 0) return;
    const err = validate(files);
    if (err) {
      setLocalError(err);
      return;
    }
    setLocalError(null);
    setBusy(true);
    try {
      await onUpload(files);
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (anyScanning) return;
    handleFiles(Array.from(e.dataTransfer.files));
  };

  const toggleSite = (id: string) => {
    setSelectedSites((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  };

  const metrics = computeScanMetrics(scanStatus, workspaceMatchCount);

  return (
    <section className={uiLocked ? "scan-locked-section" : undefined}>
      <section className="hero">
        <h1>
          קורות החיים שלך, <span className="accent">מאוחדים לפרופיל אחד</span>
        </h1>
        <p>
          העלה כמה קבצי קורות חיים — הסוכן יאחד את כולם לפרופיל מועמד מקיף
          ויחפש משרות שמתאימות לכל הניסיון והמיומנויות שלך יחד.
        </p>
      </section>

      <div className="upload-section">
        <div
          className={`dropzone ${dragging ? "dragging" : ""} ${busy ? "busy" : ""} ${uiLocked ? "dropzone-locked" : ""}`}
          onClick={() => {
            if (!uiLocked) inputRef.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            if (!uiLocked) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          role="button"
          tabIndex={uiLocked ? -1 : 0}
          aria-disabled={uiLocked}
          onKeyDown={(e) => {
            if (uiLocked) return;
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED.join(",")}
            multiple
            hidden
            disabled={uiLocked}
            onChange={(e) => {
              handleFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
          <div className="dropzone-icon" aria-hidden="true">
            <span className="icon-bubble icon-bubble-blue">
              <Upload size={22} />
            </span>
          </div>
          <div className="dropzone-title">
            {uiLocked
              ? "הסוכן רץ — לא ניתן להעלות קבצים כרגע"
              : busy
                ? "מעלה..."
                : "גרור לכאן קבצי קורות חיים או לחץ לבחירה"}
          </div>
          <div className="dropzone-hint">
            אפשר להעלות כמה קבצים · PDF / DOC / DOCX / TXT / תמונה · עד {MAX_SIZE_MB}MB
          </div>
        </div>

        {(localError || error) && (
          <div className="error-box">{localError || error}</div>
        )}
      </div>

      {/* A. Trigger State — configuration before scan */}
      {cvs.length > 0 && !anyScanning && (
        <div className="scan-config">
          <div className="scan-config-header">
            <span className="icon-bubble icon-bubble-blue" aria-hidden>
              <Rocket size={22} />
            </span>
            <div>
              <h2>הגדרת סריקה חכמה</h2>
              <p>
                בחרו קורות חיים פעילים ואתרי דרושים — ואז שלחו את הסוכן לחפש
                עבורכם.
              </p>
            </div>
          </div>

          <div>
            <span className="scan-config-label">קורות חיים פעילים בסריקה</span>
            <div className="cv-picker" role="listbox" aria-label="בחירת קורות חיים">
              {cvs.map((cv) => {
                const selected = selectedCvId === cv.id;
                return (
                  <button
                    key={cv.id}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className={`cv-picker-card ${selected ? "selected" : ""}`}
                    onClick={() => setSelectedCvId(cv.id)}
                  >
                    <span className="icon-bubble icon-bubble-sm icon-bubble-blue" aria-hidden>
                      <FileText size={18} />
                    </span>
                    <span className="cv-picker-card-info">
                      <span className="cv-picker-card-name">
                        {cv.display_name || cv.file_name}
                      </span>
                      <span className="cv-picker-card-meta">
                        הועלה {formatDate(cv.created_at)}
                        {cv.file_size != null && ` · ${formatSize(cv.file_size)}`}
                      </span>
                    </span>
                    <span className="cv-picker-check" aria-hidden>
                      <Check size={14} strokeWidth={3} />
                    </span>
                  </button>
                );
              })}
            </div>
            <p className="run-agent-hint" style={{ marginTop: "0.65rem", textAlign: "start" }}>
              הסוכן מאחד את כל הקבצים שהועלו לפרופיל אחד — הבחירה מסמנת את הקובץ
              המוצג כעיקרי.
            </p>
          </div>

          <div>
            <span className="scan-config-label">מקורות משרות</span>
            {jobSitesLoading && jobSites.length === 0 ? (
              <p className="site-loading">טוען אתרים זמינים…</p>
            ) : (
              <div className="site-toggle-grid" role="group" aria-label="בחירת אתרי חיפוש">
                {displaySites.map((site) => {
                  const selected = selectedSites.includes(site.id);
                  return (
                    <button
                      key={site.id}
                      type="button"
                      className={`site-toggle-card ${selected ? "selected" : ""}`}
                      disabled={!site.enabled}
                      onClick={() => site.enabled && toggleSite(site.id)}
                      aria-pressed={selected}
                    >
                      <span
                        className={`icon-bubble icon-bubble-sm ${
                          selected ? "icon-bubble-blue" : "icon-bubble-slate"
                        }`}
                        aria-hidden
                      >
                        <SiteIcon siteId={site.id} />
                      </span>
                      <span>
                        <div className="site-toggle-title">{site.label_he}</div>
                        <div className="site-toggle-desc">
                          {site.enabled ? site.description_he : "לא זמין בשרת"}
                        </div>
                      </span>
                      <span className="site-toggle-mark" aria-hidden>
                        <Check size={12} strokeWidth={3} />
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
            {selectedSites.length === 0 && (
              <div className="error-box" style={{ marginTop: 12 }}>
                יש לבחור לפחות אתר אחד.
              </div>
            )}
          </div>

          <button
            type="button"
            className="btn btn-scan-cta"
            disabled={loading || selectedSites.length === 0 || cvs.length === 0}
            onClick={() => onRunAgent(selectedSites)}
          >
            <Rocket size={20} aria-hidden />
            שגר סוכן לסריקה
          </button>
        </div>
      )}

      {/* B. Progress State */}
      {anyScanning && (
        <div className="run-agent-section" style={{ marginBottom: "1rem" }}>
          <button
            type="button"
            className="btn btn-danger btn-run-agent"
            disabled={stopping}
            onClick={onStopAgent}
          >
            <Square size={16} fill="currentColor" aria-hidden />
            {stopping ? "עוצר…" : "עצור סריקה"}
          </button>
          <p className="run-agent-hint">
            הסוכן רץ ברקע — אפשר לרענן את הדף, הסריקה תמשיך. אפשר לעצור בכל רגע.
          </p>
        </div>
      )}

      {(anyScanning || scanStatus?.error || (scanStatus?.steps?.length ?? 0) > 0) &&
        scanStatus && (
          <PipelineProgress
            scanStatus={scanStatus}
            matchCount={workspaceMatchCount}
            compact
            showSkeletons={anyScanning}
          />
        )}

      {/* C. Success State — summary metrics */}
      {scanFinished && !scanStatus?.error && workspaceMatchCount >= 0 && hasResults && (
        <div className="fade-in-list">
          <ScanSummaryCards
            scraped={metrics.scraped}
            highMatches={metrics.highMatches}
            autoApplied={metrics.autoApplied}
          />
        </div>
      )}

      {workspaceMatchCount > 0 && !anyScanning && (
        <div className="workspace-matches-banner fade-in-list">
          <div>
            <strong>{workspaceMatchCount} התאמות משרה</strong>
            <span className="workspace-matches-sub">
              מבוסס על הפרופיל המאוחד של כל קורות החיים
            </span>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={uiLocked}
            onClick={onOpenMatches}
          >
            צפה בתוצאות
          </button>
        </div>
      )}

      <div className="history-header">
        <h2>קבצי קורות החיים שהועלו</h2>
        <span className="history-count">
          {loading ? "טוען..." : `${cvs.length} קבצים`}
        </span>
      </div>

      {(cvs.length > 0 || hasResults) && !anyScanning && (
        <div className="reset-actions">
          <button
            type="button"
            className="btn btn-ghost btn-reset"
            disabled={uiLocked || busy || !hasResults}
            onClick={() => setConfirmReset("results")}
            title="מוחק התאמות משרה ותוצאות סריקה, משאיר את הקבצים"
          >
            אפס תוצאות
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-reset"
            disabled={uiLocked || busy || cvs.length === 0}
            onClick={() => setConfirmReset("files")}
            title="מוחק את כל הקבצים שהועלו ואת התוצאות"
          >
            אפס קבצים
          </button>
        </div>
      )}

      {cvs.length === 0 && !loading ? (
        <div className="empty-state">
          <div className="empty-icon" aria-hidden>
            <span className="icon-bubble icon-bubble-slate">
              <FileText size={22} />
            </span>
          </div>
          <p>עדיין לא העלית קורות חיים.</p>
          <p className="empty-hint">
            העלה קובץ אחד או יותר — לאחר מכן הגדירו מקורות ולחצו על "שגר סוכן
            לסריקה".
          </p>
        </div>
      ) : (
        <ul className={`cv-list ${uiLocked ? "cv-list-locked" : ""}`}>
          {cvs.map((cv) => (
            <li key={cv.id} className="cv-item cv-manager-item">
              <div className="cv-icon" aria-hidden>
                <FileText size={20} />
              </div>

              <div className="cv-info">
                <div className="cv-name">
                  {cv.display_name || cv.file_name}
                </div>
                <div className="cv-meta">
                  הועלה {formatDate(cv.created_at)}
                  {cv.file_size != null && ` · ${formatSize(cv.file_size)}`}
                  {cv.last_scan_at && ` · נכלל בסריקה ${formatDate(cv.last_scan_at)}`}
                </div>
                {cv.profile && (
                  <div className="cv-meta cv-profile-meta">
                    {cv.profile.name && <span>{cv.profile.name}</span>}
                    {cv.profile.seniority && <span> · {cv.profile.seniority}</span>}
                    {cv.profile.best_fit_roles.length > 0 && (
                      <span> · {cv.profile.best_fit_roles.slice(0, 3).join(", ")}</span>
                    )}
                  </div>
                )}
              </div>

              <div className="cv-actions">
                <button
                  className="btn btn-ghost btn-delete"
                  disabled={uiLocked}
                  onClick={() => setConfirmDelete(cv)}
                >
                  <Trash2 size={15} aria-hidden />
                  מחק
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {confirmDelete && !uiLocked && (
        <div className="modal-overlay" onClick={() => setConfirmDelete(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>מחיקת קורות חיים</h3>
            <p>
              מחיקת "{confirmDelete.display_name || confirmDelete.file_name}" תסיר
              את הקובץ מהרשימה. לאחר מכן אפשר להעלות קובץ חדש ולהריץ שוב את הסוכן.
            </p>
            <div className="modal-actions">
              <button
                className="btn btn-ghost"
                onClick={() => setConfirmDelete(null)}
              >
                ביטול
              </button>
              <button
                className="btn btn-danger"
                onClick={() => {
                  const id = confirmDelete.id;
                  setConfirmDelete(null);
                  onDelete(id);
                }}
              >
                מחק לצמיתות
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmReset && !uiLocked && (
        <div className="modal-overlay" onClick={() => setConfirmReset(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>
              {confirmReset === "results" ? "איפוס תוצאות" : "איפוס קבצים"}
            </h3>
            <p>
              {confirmReset === "results"
                ? "פעולה זו תמחק את כל התאמות המשרה ותוצאות הסריקה. קבצי קורות החיים יישארו — אפשר לסרוק מחדש מיד."
                : "פעולה זו תמחק את כל קבצי קורות החיים שהועלו ואת כל התוצאות. תצטרך להעלות קבצים מחדש לפני סריקה."}
            </p>
            <div className="modal-actions">
              <button
                className="btn btn-ghost"
                onClick={() => setConfirmReset(null)}
              >
                ביטול
              </button>
              <button
                className="btn btn-danger"
                onClick={async () => {
                  const kind = confirmReset;
                  setConfirmReset(null);
                  setBusy(true);
                  try {
                    if (kind === "results") await onResetResults();
                    else await onResetFiles();
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {confirmReset === "results" ? "אפס תוצאות" : "אפס קבצים"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
