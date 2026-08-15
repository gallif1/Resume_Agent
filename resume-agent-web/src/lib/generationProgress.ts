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
  substeps?: string[];
}

/** Weighted stage progress — single smart resume agent. */
export const STAGE_WEIGHTS: Record<string, number> = {
  smart_resume_agent: 100,
};

export const STAGE_ORDER = Object.keys(STAGE_WEIGHTS);

/** Map legacy / four-agent SSE ids onto the single UI agent. */
export const LEGACY_STAGE_TO_MERGED: Record<string, string> = {
  resume_knowledge: "smart_resume_agent",
  job_intelligence: "smart_resume_agent",
  company_intelligence: "smart_resume_agent",
  evidence_mapping: "smart_resume_agent",
  resume_strategy: "smart_resume_agent",
  resume_tailoring: "smart_resume_agent",
  claim_validation: "smart_resume_agent",
  human_writer: "smart_resume_agent",
  human_resume_writer: "smart_resume_agent",
  senior_recruiter: "smart_resume_agent",
  senior_recruiter_review: "smart_resume_agent",
  hiring_manager: "smart_resume_agent",
  hiring_manager_simulation: "smart_resume_agent",
  final_polish: "smart_resume_agent",
  candidate_opportunity_intelligence: "smart_resume_agent",
  strategy_content_selection: "smart_resume_agent",
  human_writing_credibility: "smart_resume_agent",
  final_hiring_ats_page: "smart_resume_agent",
  smart_resume_agent: "smart_resume_agent",
};

export function resolveMergedStage(stage: string | null | undefined): string | null {
  if (!stage || stage === "start") return null;
  return LEGACY_STAGE_TO_MERGED[stage] || stage;
}

export interface GenerationProgressSnapshot {
  agents: TailorAgentState[];
  completedCount: number;
  totalAgents: number;
  overallProgress: number;
  currentAgentId: string | null;
  currentMessage: string | null;
  stageOfLabel: string | null;
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
  const runningIndex = running ? agents.findIndex((a) => a.id === running.id) : -1;
  return {
    agents,
    completedCount,
    totalAgents: agents.length,
    overallProgress: overall,
    currentAgentId: running?.id ?? null,
    currentMessage: running?.message ?? null,
    stageOfLabel:
      runningIndex >= 0 ? `Stage ${runningIndex + 1} of ${agents.length}` : null,
  };
}

/**
 * When a tailor POST finishes but SSE stages never arrived (cache hit / race),
 * treat every agent as completed so the summary never shows a fake 0/1.
 */
export function markAgentsCompletedIfIdle(
  agents: TailorAgentState[],
  options: { active: boolean; complete: boolean }
): TailorAgentState[] {
  if (options.active || !options.complete) return agents;
  const anyProgress = agents.some(
    (a) => a.status === "completed" || a.status === "running" || a.status === "failed"
  );
  if (anyProgress) return agents;
  return agents.map((a) => ({
    ...a,
    status: "completed" as const,
    progress: 100,
  }));
}

/** Prefer Hebrew catalog messages over raw English SSE text when possible. */
const EN_TO_HE_HINTS: Array<[RegExp, string]> = [
  [/reading candidate|candidate profile|original resume/i, "קורא את פרופיל המועמד…"],
  [/analyz(ing|e).*job|job requirements|opportunity/i, "מנתח את דרישות המשרה…"],
  [/company/i, "בודק הקשר ארגוני…"],
  [/mapping.*(evidence|resume)|evidence|strategy/i, "ממפה ראיות ובונה אסטרטגיה…"],
  [/validat|writing|polish|credibility|natural/i, "כותב ומאמת את קורות החיים…"],
  [/recruiter|ats|one-page|one page|final|hiring/i, "ביקורת סופית — מגייס, ATS ועמוד אחד…"],
  [/crafting|tailored resume|smart/i, "בונה את קורות החיים המותאמים…"],
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
  const rawStage = event.stage || event.agent_id;
  const stage = resolveMergedStage(rawStage);
  if (!stage) return agents;
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
  const details = [...(next[idx].details || [])];
  if (localized && !details.includes(localized)) {
    details.push(localized);
  }
  if (status === "started" || status === "running") {
    next[idx] = {
      ...next[idx],
      status: "running",
      message: localized,
      details: details.slice(-6),
      // Indeterminate — do not invent precise substep %
      progress: 0,
    };
  } else if (status === "completed") {
    next[idx] = {
      ...next[idx],
      status: "completed",
      message: localized,
      details: details.slice(-6),
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

/** True while a tailor POST or regenerate is in flight. */
export function isTailorGenerating(options: {
  regenerating: boolean;
  tailoringId: number | null;
}): boolean {
  return options.regenerating || options.tailoringId != null;
}

/**
 * Draft to show in the live/result UI. Hide a saved draft from another job
 * while a new tailor session is in flight for `tailoringId`.
 */
export function resolveActiveTailoredCv<T extends { job_id: number }>(
  tailoredCv: T | null,
  tailoringId: number | null
): T | null {
  if (tailoredCv == null) return null;
  if (tailoringId != null && tailoredCv.job_id !== tailoringId) return null;
  return tailoredCv;
}
