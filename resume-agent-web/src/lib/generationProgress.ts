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

/** Weighted stage progress — four merged agents. */
export const STAGE_WEIGHTS: Record<string, number> = {
  candidate_opportunity_intelligence: 30,
  strategy_content_selection: 25,
  human_writing_credibility: 30,
  final_hiring_ats_page: 15,
};

export const STAGE_ORDER = Object.keys(STAGE_WEIGHTS);

/** Map legacy 11-agent SSE ids onto the four UI stages. */
export const LEGACY_STAGE_TO_MERGED: Record<string, string> = {
  resume_knowledge: "candidate_opportunity_intelligence",
  job_intelligence: "candidate_opportunity_intelligence",
  company_intelligence: "candidate_opportunity_intelligence",
  evidence_mapping: "candidate_opportunity_intelligence",
  resume_strategy: "strategy_content_selection",
  resume_tailoring: "strategy_content_selection",
  claim_validation: "human_writing_credibility",
  human_writer: "human_writing_credibility",
  human_resume_writer: "human_writing_credibility",
  senior_recruiter: "human_writing_credibility",
  senior_recruiter_review: "human_writing_credibility",
  hiring_manager: "final_hiring_ats_page",
  hiring_manager_simulation: "final_hiring_ats_page",
  final_polish: "final_hiring_ats_page",
  candidate_opportunity_intelligence: "candidate_opportunity_intelligence",
  strategy_content_selection: "strategy_content_selection",
  human_writing_credibility: "human_writing_credibility",
  final_hiring_ats_page: "final_hiring_ats_page",
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

/** Prefer Hebrew catalog messages over raw English SSE text when possible. */
const EN_TO_HE_HINTS: Array<[RegExp, string]> = [
  [/reading candidate|candidate profile|original resume/i, "קורא את פרופיל המועמד…"],
  [/analyz(ing|e).*job|job requirements/i, "מנתח את דרישות המשרה…"],
  [/company/i, "בודק הקשר ארגוני…"],
  [/mapping.*(evidence|resume)|evidence/i, "ממפה ראיות לדרישות…"],
  [/opportunity intelligence|experience and the opportunity/i, "מנתח ניסיון והזדמנות…"],
  [/selecting.*evidence|strategy|building the best resume/i, "בונה אסטרטגיית קורות חיים…"],
  [/building.*tailor|tailor(ing)?|structure/i, "בונה את מבנה קורות החיים…"],
  [/validat/i, "מאמת טענות מול ראיות…"],
  [/writing and validating|writing|persuasive|natural/i, "כותב ומאמת את קורות החיים…"],
  [/recruiter|ats|one-page|one page|final|hiring/i, "ביקורת סופית — מגייס, ATS ועמוד אחד…"],
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
