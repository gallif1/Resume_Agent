import { useEffect, useMemo, useState } from "react";
import {
  Check,
  Circle,
  Loader2,
  Sparkles,
  Brain,
  FileSearch,
  Target,
  PenLine,
  ShieldCheck,
  Users,
  Briefcase,
  Wand2,
} from "lucide-react";
import type { GenerationReport, TailorDecision, TailorStageEvent } from "../lib/api";

export type AgentUiStatus = "pending" | "running" | "completed" | "failed";

export interface TailorAgentState {
  id: string;
  label: string;
  message: string;
  status: AgentUiStatus;
  progress: number;
}

const AGENT_CATALOG: Array<{
  id: string;
  label: string;
  idleMessage: string;
  icon: typeof Brain;
}> = [
  {
    id: "resume_knowledge",
    label: "סוכן ידע קורות החיים",
    idleMessage: "קורא את קורות החיים המקוריים…",
    icon: FileSearch,
  },
  {
    id: "job_intelligence",
    label: "סוכן ניתוח משרה",
    idleMessage: "מנתח את תיאור המשרה…",
    icon: Target,
  },
  {
    id: "company_intelligence",
    label: "סוכן מודיעין חברה",
    idleMessage: "מבין את הקשר הארגוני…",
    icon: Briefcase,
  },
  {
    id: "evidence_mapping",
    label: "סוכן מיפוי ראיות",
    idleMessage: "ממפה ראיות לדרישות המשרה…",
    icon: Brain,
  },
  {
    id: "resume_strategy",
    label: "סוכן אסטרטגיה",
    idleMessage: "בוחר את הראיות החזקות ביותר…",
    icon: Sparkles,
  },
  {
    id: "resume_tailoring",
    label: "סוכן התאמת תוכן",
    idleMessage: "בונה את הנרטיב המותאם…",
    icon: PenLine,
  },
  {
    id: "claim_validation",
    label: "סוכן אימות טענות",
    idleMessage: "מוודא שכל טענה נתמכת בראיות…",
    icon: ShieldCheck,
  },
  {
    id: "human_writer",
    label: "כותב קורות חיים בכיר",
    idleMessage: "מנסח ניסוח טבעי ומשכנע…",
    icon: PenLine,
  },
  {
    id: "senior_recruiter",
    label: "ביקורת מגייס בכיר",
    idleMessage: "בודק כמו מגייס עמוס (15 שניות)…",
    icon: Users,
  },
  {
    id: "hiring_manager",
    label: "סימולציית מנהל גיוס",
    idleMessage: "בוחן התאמה כמנהל גיוס…",
    icon: Briefcase,
  },
  {
    id: "final_polish",
    label: "גימור סופי",
    idleMessage: "מכין קורות חיים בעמוד אחד…",
    icon: Wand2,
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

interface Props {
  active: boolean;
  stages: TailorStageEvent[];
  decisions: TailorDecision[];
  statusMessage?: string | null;
  generationReport?: GenerationReport | null;
  compact?: boolean;
}

function initialAgents(): TailorAgentState[] {
  return AGENT_CATALOG.map((a) => ({
    id: a.id,
    label: a.label,
    message: a.idleMessage,
    status: "pending" as const,
    progress: 0,
  }));
}

export function applyStageEvent(
  agents: TailorAgentState[],
  event: TailorStageEvent
): TailorAgentState[] {
  const stage = event.stage;
  if (!stage || stage === "start") return agents;
  const idx = agents.findIndex((a) => a.id === stage);
  if (idx < 0) return agents;
  const next = agents.map((a) => ({ ...a }));
  // Mark prior stages completed if we jumped ahead
  for (let i = 0; i < idx; i++) {
    if (next[i].status !== "failed") {
      next[i] = {
        ...next[i],
        status: "completed",
        progress: 100,
      };
    }
  }
  const status = event.status;
  if (status === "started" || status === "running") {
    next[idx] = {
      ...next[idx],
      status: "running",
      message: event.message || next[idx].message,
      progress: Math.max(next[idx].progress, status === "running" ? 55 : 18),
    };
  } else if (status === "completed") {
    next[idx] = {
      ...next[idx],
      status: "completed",
      message: event.message || next[idx].message,
      progress: 100,
    };
  } else if (status === "failed") {
    next[idx] = {
      ...next[idx],
      status: "failed",
      message: event.message || "שגיאה",
      progress: next[idx].progress,
    };
  }
  return next;
}

export default function TailorGenerationProgress({
  active,
  stages,
  decisions,
  statusMessage,
  generationReport,
  compact = false,
}: Props) {
  const [agents, setAgents] = useState<TailorAgentState[]>(initialAgents);
  const [hintIndex, setHintIndex] = useState(0);
  const [pulseProgress, setPulseProgress] = useState(0);

  useEffect(() => {
    if (!active) {
      // Keep final state visible when report arrives
      return;
    }
    setAgents(initialAgents());
  }, [active]);

  useEffect(() => {
    let next = initialAgents();
    for (const stage of stages) {
      next = applyStageEvent(next, stage);
    }
    setAgents(next);
  }, [stages]);

  // Keep the UI alive while an agent runs
  useEffect(() => {
    if (!active) return;
    const hintTimer = window.setInterval(() => {
      setHintIndex((i) => (i + 1) % RUNNING_HINTS.length);
    }, 2800);
    const pulseTimer = window.setInterval(() => {
      setPulseProgress((p) => (p >= 92 ? 40 : p + 3));
      setAgents((prev) =>
        prev.map((a) =>
          a.status === "running"
            ? {
                ...a,
                progress: Math.min(92, a.progress + 2),
              }
            : a
        )
      );
    }, 700);
    return () => {
      window.clearInterval(hintTimer);
      window.clearInterval(pulseTimer);
    };
  }, [active]);

  const overall = useMemo(() => {
    const done = agents.filter((a) => a.status === "completed").length;
    const running = agents.some((a) => a.status === "running");
    const base = Math.round((done / agents.length) * 100);
    if (running) return Math.min(99, Math.max(base, pulseProgress));
    if (!active && generationReport) return 100;
    return base;
  }, [agents, active, pulseProgress, generationReport]);

  const current = agents.find((a) => a.status === "running");
  const recentDecisions = decisions.slice(-8).reverse();

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
        <p className="tailor-live-sub">
          {statusMessage ||
            current?.message ||
            (active ? RUNNING_HINTS[hintIndex] : "התהליך הושלם")}
        </p>
        <div className="tailor-live-bar" aria-hidden="true">
          <div
            className="tailor-live-bar-fill"
            style={{ width: `${overall}%` }}
          />
        </div>
        <div className="tailor-live-bar-meta">
          <span>{overall}%</span>
          <span>
            {agents.filter((a) => a.status === "completed").length}/
            {agents.length} סוכנים
          </span>
        </div>
      </div>

      <div className="tailor-live-grid">
        <ol className="tailor-live-timeline">
          {agents.map((agent) => {
            const meta = AGENT_CATALOG.find((a) => a.id === agent.id);
            const Icon = meta?.icon || Circle;
            return (
              <li
                key={agent.id}
                className={`tailor-agent-card tailor-agent-${agent.status}`}
              >
                <div className="tailor-agent-icon">
                  {agent.status === "completed" ? (
                    <Check size={16} />
                  ) : agent.status === "running" ? (
                    <Loader2 size={16} className="spin" />
                  ) : (
                    <Icon size={16} />
                  )}
                </div>
                <div className="tailor-agent-body">
                  <div className="tailor-agent-label">{agent.label}</div>
                  <div className="tailor-agent-message">{agent.message}</div>
                  <div className="tailor-agent-progress" aria-hidden="true">
                    <div
                      className="tailor-agent-progress-fill"
                      style={{ width: `${agent.progress}%` }}
                    />
                  </div>
                </div>
              </li>
            );
          })}
        </ol>

        <div className="tailor-live-side">
          <div className="tailor-decision-log">
            <h4>יומן החלטות AI</h4>
            {recentDecisions.length === 0 ? (
              <p className="tailor-decision-empty">
                {active
                  ? "מחכה להחלטות משמעותיות מהסוכנים…"
                  : "אין החלטות להצגה"}
              </p>
            ) : (
              <ul>
                {recentDecisions.map((d, i) => (
                  <li key={`${d.text}-${i}`}>
                    <span className="tailor-decision-check">✓</span>
                    <span>{d.text}</span>
                  </li>
                ))}
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
              {!!generationReport.sections_changed?.length && (
                <div className="tailor-sections-changed">
                  {generationReport.sections_changed.map((s) => (
                    <span key={s} className="tailor-chip">
                      {s}
                    </span>
                  ))}
                </div>
              )}
              {!!generationReport.top_interview_reasons?.length && (
                <div className="tailor-top-reasons">
                  <h5>3 הסיבות החזקות לראיון</h5>
                  <ol>
                    {generationReport.top_interview_reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
