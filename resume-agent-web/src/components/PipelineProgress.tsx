import { useEffect, useState } from "react";
import {
  Briefcase,
  Check,
  Circle,
  Lightbulb,
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
}

/** Map backend pipeline keys into 3 friendly visual stages. */
const VISUAL_STAGES = [
  {
    id: "analyze",
    label: "ניתוח קורות חיים ואסטרטגיה",
    keys: ["parse_cv", "parse_cvs", "aggregate", "analyze_roles"],
    liveHint: "שלב 1: מנתח את קורות החיים ובונה אסטרטגיית חיפוש…",
  },
  {
    id: "scrape",
    label: "סריקת לוחות דרושים",
    keys: ["collect", "enrich"],
    liveHint: "שלב 2: מתחבר ללוחות דרושים ואוסף משרות…",
  },
  {
    id: "match",
    label: "הרצת מודל AI לחישוב התאמה",
    keys: ["match"],
    liveHint: "שלב 3: מחשב התאמה בין הכישורים למשרות…",
  },
] as const;

const CAREER_TIPS = [
  "טיפ: התאמת תקציר קורות החיים למשרה יכולה להעלות את שיעור התגובות בכ־40%.",
  "טיפ: הדגישו מספרי השפעה (\"שיפור של 30%\") במקום רק רשימת משימות.",
  "טיפ: השתמשו במילות מפתח מהמודעה — מערכות ATS מחפשות התאמה לשונית.",
  "טיפ: פתיח קצר וממוקד עדיף על פסקה כללית ארוכה.",
  "טיפ: התאימו את כותרת התפקיד בקורות החיים לשפה של המשרה המבוקשת.",
  "טיפ: הוסיפו קישור ללינקדאין או לתיק עבודות — זה מקצר את תהליך הסינון.",
  "טיפ: בדקו שפרטי הקשר מעודכנים ונראים גם במובייל.",
  "טיפ: למשרות ג'וניור — הדגישו פרויקטים, למידה עצמית והתנדבות רלוונטית.",
];

type StageStatus = "pending" | "running" | "success" | "failed" | "skipped";

function stageStatus(
  keys: readonly string[],
  steps: CvScanStatus["steps"]
): StageStatus {
  const relevant = steps.filter((s) => keys.includes(s.key));
  if (relevant.length === 0) {
    // Search-only runs omit analyze steps — treat missing stages as skipped.
    return steps.length > 0 ? "skipped" : "pending";
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

function progressFromStages(stages: Array<{ status: StageStatus }>): number {
  if (stages.length === 0) return 8;
  const weight = 100 / stages.length;
  let value = 0;
  for (const stage of stages) {
    if (stage.status === "success" || stage.status === "skipped") {
      value += weight;
    } else if (stage.status === "running") {
      value += weight * 0.55;
    } else if (stage.status === "failed") {
      value += weight * 0.35;
    }
  }
  return Math.max(6, Math.min(98, Math.round(value)));
}

export default function PipelineProgress({
  scanStatus,
  matchCount = 0,
  compact = false,
}: Props) {
  const [tipIndex, setTipIndex] = useState(0);

  const running = scanStatus?.running ?? false;

  useEffect(() => {
    if (!running) return;
    setTipIndex(0);
    const id = window.setInterval(() => {
      setTipIndex((prev) => (prev + 1) % CAREER_TIPS.length);
    }, 6000);
    return () => window.clearInterval(id);
  }, [running]);

  if (!scanStatus) {
    return null;
  }

  const steps = scanStatus.steps ?? [];
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
  const liveFound = Math.max(matchCount, 0);
  const activeStage = stages.find((s) => s.status === "running");
  const progress = running
    ? progressFromStages(stages)
    : scanStatus.error
      ? Math.max(12, progressFromStages(stages))
      : 100;

  const fallbackLines: string[] = [];
  if (detail) fallbackLines.push(detail);
  if (activeStage) fallbackLines.push(activeStage.liveHint);
  if (scraped > 0) {
    fallbackLines.push(`נאספו ${scraped} משרות מלוחות הדרושים עד כה`);
  }
  if (liveFound > 0) {
    fallbackLines.push(`זוהו ${liveFound} התאמות רלוונטיות עד כה`);
  }
  if (scanStatus.current_step) {
    fallbackLines.push(`שלב נוכחי: ${scanStatus.current_step}`);
  }

  const consoleLines =
    logLines.length > 0
      ? logLines.map(formatLogLine).filter(Boolean)
      : fallbackLines.map(formatLogLine).filter(Boolean);

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

      {running && (
        <div
          className="scan-progress-bar-wrap"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
          aria-label="התקדמות הסריקה"
        >
          <div className="scan-progress-bar-meta">
            <span>{activeStage?.label ?? "מתחיל סריקה…"}</span>
            <span>{progress}%</span>
          </div>
          <div className="scan-progress-bar-track">
            <div
              className="scan-progress-bar-fill"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

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
            <span className="agent-console-title">
              <Terminal size={14} aria-hidden />
              יומן התקדמות חי
            </span>
            <span className="agent-console-dot" aria-hidden />
          </div>
          <pre className="agent-console-body">
            {consoleLines.length > 0 ? (
              consoleLines.join("\n")
            ) : (
              <span className="agent-console-empty">
                שלב 1: מתחבר ללוחות דרושים…
              </span>
            )}
          </pre>
        </div>
      )}

      {running && (
        <div className="career-tip-card" key={tipIndex} role="status">
          <span className="career-tip-icon" aria-hidden>
            <Lightbulb size={18} />
          </span>
          <div className="career-tip-body">
            <span className="career-tip-label">טיפ קריירה בזמן הסריקה</span>
            <p className="career-tip-text">{CAREER_TIPS[tipIndex]}</p>
          </div>
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
    return (
      <Loader2
        size={20}
        className="step-icon"
        style={{ animation: "spin 1.1s linear infinite" }}
      />
    );
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
    <div className="scan-summary-grid" dir="rtl">
      <div className="scan-metric-card metric-slate">
        <span className="icon-bubble icon-bubble-sm icon-bubble-slate" aria-hidden>
          <Radar size={18} />
        </span>
        <div className="scan-metric-value">{scraped}</div>
        <div className="scan-metric-label">משרות שנסרקו ברשת</div>
        <div className="scan-metric-subtitle">
          סך הכל משרות שנמצאו בלוחות השונים
        </div>
      </div>
      <div className="scan-metric-card metric-success metric-success-soft">
        <span className="icon-bubble icon-bubble-sm icon-bubble-green" aria-hidden>
          <Sparkles size={18} />
        </span>
        <div className="scan-metric-value">{highMatches}</div>
        <div className="scan-metric-label">התאמות רלוונטיות</div>
        <div className="scan-metric-subtitle">
          משרות עם ציון התאמה מעל 60%
        </div>
      </div>
      <div className="scan-metric-card">
        <span className="icon-bubble icon-bubble-sm icon-bubble-slate" aria-hidden>
          <Briefcase size={18} />
        </span>
        <div className="scan-metric-value">{autoApplied ?? 0}</div>
        <div className="scan-metric-label">הגשות אוטומטיות</div>
        <div className="scan-metric-subtitle">הגשות שבוצעו אוטומטית</div>
      </div>
    </div>
  );
}
