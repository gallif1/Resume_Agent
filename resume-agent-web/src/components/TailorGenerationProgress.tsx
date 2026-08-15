import { useEffect, useId, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  Circle,
  Loader2,
  Sparkles,
  AlertTriangle,
  Info,
} from "lucide-react";
import type { GenerationReport, TailorDecision, TailorStageEvent } from "../lib/api";
import {
  applyStageEventToAgents,
  buildProgressSnapshot,
  localizeAgentMessage,
  markAgentsCompletedIfIdle,
  type AgentUiStatus,
  type TailorAgentState,
} from "../lib/generationProgress";
import {
  buildScoreBreakdown,
  type ScoreBreakdown,
} from "../lib/tailorScores";

export type { AgentUiStatus, TailorAgentState };

const AGENT_CATALOG: Array<{
  id: string;
  label: string;
  idleMessage: string;
  icon: typeof Sparkles;
  substeps: string[];
}> = [
  {
    id: "smart_resume_agent",
    label: "בונה את קורות החיים המותאמים",
    idleMessage: "מנתח ראיות וכותב קורות חיים מוכנים למגייסים…",
    icon: Sparkles,
    substeps: [
      "קורא פרופיל ומשרה",
      "ממפה ראיות ואסטרטגיה",
      "כותב ומאמת",
      "ביקורת ATS ועמוד אחד",
    ],
  },
];

const RUNNING_HINTS = [
  "מחפש כישורים ניתנים להעברה…",
  "ממפה ראיות מהניסיון…",
  "בוחר את הפרויקטים החזקים ביותר…",
  "משפר את התקציר…",
  "בודק תאימות ATS…",
  "סוקר כמגייס…",
  "סוקר כמנהל גיוס…",
  "משפר סעיפים חלשים…",
  "מכין את ה-PDF הסופי…",
];

const STATUS_LABEL_HE: Record<AgentUiStatus, string> = {
  pending: "ממתין",
  running: "פועל",
  completed: "הושלם",
  failed: "נכשל",
};

export function applyStageEvent(
  agents: TailorAgentState[],
  event: TailorStageEvent
): TailorAgentState[] {
  return applyStageEventToAgents(agents, event);
}

export function initialAgents(): TailorAgentState[] {
  return AGENT_CATALOG.map((a) => ({
    id: a.id,
    label: a.label,
    message: a.idleMessage,
    status: "pending" as const,
    progress: 0,
    details: [],
    substeps: a.substeps,
  }));
}

function decisionTone(text: string, action?: string): "info" | "positive" | "missing" | "revision" | "warning" {
  const t = `${action || ""} ${text}`.toLowerCase();
  if (/warn|⚠|unsupported|could not|fail/.test(t)) return "warning";
  if (/missing|not added|gap|חסר/.test(t)) return "missing";
  if (/revis|refine|compress|reduced|שינ/.test(t)) return "revision";
  if (/found|strong|highlight|✓|match|emphas/.test(t)) return "positive";
  return "info";
}

function ToneIcon({ tone }: { tone: ReturnType<typeof decisionTone> }) {
  if (tone === "warning" || tone === "missing") {
    return <AlertTriangle size={14} aria-hidden="true" />;
  }
  if (tone === "positive") return <Check size={14} aria-hidden="true" />;
  return <Info size={14} aria-hidden="true" />;
}

function ShowMoreText({
  text,
  maxChars = 220,
}: {
  text: string;
  maxChars?: number;
}) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  const needsClamp = text.length > maxChars;
  const shown = !needsClamp || open ? text : `${text.slice(0, maxChars).trim()}…`;
  return (
    <div className="tailor-show-more" dir="auto">
      <p>{shown}</p>
      {needsClamp && (
        <button
          type="button"
          className="btn-link-touch"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "הצג פחות" : "הצג עוד"}
        </button>
      )}
    </div>
  );
}

function ScoreLifecyclePanel({
  score,
  active,
}: {
  score: ScoreBreakdown;
  active: boolean;
}) {
  const original = score.original_score;
  const tailored = score.tailored_score;
  const delta = score.score_delta;
  const calculating =
    active ||
    score.calculation_status === "calculating" ||
    score.calculation_status === "pending";

  return (
    <div className="tailor-score-lifecycle" aria-live="polite">
      <div className="tailor-score-row">
        <span className="tailor-score-label">התאמת קורות חיים מקוריים</span>
        <strong className="tailor-score-value">
          {original != null ? `${original}%` : "—"}
        </strong>
      </div>
      <div className="tailor-score-row">
        <span className="tailor-score-label">התאמת קורות חיים מותאמים</span>
        <strong className="tailor-score-value">
          {calculating && tailored == null
            ? "מחשב אחרי אימות סופי…"
            : tailored != null
              ? `${tailored}%`
              : score.calculation_status === "failed"
                ? "חישוב נכשל"
                : "—"}
        </strong>
      </div>
      {!calculating && delta != null && (
        <div className="tailor-score-row tailor-score-delta">
          <span className="tailor-score-label">שיפור</span>
          <strong>
            {delta > 0 ? `+${delta}` : `${delta}`}
          </strong>
        </div>
      )}
      {calculating && (
        <p className="tailor-score-note">
          הציון הסופי יחושב רק אחרי אימות הטענות, ביקורת מגייס, מנהל גיוס ואימות
          ATS/עמוד אחד — לא מוצג ציון מקורי כאילו הוא של הגרסה המותאמת.
        </p>
      )}
      {!calculating && !!(score.improved_because?.length) && (
        <div className="tailor-score-reasons">
          <h5>שופר כי</h5>
          <ul>
            {score.improved_because!.slice(0, 4).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {!calculating && !!(score.still_missing?.length) && (
        <div className="tailor-score-reasons tailor-score-missing">
          <h5>עדיין חסר</h5>
          <ul>
            {score.still_missing!.slice(0, 6).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function AgentCard({
  agent,
  expanded,
  onToggle,
}: {
  agent: TailorAgentState;
  expanded: boolean;
  onToggle: () => void;
}) {
  const meta = AGENT_CATALOG.find((a) => a.id === agent.id);
  const Icon = meta?.icon || Circle;
  const panelId = useId();
  const indeterminate = agent.status === "running";

  return (
    <li className={`tailor-agent-card tailor-agent-${agent.status}`}>
      <button
        type="button"
        className="tailor-agent-header"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={onToggle}
      >
        <div className="tailor-agent-icon" aria-hidden="true">
          {agent.status === "completed" ? (
            <Check size={16} />
          ) : agent.status === "running" ? (
            <Loader2 size={16} className="spin" />
          ) : agent.status === "failed" ? (
            <AlertTriangle size={16} />
          ) : (
            <Icon size={16} />
          )}
        </div>
        <div className="tailor-agent-body">
          <div className="tailor-agent-label-row">
            <span className="tailor-agent-label">{agent.label}</span>
            <span className={`tailor-agent-status tailor-agent-status-${agent.status}`}>
              {STATUS_LABEL_HE[agent.status]}
            </span>
          </div>
          <div className="tailor-agent-message" dir="auto">
            {agent.message}
          </div>
          <div
            className={`tailor-agent-progress ${indeterminate ? "tailor-agent-progress-indeterminate" : ""}`}
            aria-hidden="true"
          >
            <div
              className="tailor-agent-progress-fill"
              style={
                indeterminate
                  ? undefined
                  : { width: `${agent.progress}%` }
              }
            />
          </div>
        </div>
        <span className="tailor-agent-expand" aria-hidden="true">
          <ChevronDown
            size={18}
            className={expanded ? "tailor-chevron-open" : ""}
          />
        </span>
      </button>
      <div
        id={panelId}
        className={`tailor-agent-details ${expanded ? "is-open" : ""}`}
        hidden={!expanded}
      >
        <ShowMoreText text={agent.message} maxChars={160} />
        {!!agent.details?.length && (
          <ul className="tailor-agent-detail-list">
            {agent.details.map((d, i) => (
              <li key={`${agent.id}-d-${i}`} dir="auto">
                {d}
              </li>
            ))}
          </ul>
        )}
        <p className="tailor-agent-meta">
          משימה נוכחית: {STATUS_LABEL_HE[agent.status]}
          {agent.status === "running" && " · התקדמות בתוך השלב אינה מדויקת באחוזים"}
        </p>
      </div>
    </li>
  );
}

interface Props {
  active: boolean;
  stages: TailorStageEvent[];
  decisions: TailorDecision[];
  statusMessage?: string | null;
  generationReport?: GenerationReport | null;
  fromCache?: boolean;
  compact?: boolean;
  originalBaseline?: number | null;
  scoreBreakdown?: ScoreBreakdown | null;
  showCompletion?: boolean;
  onPreview?: () => void;
  onViewPdf?: () => void;
  onViewScoreBreakdown?: () => void;
  onClose?: () => void;
  pdfBusy?: boolean;
}

export default function TailorGenerationProgress({
  active,
  stages,
  decisions,
  statusMessage,
  generationReport,
  fromCache = false,
  compact = false,
  originalBaseline = null,
  scoreBreakdown = null,
  showCompletion = false,
  onPreview,
  onViewPdf,
  onViewScoreBreakdown,
  onClose,
  pdfBusy = false,
}: Props) {
  const [agents, setAgents] = useState<TailorAgentState[]>(initialAgents);
  const [hintIndex, setHintIndex] = useState(0);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [decisionsOpen, setDecisionsOpen] = useState(false);

  useEffect(() => {
    if (!active) return;
    setAgents(initialAgents());
    setExpandedIds(new Set());
  }, [active]);

  useEffect(() => {
    let next = initialAgents();
    for (const stage of stages) {
      next = applyStageEvent(next, stage);
    }
    // Attach recent decisions as details per agent
    const byStage = new Map<string, string[]>();
    for (const d of decisions) {
      const key = d.stage || "";
      if (!key) continue;
      const list = byStage.get(key) || [];
      list.push(d.text);
      byStage.set(key, list);
    }
    next = next.map((a) => ({
      ...a,
      details: byStage.get(a.id)?.slice(-6) || [],
    }));
    next = markAgentsCompletedIfIdle(next, {
      active,
      complete: showCompletion,
    });
    setAgents(next);
  }, [stages, decisions, active, showCompletion]);

  useEffect(() => {
    if (!active) return;
    const hintTimer = window.setInterval(() => {
      setHintIndex((i) => (i + 1) % RUNNING_HINTS.length);
    }, 2800);
    return () => window.clearInterval(hintTimer);
  }, [active]);

  const loadedFromCache =
    fromCache || Boolean(generationReport?.from_cache);

  const snapshot = useMemo(
    () =>
      buildProgressSnapshot(agents, {
        active,
        complete: !active && (showCompletion || !!generationReport),
      }),
    [agents, active, generationReport, showCompletion]
  );

  const score =
    scoreBreakdown ||
    buildScoreBreakdown({
      generationReport,
      originalBaseline,
      isGenerating: active,
    });

  const current = agents.find((a) => a.status === "running");
  const recentDecisions = decisions.slice(-3).reverse();
  const allDecisions = [...decisions].reverse();

  const localizedStatus = localizeAgentMessage(
    statusMessage,
    current?.message || (active ? RUNNING_HINTS[hintIndex] : "התהליך הושלם")
  );

  // Parent decides when completion UI is ready (report and/or result present).
  const completionReady = showCompletion && !active;

  if (completionReady) {
    const totalAgents =
      generationReport?.agents_total ?? snapshot.totalAgents;
    const completedAgents =
      generationReport?.agents_completed ?? snapshot.completedCount;
    const creationTimeLabel = loadedFromCache
      ? "נטען מהשמור"
      : generationReport?.generation_time_seconds != null
        ? `${generationReport.generation_time_seconds} שניות`
        : "—";

    return (
      <div
        className={`tailor-live tailor-live-complete ${compact ? "tailor-live-compact" : ""}`}
        role="status"
        aria-live="polite"
      >
        <div className="tailor-complete-hero">
          <Sparkles size={22} aria-hidden="true" />
          <h3>
            {loadedFromCache
              ? "נטענו קורות חיים שמורים"
              : "קורות החיים מוכנים"}
          </h3>
        </div>
        <ScoreLifecyclePanel score={score} active={false} />
        <ul className="tailor-complete-stats">
          <li>
            <span>שלבים שהושלמו</span>
            <strong>
              {snapshot.stageOfLabel ||
                `${completedAgents}/${totalAgents}`}
            </strong>
          </li>
          <li>
            <span>תיקונים</span>
            <strong>{generationReport?.resume_revisions ?? "—"}</strong>
          </li>
          <li>
            <span>זמן יצירה</span>
            <strong>{creationTimeLabel}</strong>
          </li>
        </ul>
        {!!generationReport?.top_interview_reasons?.length && (
          <div className="tailor-top-reasons">
            <h5>סיבות חזקות לראיון</h5>
            <ol>
              {generationReport.top_interview_reasons.map((r) => (
                <li key={r} dir="auto">
                  {r}
                </li>
              ))}
            </ol>
          </div>
        )}
        <div className="tailor-complete-actions">
          {onViewPdf && (
            <button
              type="button"
              className="btn btn-primary touch-target"
              onClick={onViewPdf}
              disabled={pdfBusy}
            >
              {pdfBusy ? "מכין PDF..." : "הצג PDF"}
            </button>
          )}
          {onPreview && (
            <button type="button" className="btn btn-ghost touch-target" onClick={onPreview}>
              תצוגה מקדימה
            </button>
          )}
          {onViewScoreBreakdown && (
            <button
              type="button"
              className="btn btn-ghost touch-target"
              onClick={onViewScoreBreakdown}
            >
              פירוט ציון
            </button>
          )}
          {onClose && (
            <button type="button" className="btn btn-ghost touch-target" onClick={onClose}>
              סגור
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`tailor-live ${compact ? "tailor-live-compact" : ""}`}
      role="status"
      aria-live="polite"
    >
      <div className="tailor-live-header">
        <div className="tailor-live-title-row">
          <Sparkles size={18} aria-hidden="true" />
          <h3 className="tailor-live-title">
            {active
              ? "צוות ה-AI עובד על קורות החיים שלך"
              : "סיכום יצירת קורות החיים"}
          </h3>
        </div>
        <p className="tailor-live-sub" dir="auto">
          {localizedStatus}
        </p>
        <div
          className="tailor-live-bar"
          role="progressbar"
          aria-valuenow={snapshot.overallProgress}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="התקדמות כוללת"
        >
          <div
            className="tailor-live-bar-fill"
            style={{ width: `${snapshot.overallProgress}%` }}
          />
        </div>
        <div className="tailor-live-bar-meta">
          <span>{snapshot.overallProgress}%</span>
          <span>
            {snapshot.stageOfLabel ||
              `שלב ${Math.min(snapshot.completedCount + 1, snapshot.totalAgents)} מתוך ${snapshot.totalAgents}`}
          </span>
        </div>
      </div>

      <ScoreLifecyclePanel score={score} active={active} />

      <div className="tailor-live-grid">
        <ol className="tailor-live-timeline">
          {agents.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              expanded={expandedIds.has(agent.id)}
              onToggle={() =>
                setExpandedIds((prev) => {
                  const next = new Set(prev);
                  if (next.has(agent.id)) next.delete(agent.id);
                  else next.add(agent.id);
                  return next;
                })
              }
            />
          ))}
        </ol>

        <div className="tailor-live-side">
          <div className="tailor-decision-log">
            <div className="tailor-decision-log-header">
              <h4>יומן החלטות AI</h4>
              {decisions.length > 3 && (
                <button
                  type="button"
                  className="btn-link-touch"
                  onClick={() => setDecisionsOpen((v) => !v)}
                  aria-expanded={decisionsOpen}
                >
                  {decisionsOpen ? "הצג פחות" : "הצג את כל ההחלטות"}
                </button>
              )}
            </div>
            {(decisionsOpen ? allDecisions : recentDecisions).length === 0 ? (
              <p className="tailor-decision-empty">
                {active
                  ? "מחכה להחלטות משמעותיות מהשלבים…"
                  : "אין החלטות להצגה"}
              </p>
            ) : (
              <ul className={decisionsOpen ? "tailor-decision-all" : undefined}>
                {(decisionsOpen ? allDecisions : recentDecisions).map((d, i) => {
                  const tone = decisionTone(d.text, d.action);
                  return (
                    <li
                      key={`${d.text}-${i}`}
                      className={`tailor-decision-item tailor-decision-${tone}`}
                    >
                      <span className="tailor-decision-check" aria-hidden="true">
                        <ToneIcon tone={tone} />
                      </span>
                      <span dir="auto">{d.text}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {generationReport && !active && (
            <div className="tailor-generation-report">
              <h4>דוח יצירה</h4>
              <ul>
                <li>
                  <strong>דרישות שנותחו:</strong>{" "}
                  {generationReport.job_requirements_analyzed ?? "—"}
                </li>
                <li>
                  <strong>חוזקות שזוהו:</strong>{" "}
                  {generationReport.candidate_strengths_identified ?? "—"}
                </li>
                <li>
                  <strong>כישורים מועברים:</strong>{" "}
                  {generationReport.transferable_skills_inferred ?? "—"}
                </li>
                <li>
                  <strong>שינויים בקורות החיים:</strong>{" "}
                  {generationReport.resume_revisions ?? "—"}
                </li>
                <li>
                  <strong>אופטימיזציית ATS:</strong>{" "}
                  {generationReport.ats_optimization_completed ? "✓" : "—"}
                </li>
                <li>
                  <strong>ביקורת מגייס:</strong>{" "}
                  {generationReport.recruiter_review_completed ? "✓" : "—"}
                </li>
                <li>
                  <strong>ביקורת מנהל גיוס:</strong>{" "}
                  {generationReport.hiring_manager_review_completed ? "✓" : "—"}
                </li>
                {generationReport.generation_time_seconds != null && (
                  <li>
                    <strong>זמן יצירה:</strong>{" "}
                    {generationReport.generation_time_seconds} שניות
                  </li>
                )}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
