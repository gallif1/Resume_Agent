import type { TailorStageEvent } from "./api";

export type AgentUiStatus = "pending" | "running" | "completed" | "failed";

export interface TailorAgentState {
  id: string;
  label: string;
  message: string;
  status: AgentUiStatus;
  progress: number;
  details?: string[];
  startedAt?: string | null;
  completedAt?: string | null;
}

/** Weighted stage progress — one source of truth for % and agent counts. */
export const STAGE_WEIGHTS: Record<string, number> = {
  resume_knowledge: 10,
  job_intelligence: 10,
  company_intelligence: 5,
  evidence_mapping: 15,
  resume_strategy: 10,
  resume_tailoring: 15,
  claim_validation: 10,
  human_writer: 10,
  senior_recruiter: 5,
  hiring_manager: 5,
  final_polish: 5,
};

export const STAGE_ORDER = Object.keys(STAGE_WEIGHTS);

export interface GenerationProgressSnapshot {
  agents: TailorAgentState[];
  completedCount: number;
  totalAgents: number;
  overallProgress: number;
  currentAgentId: string | null;
  currentMessage: string | null;
}

export function computeWeightedProgress(agents: TailorAgentState[]): number {
  let earned = 0;
  let total = 0;
  for (const agent of agents) {
    const weight = STAGE_WEIGHTS[agent.id] ?? 0;
    total += weight;
    if (agent.status === "completed") {
      earned += weight;
    } else if (agent.status === "running") {
      // Indeterminate within stage — credit a small fraction only.
      earned += weight * 0.35;
    } else if (agent.status === "failed") {
      earned += weight * 0.15;
    }
  }
  if (total <= 0) return 0;
  return Math.min(99, Math.round((earned / total) * 100));
}

export function buildProgressSnapshot(
  agents: TailorAgentState[],
  options?: { active?: boolean; complete?: boolean }
): GenerationProgressSnapshot {
  const completedCount = agents.filter((a) => a.status === "completed").length;
  const running = agents.find((a) => a.status === "running");
  let overall = computeWeightedProgress(agents);
  if (options?.complete || (!options?.active && completedCount === agents.length)) {
    overall = 100;
  }
  return {
    agents,
    completedCount,
    totalAgents: agents.length,
    overallProgress: overall,
    currentAgentId: running?.id ?? null,
    currentMessage: running?.message ?? null,
  };
}

/** Prefer Hebrew catalog messages over raw English SSE text when possible. */
const EN_TO_HE_HINTS: Array<[RegExp, string]> = [
  [/reading original resume/i, "קורא את קורות החיים המקוריים…"],
  [/analyz(ing|e).*job/i, "מנתח את תיאור המשרה…"],
  [/company/i, "מבין את הקשר הארגוני…"],
  [/mapping.*(evidence|resume)/i, "ממפה ראיות לדרישות המשרה…"],
  [/selecting.*evidence|strategy/i, "בוחר את הראיות החזקות ביותר…"],
  [/building.*tailor|tailor(ing)?/i, "בונה את הנרטיב המותאם…"],
  [/validat/i, "מוודא שכל טענה נתמכת בראיות…"],
  [/writing|persuasive|natural/i, "מנסח ניסוח טבעי ומשכנע…"],
  [/recruiter/i, "בודק כמו מגייס עמוס…"],
  [/hiring manager/i, "בוחן התאמה כמנהל גיוס…"],
  [/final|one-page|one page|polish/i, "מכין קורות חיים בעמוד אחד…"],
  [/interview probability/i, "מכין קורות חיים מוכנים לראיון…"],
  [/optimized for interview/i, "קורות החיים מוכנים — מותאמים להגדלת סיכוי לראיון"],
];

export function localizeAgentMessage(
  message: string | null | undefined,
  fallbackHe: string
): string {
  const text = (message || "").trim();
  if (!text) return fallbackHe;
  // Already Hebrew-dominant
  const hebrew = (text.match(/[\u0590-\u05FF]/g) || []).join("").length;
  const latin = (text.match(/[A-Za-z]/g) || []).join("").length;
  if (hebrew >= latin) return text;
  for (const [re, he] of EN_TO_HE_HINTS) {
    if (re.test(text)) return he;
  }
  return text;
}

export function applyStageEventToAgents(
  agents: TailorAgentState[],
  event: TailorStageEvent
): TailorAgentState[] {
  const stage = event.stage || event.agent_id;
  if (!stage || stage === "start") return agents;
  const idx = agents.findIndex((a) => a.id === stage);
  if (idx < 0) return agents;
  const next = agents.map((a) => ({ ...a }));
  for (let i = 0; i < idx; i++) {
    if (next[i].status !== "failed") {
      next[i] = { ...next[i], status: "completed", progress: 100 };
    }
  }
  const status = event.status;
  const localized = localizeAgentMessage(event.message, next[idx].message);
  if (status === "started" || status === "running") {
    next[idx] = {
      ...next[idx],
      status: "running",
      message: localized,
      // Indeterminate — do not invent precise substep %
      progress: 0,
    };
  } else if (status === "completed") {
    next[idx] = {
      ...next[idx],
      status: "completed",
      message: localized,
      progress: 100,
    };
  } else if (status === "failed" || status === "retrying") {
    next[idx] = {
      ...next[idx],
      status: (status === "retrying" ? "running" : "failed") as AgentUiStatus,
      message: localized || "שגיאה",
      progress: next[idx].progress,
    };
  }
  return next;
}
