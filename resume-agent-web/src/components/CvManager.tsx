import { useRef, useState, type DragEvent } from "react";
import { type Cv, type CvScanStatus } from "../lib/api";
import PipelineProgress from "./PipelineProgress";

const ACCEPTED = [".pdf", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp"];
const MAX_SIZE_MB = 15;

interface Props {
  cvs: Cv[];
  loading: boolean;
  error: string | null;
  scanCvId: string | null;
  scanStatus: CvScanStatus | null;
  onUpload: (files: File[]) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onRun: (id: string) => void;
  onOpen: (id: string) => void;
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
  scanCvId,
  scanStatus,
  onUpload,
  onDelete,
  onRun,
  onOpen,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Cv | null>(null);

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
    if (files.length === 0) return;
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
    handleFiles(Array.from(e.dataTransfer.files));
  };

  const isScanning = (id: string) =>
    scanCvId === id && (scanStatus?.running ?? false);
  const anyScanning = scanStatus?.running ?? false;

  return (
    <section>
      <section className="hero">
        <h1>
          קורות החיים שלך, <span className="accent">מרובי גרסאות</span>
        </h1>
        <p>
          העלה כמה קבצי קורות חיים. לכל אחד יש פרופיל, סריקות והתאמות נפרדות —
          הרצת הסוכן על קובץ אחד לא משפיעה על האחרים.
        </p>
      </section>

      <div className="upload-section">
        <div
          className={`dropzone ${dragging ? "dragging" : ""} ${busy ? "busy" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED.join(",")}
            multiple
            hidden
            onChange={(e) => {
              handleFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
          <div className="dropzone-icon">{busy ? "⏳" : "⬆️"}</div>
          <div className="dropzone-title">
            {busy ? "מעלה..." : "גרור לכאן קבצי קורות חיים או לחץ לבחירה"}
          </div>
          <div className="dropzone-hint">
            אפשר להעלות כמה קבצים · PDF / DOC / DOCX / TXT / תמונה · עד {MAX_SIZE_MB}MB
          </div>
        </div>

        {(localError || error) && (
          <div className="error-box">{localError || error}</div>
        )}
      </div>

      {anyScanning && scanStatus && (
        <PipelineProgress scanStatus={scanStatus} compact />
      )}

      <div className="history-header">
        <h2>קורות החיים שהועלו</h2>
        <span className="history-count">
          {loading ? "טוען..." : `${cvs.length} קבצים`}
        </span>
      </div>

      {cvs.length === 0 && !loading ? (
        <div className="empty-state">
          <div className="empty-icon">🗂️</div>
          <p>עדיין לא העלית קורות חיים.</p>
          <p className="empty-hint">
            העלה קובץ כדי להתחיל — לאחר מכן תוכל להריץ את הסוכן ולראות התאמות.
          </p>
        </div>
      ) : (
        <ul className="cv-list">
          {cvs.map((cv) => (
            <li key={cv.id} className="cv-item cv-manager-item">
              <div className="cv-icon">{fileIcon(cv.file_name)}</div>

              <div className="cv-info" onClick={() => onOpen(cv.id)}>
                <div className="cv-name">
                  {cv.display_name || cv.file_name}
                  {isScanning(cv.id) && <span className="badge badge-run">סורק…</span>}
                  {(cv.match_count ?? 0) > 0 && (
                    <span className="badge">{cv.match_count} התאמות</span>
                  )}
                </div>
                <div className="cv-meta">
                  הועלה {formatDate(cv.created_at)}
                  {cv.file_size != null && ` · ${formatSize(cv.file_size)}`}
                  {cv.last_scan_at && ` · סריקה אחרונה ${formatDate(cv.last_scan_at)}`}
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
                <button className="btn btn-ghost" onClick={() => onOpen(cv.id)}>
                  פרטים ותוצאות
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={anyScanning}
                  onClick={(e) => {
                    e.stopPropagation();
                    onRun(cv.id);
                  }}
                >
                  {isScanning(cv.id) ? "רץ…" : "▶ הרץ סוכן"}
                </button>
                <button
                  className="btn btn-ghost btn-delete"
                  disabled={isScanning(cv.id)}
                  onClick={() => setConfirmDelete(cv)}
                >
                  מחק
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {confirmDelete && (
        <div className="modal-overlay" onClick={() => setConfirmDelete(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>מחיקת קורות חיים</h3>
            <p>
              מחיקת "{confirmDelete.display_name || confirmDelete.file_name}" תמחק
              גם את כל הנתונים הקשורים: הפרופיל המנותח, היסטוריית הסריקות, תוצאות
              ההתאמה וסטטוסי ההגשה. לא ניתן לבטל פעולה זו.
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
    </section>
  );
}
