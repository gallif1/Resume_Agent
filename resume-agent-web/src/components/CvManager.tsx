import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import {
  Briefcase,
  Check,
  FileText,
  Globe,
  Loader2,
  Plus,
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

type FlowStep = "config" | "analyzing" | "domains";

interface Props {
  cvs: Cv[];
  loading: boolean;
  error: string | null;
  scanStatus: CvScanStatus | null;
  workspaceMatchCount: number;
  jobSites?: JobSite[];
  jobSitesLoading?: boolean;
  stopping?: boolean;
  analyzing?: boolean;
  suggestedDomains?: string[];
  candidateSummary?: string;
  onUpload: (files: File[]) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onAnalyze: (cvId: string) => Promise<void>;
  onStartSearch: (cvId: string, domains: string[], siteIds: string[]) => void;
  onStopAgent: () => void;
  onOpenMatches: (cvId: string) => void;
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
  analyzing = false,
  suggestedDomains = [],
  candidateSummary = "",
  onUpload,
  onDelete,
  onAnalyze,
  onStartSearch,
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
  const [selectedDomains, setSelectedDomains] = useState<string[]>([]);
  const [customDomain, setCustomDomain] = useState("");
  const [flowStep, setFlowStep] = useState<FlowStep>("config");

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

  useEffect(() => {
    if (analyzing) {
      setFlowStep("analyzing");
      return;
    }
    if (!anyScanning && suggestedDomains.length > 0) {
      setFlowStep("domains");
      setSelectedDomains([...suggestedDomains]);
    } else if (!anyScanning && !analyzing && flowStep === "analyzing") {
      // Analysis finished with no suggestions — still allow custom domains.
      setFlowStep("domains");
    }
  }, [analyzing, suggestedDomains, anyScanning]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (anyScanning) {
      setFlowStep("config");
    }
  }, [anyScanning]);

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

  const toggleDomain = (domain: string) => {
    setSelectedDomains((prev) =>
      prev.includes(domain)
        ? prev.filter((d) => d !== domain)
        : [...prev, domain]
    );
  };

  const addCustomDomain = (e?: FormEvent) => {
    e?.preventDefault();
    const value = customDomain.trim();
    if (!value) return;
    setSelectedDomains((prev) =>
      prev.some((d) => d.toLowerCase() === value.toLowerCase())
        ? prev
        : [...prev, value]
    );
    setCustomDomain("");
  };

  const handleAnalyzeClick = async () => {
    if (!selectedCvId || selectedSites.length === 0) return;
    setLocalError(null);
    setFlowStep("analyzing");
    try {
      await onAnalyze(selectedCvId);
    } catch (err) {
      setFlowStep("config");
      setLocalError(err instanceof Error ? err.message : "ניתוח קורות החיים נכשל");
    }
  };

  const handleStartSearch = () => {
    if (!selectedCvId || selectedDomains.length === 0 || selectedSites.length === 0) {
      return;
    }
    onStartSearch(selectedCvId, selectedDomains, selectedSites);
  };

  const metrics = computeScanMetrics(scanStatus, workspaceMatchCount);
  const showDomainPicker = flowStep === "domains" && !anyScanning && !analyzing;

  return (
    <section className={uiLocked ? "scan-locked-section" : undefined}>
      <section className="hero">
        <h1>
          קורות החיים שלך, <span className="accent">חיפוש משרות ממוקד</span>
        </h1>
        <p>
          העלה קורות חיים — הסוכן יציע תחומים רלוונטיים, תבחרו מה לחפש, ואז
          נאסוף משרות חדשות בלי למחוק תוצאות קודמות.
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

      {/* Step 1 — CV + sites, then analyze */}
      {cvs.length > 0 && !anyScanning && flowStep !== "domains" && !analyzing && (
        <div className="scan-config">
          <div className="scan-config-header">
            <span className="icon-bubble icon-bubble-blue" aria-hidden>
              <Rocket size={22} />
            </span>
            <div>
              <h2>שלב 1 — ניתוח קורות חיים</h2>
              <p>
                בחרו קובץ ואתרי דרושים, ואז ננתח את קורות החיים ונציע תחומי
                חיפוש רלוונטיים.
              </p>
            </div>
          </div>

          <div>
            <span className="scan-config-label">קורות חיים לסריקה</span>
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
                    onClick={() => {
                      setSelectedCvId(cv.id);
                      setFlowStep("config");
                    }}
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
            disabled={
              loading ||
              selectedSites.length === 0 ||
              !selectedCvId ||
              analyzing
            }
            onClick={handleAnalyzeClick}
          >
            <Rocket size={20} aria-hidden />
            נתח קורות חיים והצע תחומים
          </button>
        </div>
      )}

      {/* Step 1 loading */}
      {analyzing && (
        <div className="scan-config domain-analyzing">
          <div className="domain-analyzing-inner">
            <Loader2 className="domain-analyzing-spinner" size={28} aria-hidden />
            <div>
              <h2>מנתח קורות חיים ומחלץ תחומים…</h2>
              <p>זה עשוי לקחת כמה רגעים — מזהים תפקידים ותחומים רלוונטיים מהקובץ.</p>
            </div>
          </div>
        </div>
      )}

      {/* Step 2 — domain multi-select */}
      {showDomainPicker && (
        <div className="scan-config">
          <div className="scan-config-header">
            <span className="icon-bubble icon-bubble-blue" aria-hidden>
              <Briefcase size={22} />
            </span>
            <div>
              <h2>שלב 2 — בחירת תחומי חיפוש</h2>
              <p>
                סמנו תחומים מוצעים או הוסיפו תחום משלכם. חיפוש חדש יוסיף משרות
                בלי למחוק תוצאות קודמות.
              </p>
            </div>
          </div>

          {candidateSummary && (
            <p className="domain-summary">{candidateSummary}</p>
          )}

          <div>
            <span className="scan-config-label">תחומים מומלצים</span>
            <div className="domain-chip-grid" role="group" aria-label="בחירת תחומים">
              {(suggestedDomains.length > 0
                ? suggestedDomains
                : selectedDomains
              ).map((domain) => {
                const selected = selectedDomains.includes(domain);
                return (
                  <button
                    key={domain}
                    type="button"
                    className={`domain-chip ${selected ? "selected" : ""}`}
                    onClick={() => toggleDomain(domain)}
                    aria-pressed={selected}
                  >
                    {selected && <Check size={14} strokeWidth={3} aria-hidden />}
                    {domain}
                  </button>
                );
              })}
              {selectedDomains
                .filter((d) => !suggestedDomains.includes(d))
                .map((domain) => (
                  <button
                    key={`custom-${domain}`}
                    type="button"
                    className="domain-chip selected"
                    onClick={() => toggleDomain(domain)}
                    aria-pressed
                  >
                    <Check size={14} strokeWidth={3} aria-hidden />
                    {domain}
                  </button>
                ))}
            </div>
          </div>

          <form className="domain-custom-row" onSubmit={addCustomDomain}>
            <input
              type="text"
              className="domain-custom-input"
              placeholder="הוסיפו תחום מותאם אישית (למשל Fullstack Developer)"
              value={customDomain}
              onChange={(e) => setCustomDomain(e.target.value)}
              dir="auto"
            />
            <button
              type="submit"
              className="btn btn-secondary"
              disabled={!customDomain.trim()}
            >
              <Plus size={16} aria-hidden />
              הוסף
            </button>
          </form>

          {selectedDomains.length === 0 && (
            <div className="error-box">יש לבחור לפחות תחום אחד.</div>
          )}

          <div className="domain-actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setFlowStep("config");
                setSelectedDomains([]);
              }}
            >
              חזרה
            </button>
            <button
              type="button"
              className="btn btn-scan-cta"
              disabled={
                selectedDomains.length === 0 ||
                selectedSites.length === 0 ||
                !selectedCvId
              }
              onClick={handleStartSearch}
            >
              <Rocket size={20} aria-hidden />
              התחל חיפוש משרות
            </button>
          </div>
        </div>
      )}

      {/* Progress State */}
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

      {scanFinished && !scanStatus?.error && workspaceMatchCount >= 0 && hasResults && (
        <div className="fade-in-list">
          <ScanSummaryCards
            scraped={metrics.scraped}
            highMatches={metrics.highMatches}
            autoApplied={metrics.autoApplied}
          />
        </div>
      )}

      {workspaceMatchCount > 0 && !anyScanning && selectedCvId && (
        <div className="workspace-matches-banner fade-in-list">
          <div>
            <strong>{workspaceMatchCount} התאמות משרה</strong>
            <span className="workspace-matches-sub">
              כולל תוצאות מכל הסריקות הקודמות לקורות החיים האלה
            </span>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={uiLocked}
            onClick={() => onOpenMatches(selectedCvId)}
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
            title="מוחק את כל קבצי קורות החיים והתוצאות"
          >
            מחק את כל הקבצים
          </button>
        </div>
      )}

      {cvs.length === 0 && !loading ? (
        <div className="empty-state compact">
          <p>עדיין לא הועלו קבצים.</p>
        </div>
      ) : (
        <ul className="cv-list">
          {cvs.map((cv) => (
            <li key={cv.id} className="cv-card">
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
                {(cv.match_count ?? 0) > 0 && (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    disabled={uiLocked}
                    onClick={() => onOpenMatches(cv.id)}
                  >
                    תוצאות
                  </button>
                )}
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
          ))}
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
                  setFlowStep("config");
                  setSelectedDomains([]);
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
