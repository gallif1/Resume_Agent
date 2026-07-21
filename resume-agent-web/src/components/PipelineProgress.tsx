import { useEffect, useState } from "react";
import {
  Briefcase,
  Check,
  Circle,
  FileSearch,
  Lightbulb,
  Loader2,
  Radar,
  Search,
  Sparkles,
  Target,
  BarChart3,
  X,
} from "lucide-react";
import type { CollectionSummary, CvScanStatus, SiteCollectionSummary } from "../lib/api";

interface Props {
  scanStatus: CvScanStatus | null;
  matchCount?: number;
  compact?: boolean;
}

/**
 * Five user-facing stages mapped from backend pipeline keys.
 * Interactive search runs may omit parse/analyze (done in the modal) —
 * those stages are then treated as already completed.
 */
const VISUAL_STAGES = [
  {
    id: "parse",
    emoji: "📄",
    title: "שלב 1 (קריאת קורות חיים)",
    label: "קריאת קורות חיים",
    keys: ["parse_cv", "parse_cvs", "aggregate"],
    message: "מנתח את קורות החיים והניסיון שלך...",
  },
  {
    id: "strategy",
    emoji: "🎯",
    title: "שלב 2 (גיבוש אסטרטגיה)",
    label: "גיבוש אסטרטגיה",
    keys: ["analyze_roles"],
    message: "מגדיר תחומי חיפוש ומילות מפתח מתאימות...",
  },
  {
    id: "collect",
    emoji: "🔎",
    title: "שלב 3 (איסוף משרות)",
    label: "איסוף משרות",
    keys: ["collect"],
    message: "מחפש משרות בלוחות הדרושים (דרושים, LinkedIn, GotFriends)...",
  },
  {
    id: "enrich",
    emoji: "💼",
    title: "שלב 4 (העשרת מידע)",
    label: "העשרת מידע",
    keys: ["enrich"],
    message: "מעשיר את התיאור המלא של כל משרה...",
  },
  {
    id: "match",
    emoji: "📊",
    title: "שלב 5 (חישוב התאמה)",
    label: "חישוב התאמה",
    keys: ["match"],
    message: "מחשב אחוזי התאמה וציון ATS עבור כל משרה...",
  },
] as const;

const EARLY_STAGE_IDS = new Set(["parse", "strategy"]);

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

function totalNew(collection: CollectionSummary | null | undefined): number {
  if (!collection) return 0;
  let total = 0;
  for (const site of ["drushim", "linkedin", "gotfriends"] as const) {
    const data = collection[site];
    if (data) total += data.new ?? 0;
  }
  return total;
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

function isSearchOnlyRun(steps: CvScanStatus["steps"]): boolean {
  if (steps.length === 0) return false;
  const hasEarly = steps.some((s) =>
    ["parse_cv", "parse_cvs", "aggregate", "analyze_roles"].includes(s.key)
  );
  const hasSearch = steps.some((s) =>
    ["collect", "enrich", "match"].includes(s.key)
  );
  return !hasEarly && hasSearch;
}

function friendlyLiveMessage(opts: {
  running: boolean;
  error: string | null;
  activeStage: (typeof VISUAL_STAGES)[number] | undefined;
  scraped: number;
  newJobs: number;
  matchCount: number;
}): string {
  const { running, error, activeStage, scraped, newJobs, matchCount } = opts;
  if (error) return error;
  if (!running) return "הסריקה הושלמה בהצלחה!";

  const jobsCount = scraped > 0 ? scraped : newJobs;

  if (!activeStage) {
    return "מתחיל סריקה...";
  }

  if (activeStage.id === "collect" && jobsCount > 0) {
    return `נמצאו ${jobsCount} משרות חדשות, עכשיו בודק מה מתוכן הכי מתאים לך...`;
  }

  if (activeStage.id === "enrich") {
    return jobsCount > 0
      ? `נמצאו ${jobsCount} משרות! כעת מעשיר את התיאור המלא של כל משרה...`
      : activeStage.message;
  }

  if (activeStage.id === "match") {
    if (matchCount > 0) {
      return `מחשב אחוזי התאמה וציון ATS — זוהו כבר ${matchCount} התאמות רלוונטיות...`;
    }
    return jobsCount > 0
      ? `מחשב אחוזי התאמה וציון ATS עבור ${jobsCount} משרות...`
      : activeStage.message;
  }

  return activeStage.message;
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
  const warnings = scanStatus.warnings ?? [];
  const hasWarnings = warnings.length > 0;
  const hasCollectionDetails = Boolean(scanStatus.collection);
  const showPanel =
    running || Boolean(scanStatus.error) || hasWarnings || hasCollectionDetails;

  if (!showPanel) {
    return null;
  }

  const searchOnly = isSearchOnlyRun(steps);

  const stages = VISUAL_STAGES.map((stage) => {
    let status = stageStatus(stage.keys, steps);
    // Search-only runs already finished CV analysis in the modal.
    if (searchOnly && EARLY_STAGE_IDS.has(stage.id) && status === "pending") {
      status = "success";
    }
    return { ...stage, status };
  });

  // If backend steps haven't arrived yet while running, mark first relevant stage active.
  if (running && steps.length === 0) {
    const startIdx = 0;
    stages[startIdx].status = "running";
  } else if (running && stages.every((s) => s.status === "pending" || s.status === "success")) {
    const next = stages.find((s) => s.status === "pending");
    if (next) next.status = "running";
  }

  const scraped = totalScraped(scanStatus.collection);
  const newJobs = totalNew(scanStatus.collection);
  const liveFound = Math.max(matchCount, 0);
  const activeStage = stages.find((s) => s.status === "running");
  const activeDef = activeStage
    ? VISUAL_STAGES.find((s) => s.id === activeStage.id)
    : undefined;
  const progress = running
    ? progressFromStages(stages)
    : scanStatus.error
      ? Math.max(12, progressFromStages(stages))
      : 100;

  const liveMessage = friendlyLiveMessage({
    running,
    error: scanStatus.error,
    activeStage: activeDef,
    scraped,
    newJobs,
    matchCount: liveFound,
  });

  return (
    <div
      className={`scan-progress-panel ${compact ? "pipeline-panel-compact" : ""}`}
      dir="rtl"
    >
      <div className="scan-progress-top">
        <h3 className="scan-progress-title">
          {running
            ? "הסוכן בסריקה חכמה…"
            : scanStatus.error
              ? "הסריקה נעצרה"
              : "הסריקה הושלמה בהצלחה!"}
        </h3>
        {(running || liveFound > 0) && (
          <span className="live-counter-badge" role="status">
            <Briefcase size={15} strokeWidth={2.25} aria-hidden />
            {running
              ? scraped > 0
                ? `נמצאו ${scraped} משרות עד כה…`
                : liveFound > 0
                  ? `זוהו ${liveFound} התאמות עד כה…`
                  : "מחפש משרות…"
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
            <span>{activeStage?.title ?? "מתחיל סריקה…"}</span>
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

      <div className="scan-friendly-status" role="status" aria-live="polite">
        <span className="scan-friendly-status-emoji" aria-hidden>
          {running
            ? activeStage?.emoji ?? "✅"
            : scanStatus.error
              ? "⚠️"
              : "✅"}
        </span>
        <p className="scan-friendly-status-text" key={liveMessage}>
          {liveMessage}
        </p>
      </div>

      <div className="visual-stepper visual-stepper-five" role="list" aria-label="שלבי הסריקה">
        {stages.map((stage) => (
          <div
            key={stage.id}
            className={`visual-step ${stage.status}`}
            role="listitem"
          >
            <span className="visual-step-icon" aria-hidden="true">
              <StageIcon status={stage.status} stageId={stage.id} />
            </span>
            <span className="visual-step-label">
              <span className="visual-step-emoji" aria-hidden>
                {stage.emoji}
              </span>
              {stage.label}
            </span>
          </div>
        ))}
      </div>

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
    </div>
  );
}

function StageIcon({
  status,
  stageId,
}: {
  status: StageStatus;
  stageId: string;
}) {
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
    switch (stageId) {
      case "parse":
        return <FileSearch size={18} strokeWidth={2} />;
      case "strategy":
        return <Target size={18} strokeWidth={2} />;
      case "collect":
        return <Search size={18} strokeWidth={2} />;
      case "enrich":
        return <Briefcase size={18} strokeWidth={2} />;
      case "match":
        return <BarChart3 size={18} strokeWidth={2} />;
      default:
        return <Circle size={18} strokeWidth={2} />;
    }
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
