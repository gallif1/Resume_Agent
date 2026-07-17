import { useRef, useState, type DragEvent } from "react";
import { type Cv, type CvScanStatus } from "../lib/api";
import PipelineProgress from "./PipelineProgress";

const ACCEPTED = [".pdf", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"];
const MAX_SIZE_MB = 15;

interface Props {
  cvs: Cv[];
  loading: boolean;
  error: string | null;
  scanStatus: CvScanStatus | null;
  workspaceMatchCount: number;
  stopping?: boolean;
  onUpload: (files: File[]) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onRunAgent: () => void;
  onStopAgent: () => void;
  onOpenMatches: () => void;
  onResetResults: () => Promise<void>;
  onResetFiles: () => Promise<void>;
}

function fileIcon(name: string | null): string {
  const lower = (name ?? "").toLowerCase();
  if (lower.endsWith(".pdf")) return "📕";
  if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg"))
    return "🖼️";
  return "📘";
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
  const anyScanning = scanStatus?.running ?? false;
  const uiLocked = anyScanning;
  const hasResults =
    workspaceMatchCount > 0 ||
    Boolean(scanStatus?.error) ||
    (scanStatus?.steps?.length ?? 0) > 0;

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
          <div className="dropzone-icon">{busy ? "⏳" : "⬆️"}</div>
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

      {cvs.length > 0 && (
        <div className="run-agent-section">
          {anyScanning ? (
            <button
              type="button"
              className="btn btn-danger btn-run-agent"
              disabled={stopping}
              onClick={onStopAgent}
            >
              {stopping ? "עוצר…" : "עצור סריקה"}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary btn-run-agent"
              disabled={loading}
              onClick={onRunAgent}
            >
              הפעל סוכן מציאת משרות
            </button>
          )}
          <p className="run-agent-hint">
            {anyScanning
              ? "הסוכן רץ ברקע — אפשר לרענן את הדף, הסריקה תמשיך. אפשר לעצור בכל רגע."
              : `הסוכן ינתח את כל ${cvs.length} הקבצים שהועלו, יאחד אותם לפרופיל מאוחד ויחפש משרות מתאימות.`}
          </p>
        </div>
      )}

      {(anyScanning || scanStatus?.error || (scanStatus?.steps?.length ?? 0) > 0) &&
        scanStatus && <PipelineProgress scanStatus={scanStatus} compact />}

      {workspaceMatchCount > 0 && !anyScanning && (
        <div className="workspace-matches-banner">
          <div>
            <strong>{workspaceMatchCount} התאמות משרה</strong>
            <span className="workspace-matches-sub">
              מבוסס על הפרופיל המאוחד של כל קורות החיים
            </span>
          </div>
          <button
            type="button"
            className="btn btn-ghost"
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
          <div className="empty-icon">🗂️</div>
          <p>עדיין לא העלית קורות חיים.</p>
          <p className="empty-hint">
            העלה קובץ אחד או יותר — לאחר מכן לחץ על "הפעל סוכן מציאת משרות".
          </p>
        </div>
      ) : (
        <ul className={`cv-list ${uiLocked ? "cv-list-locked" : ""}`}>
          {cvs.map((cv) => (
            <li key={cv.id} className="cv-item cv-manager-item">
              <div className="cv-icon">{fileIcon(cv.file_name)}</div>

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
