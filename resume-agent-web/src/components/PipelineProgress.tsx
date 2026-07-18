import {
  Briefcase,
  Check,
  Circle,
  Loader2,
  Radar,
  Sparkles,
  Terminal,
  X,
} from "lucide-react";
import type { CollectionSummary, CvScanStatus, SiteCollectionSummary } from "../lib/api";

interface Props {
  scanStatus: CvScanStatus | null;
  matchCount?: number;
  compact?: boolean;
  showSkeletons?: boolean;
}

/** Map backend pipeline keys into 3 friendly visual stages. */
const VISUAL_STAGES = [
  {
    id: "analyze",
    label: "ניתוח קורות חיים ואסטרטגיה",
    keys: ["parse_cv", "parse_cvs", "aggregate", "analyze_roles"],
  },
  {
    id: "scrape",
    label: "סריקת לוחות דרושים",
    keys: ["collect", "enrich"],
  },
  {
    id: "match",
    label: "הרצת מודל AI לחישוב התאמה",
    keys: ["match"],
  },
] as const;

type StageStatus = "pending" | "running" | "success" | "failed" | "skipped";

function stageStatus(
  keys: readonly string[],
  steps: CvScanStatus["steps"]
): StageStatus {
  const relevant = steps.filter((s) => keys.includes(s.key));
  if (relevant.length === 0) {
    // Fall back: if backend uses unknown keys, derive from overall running state later.
    return "pending";
  }
  if (relevant.some((s) => s.status === "failed")) return "failed";
  if (relevant.some((s) => s.status === "running")) return "running";
  const done = relevant.every(
    (s) => s.status === "success" || s.status === "skipped"
  );
  if (done) return "success";
  if (relevant.some((s) => s.status === "success" || s.status === "skipped")) {
    return "running";
  }
  return "pending";
}

function totalScraped(collection: CollectionSummary | null | undefined): number {
  if (!collection) return 0;
  let total = 0;
  for (const site of ["drushim", "linkedin", "gotfriends"] as const) {
    const data = collection[site];
    if (data) total += data.raw ?? 0;
  }
  return total;
}

function formatLogLine(line: string): string {
  const trimmed = line.trim();
  if (!trimmed) return "";
  if (/^\[\d{1,2}:\d{2}/.test(trimmed)) return trimmed;
  const now = new Date();
  const ts = now.toLocaleTimeString("he-IL", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return `[${ts}] ${trimmed}`;
}

export default function PipelineProgress({
  scanStatus,
  matchCount = 0,
  compact = false,
  showSkeletons = true,
}: Props) {
  if (!scanStatus) {
    return null;
  }

  const steps = scanStatus.steps ?? [];
  const running = scanStatus.running ?? false;
  const detail = scanStatus.detail?.trim();
  const logLines = scanStatus.log ?? [];
  const warnings = scanStatus.warnings ?? [];
  const hasWarnings = warnings.length > 0;
  const hasCollectionDetails = Boolean(scanStatus.collection);
  const showPanel =
    running || Boolean(scanStatus.error) || hasWarnings || hasCollectionDetails;

  if (!showPanel) {
    return null;
  }

  const stages = VISUAL_STAGES.map((stage) => ({
    ...stage,
    status: stageStatus(stage.keys, steps),
  }));

  // If backend steps haven't arrived yet while running, mark first stage active.
  if (running && steps.length === 0) {
    stages[0].status = "running";
  } else if (running && stages.every((s) => s.status === "pending")) {
    stages[0].status = "running";
  }

  const scraped = totalScraped(scanStatus.collection);
  const liveFound = Math.max(matchCount, scraped > 0 && running ? 0 : matchCount);

  return (
    <div
      className={`scan-progress-panel ${compact ? "pipeline-panel-compact" : ""}`}
    >
      <div className="scan-progress-top">
        <h3 className="scan-progress-title">
          {running ? "הסוכן בסריקה חכמה…" : "הסריקה הושלמה"}
        </h3>
        {(running || liveFound > 0) && (
          <span className="live-counter-badge" role="status">
            <Briefcase size={15} strokeWidth={2.25} aria-hidden />
            {running
              ? `נמצאו ${liveFound} משרות מתאימות עד כה…`
              : `${liveFound} משרות מתאימות`}
          </span>
        )}
      </div>

      {running && detail && (
        <div className="pipeline-live">
          <span className="pipeline-live-label">מתבצע עכשיו</span>
          <p className="pipeline-live-detail">{detail}</p>
        </div>
      )}

      <div className="visual-stepper" role="list" aria-label="שלבי הסריקה">
        {stages.map((stage) => (
          <div
            key={stage.id}
            className={`visual-step ${stage.status}`}
            role="listitem"
          >
            <span className="visual-step-icon" aria-hidden="true">
              <StageIcon status={stage.status} />
            </span>
            <span className="visual-step-label">{stage.label}</span>
          </div>
        ))}
      </div>

      {running && (
        <div className="agent-console" aria-live="polite">
          <div className="agent-console-header">
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <Terminal size={14} aria-hidden />
              Live Agent Console
            </span>
            <span className="agent-console-dot" aria-hidden />
          </div>
          <pre className="agent-console-body">
            {logLines.length > 0 ? (
              logLines.map(formatLogLine).filter(Boolean).join("\n")
            ) : (
              <span className="agent-console-empty">
                ממתין לעדכונים מהסוכן…
                {detail ? `\n>> ${detail}` : ""}
              </span>
            )}
          </pre>
        </div>
      )}

      {scanStatus.error && <div className="error-box">{scanStatus.error}</div>}

      {hasWarnings && (
        <div className="warning-box">
          <div className="warning-box-title">שימו לב — בעיות באיסוף משרות</div>
          <ul className="warning-list">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {!running && scanStatus.collection && (
        <CollectionDetails summary={scanStatus.collection} />
      )}

      {!running && logLines.length > 0 && (
        <div className="pipeline-log-wrap">
          <div className="pipeline-log-label">יומן פעילות</div>
          <pre className="pipeline-log pipeline-log-visible" dir="ltr">
            {logLines.join("\n")}
          </pre>
        </div>
      )}

      {running && showSkeletons && (
        <div className="skeleton-grid" aria-hidden="true">
          <div className="skeleton-card" />
          <div className="skeleton-card" />
          <div className="skeleton-card" />
          <div className="skeleton-card" />
        </div>
      )}
    </div>
  );
}

function StageIcon({ status }: { status: StageStatus }) {
  if (status === "success" || status === "skipped") {
    return <Check size={20} strokeWidth={2.5} />;
  }
  if (status === "failed") {
    return <X size={20} strokeWidth={2.5} />;
  }
  if (status === "running") {
    return <Loader2 size={20} className="step-icon" style={{ animation: "spin 1.1s linear infinite" }} />;
  }
  if (status === "pending") {
    return <Circle size={18} strokeWidth={2} />;
  }
  return <Sparkles size={18} />;
}

function CollectionDetails({ summary }: { summary: CollectionSummary }) {
  const siteEntries: Array<[string, SiteCollectionSummary]> = [];
  for (const site of ["drushim", "linkedin", "gotfriends"] as const) {
    const data = summary[site];
    if (data) siteEntries.push([site, data]);
  }

  if (siteEntries.length === 0) {
    return null;
  }

  const labels: Record<string, string> = {
    drushim: "דרושים",
    linkedin: "לינקדאין",
    gotfriends: "GotFriends",
  };

  return (
    <div className="collection-summary">
      <div className="collection-summary-title">סיכום איסוף משרות</div>
      <div className="collection-summary-grid">
        {siteEntries.map(([site, data]) => (
          <div key={site} className="collection-summary-card">
            <div className="collection-summary-site">{labels[site] ?? site}</div>
            <div className="collection-summary-stats">
              <span>נמצאו: {data.raw}</span>
              <span>חדשות: {data.new}</span>
              <span>כבר במערכת: {data.already_in_db}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Exported for success-state metric widgets in CvManager. */
export function computeScanMetrics(
  scanStatus: CvScanStatus | null,
  matchCount: number
): {
  scraped: number;
  highMatches: number;
  autoApplied: number;
} {
  return {
    scraped: totalScraped(scanStatus?.collection),
    highMatches: matchCount,
    autoApplied: 0,
  };
}

export function ScanSummaryCards({
  scraped,
  highMatches,
  autoApplied,
}: {
  scraped: number;
  highMatches: number;
  autoApplied?: number;
}) {
  return (
    <div className="scan-summary-grid">
      <div className="scan-metric-card metric-accent">
        <span className="icon-bubble icon-bubble-sm icon-bubble-blue" aria-hidden>
          <Radar size={18} />
        </span>
        <div className="scan-metric-value">{scraped}</div>
        <div className="scan-metric-label">משרות שנסרקו מלוחות הדרושים</div>
      </div>
      <div className="scan-metric-card metric-success">
        <span className="icon-bubble icon-bubble-sm icon-bubble-green" aria-hidden>
          <Sparkles size={18} />
        </span>
        <div className="scan-metric-value">{highMatches}</div>
        <div className="scan-metric-label">התאמות גבוהות שנמצאו</div>
      </div>
      <div className="scan-metric-card">
        <span className="icon-bubble icon-bubble-sm icon-bubble-slate" aria-hidden>
          <Briefcase size={18} />
        </span>
        <div className="scan-metric-value">{autoApplied ?? 0}</div>
        <div className="scan-metric-label">הגשות אוטומטיות</div>
      </div>
    </div>
  );
}
