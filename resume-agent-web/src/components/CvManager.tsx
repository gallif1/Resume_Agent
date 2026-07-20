import { useRef, useState, type DragEvent } from "react";
import { FileText, Trash2, Upload } from "lucide-react";
import { type Cv, type CvScanStatus } from "../lib/api";
import { computeScanMetrics, ScanSummaryCards } from "./PipelineProgress";

const ACCEPTED = [".pdf", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"];
const MAX_SIZE_MB = 15;

interface Props {
  cvs: Cv[];
  loading: boolean;
  error: string | null;
  scanStatus: CvScanStatus | null;
  workspaceMatchCount: number;
  jobSitesLoading?: boolean;
  stopping?: boolean;
  analyzing?: boolean;
  onUpload: (files: File[]) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onAnalyze: (cvId: string) => Promise<void>;
  onStartSearch: (cvId: string, domains: string[], siteIds: string[]) => void;
  onStopAgent: () => void;
  selectedCvId: string | null;
  onSelectCv: (cvId: string) => void;
  onNewScan: (cvId: string) => void;
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

export default function CvManager({
  cvs,
  loading,
  error,
  scanStatus,
  workspaceMatchCount,
  jobSitesLoading = false,
  stopping: _stopping = false,
  analyzing = false,
  onUpload,
  onDelete,
  onAnalyze: _onAnalyze,
  onStartSearch: _onStartSearch,
  onStopAgent: _onStopAgent,
  selectedCvId,
  onSelectCv,
  onNewScan,
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

  const anyScanning = scanStatus?.running ?? false;
  const uiLocked = anyScanning || analyzing;
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
    void handleFiles(Array.from(e.dataTransfer.files));
  };

  const metrics = computeScanMetrics(scanStatus, workspaceMatchCount);

  return (
    <section className={uiLocked ? "scan-locked-section" : undefined}>
      <section className="hero">
        <h1>
          קורות החיים שלך, <span className="accent">חיפוש משרות ממוקד</span>
        </h1>
        <p>
          העלו קורות חיים, בחרו קובץ, וצפו מיד במשרות שנשמרו עבורו. סריקות חדשות
          מתבצעות מתוך הדשבורד בלי למחוק תוצאות קודמות.
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
              void handleFiles(Array.from(e.target.files ?? []));
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
              ? analyzing
                ? "מנתח את קורות החיים…"
                : "הסוכן רץ — לא ניתן להעלות קבצים כרגע"
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

      {scanFinished && !scanStatus?.error && workspaceMatchCount >= 0 && hasResults && (
        <div className="fade-in-list">
          <ScanSummaryCards
            scraped={metrics.scraped}
            highMatches={metrics.highMatches}
            autoApplied={metrics.autoApplied}
          />
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
            title="מוחק את כל קבצי קורות החיים והתוצאות"
          >
            מחק את כל הקבצים
          </button>
        </div>
      )}

      {loading && cvs.length === 0 ? (
        <div className="empty-state compact" role="status">
          <p>טוען קבצים…</p>
        </div>
      ) : cvs.length === 0 ? (
        <div className="empty-state compact">
          <div className="empty-icon" aria-hidden>
            <span className="icon-bubble icon-bubble-blue">
              <Upload size={22} />
            </span>
          </div>
          <p>עדיין לא הועלו קבצים.</p>
          <p className="empty-hint">העלו קורות חיים כדי להתחיל סריקת משרות.</p>
        </div>
      ) : (
        <ul className="cv-list">
          {cvs.map((cv) => {
            const selected = selectedCvId === cv.id;
            return (
              <li key={cv.id} className={`cv-card ${selected ? "cv-card-selected" : ""}`}>
                <div className="cv-card-main">
                  <span className="icon-bubble icon-bubble-sm icon-bubble-blue" aria-hidden>
                    <FileText size={18} />
                  </span>
                  <div>
                    <div className="cv-name">{cv.display_name || cv.file_name}</div>
                    <div className="cv-meta">
                      הועלה {formatDate(cv.created_at)}
                      {cv.file_size != null && ` · ${formatSize(cv.file_size)}`}
                      {cv.last_scan_at && ` · נסרק ${formatDate(cv.last_scan_at)}`}
                    </div>
                    {cv.match_count != null && cv.match_count > 0 && (
                      <span className="badge">{cv.match_count} התאמות</span>
                    )}
                  </div>
                </div>
                <div className="cv-actions">
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    disabled={uiLocked}
                    onClick={() => onSelectCv(cv.id)}
                  >
                    {selected ? "נבחר" : "בחר"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={uiLocked || jobSitesLoading}
                    onClick={() => onNewScan(cv.id)}
                  >
                    סריקה חדשה
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={uiLocked}
                    onClick={() => setConfirmDelete(cv)}
                    aria-label="מחק"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {confirmDelete && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>למחוק את הקובץ?</h3>
            <p>
              פעולה זו תמחק את{" "}
              <strong>{confirmDelete.display_name || confirmDelete.file_name}</strong>{" "}
              ואת כל הנתונים הקשורים אליו.
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setConfirmDelete(null)}
              >
                ביטול
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={async () => {
                  const id = confirmDelete.id;
                  setConfirmDelete(null);
                  await onDelete(id);
                }}
              >
                מחק
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmReset && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>
              {confirmReset === "results"
                ? "לאפס את תוצאות החיפוש?"
                : "למחוק את כל הקבצים?"}
            </h3>
            <p>
              {confirmReset === "results"
                ? "ההתאמות ותוצאות הסריקה יימחקו. קבצי קורות החיים יישארו."
                : "כל קבצי קורות החיים והתוצאות יימחקו לצמיתות."}
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setConfirmReset(null)}
              >
                ביטול
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={async () => {
                  const kind = confirmReset;
                  setConfirmReset(null);
                  if (kind === "results") await onResetResults();
                  else await onResetFiles();
                }}
              >
                אישור
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
