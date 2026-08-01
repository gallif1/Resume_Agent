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

/** Weighted stage progress — prepare (code) → one LLM → final (code). */
export const STAGE_WEIGHTS: Record<string, number> = {
  prepare_evidence: 25,
  resume_generation_agent: 55,
  final_hiring_ats_page: 20,
};

export const STAGE_ORDER = Object.keys(STAGE_WEIGHTS);

/** Map legacy / four-agent SSE ids onto the current UI stages. */
export const LEGACY_STAGE_TO_MERGED: Record<string, string> = {
  resume_knowledge: "prepare_evidence",
  job_intelligence: "prepare_evidence",
  company_intelligence: "prepare_evidence",
  evidence_mapping: "prepare_evidence",
  resume_strategy: "prepare_evidence",
  candidate_opportunity_intelligence: "prepare_evidence",
  strategy_content_selection: "prepare_evidence",
  prepare_evidence: "prepare_evidence",
  resume_tailoring: "resume_generation_agent",
  resume_generation_agent: "resume_generation_agent",
  claim_validation: "resume_generation_agent",
  human_writer: "resume_generation_agent",
  human_resume_writer: "resume_generation_agent",
  senior_recruiter: "resume_generation_agent",
  senior_recruiter_review: "resume_generation_agent",
  human_writing_credibility: "resume_generation_agent",
  hiring_manager: "final_hiring_ats_page",
  hiring_manager_simulation: "final_hiring_ats_page",
  final_polish: "final_hiring_ats_page",
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
  [/reading candidate|candidate profile|original resume|parsing resume/i, "קורא את פרופיל המועמד…"],
  [/analyz(ing|e).*job|job requirements|parsing job/i, "מנתח את דרישות המשרה…"],
  [/company|normaliz/i, "מנרמל עובדות…"],
  [/mapping.*(evidence|resume)|evidence|preparing candidate/i, "ממפה ראיות לדרישות…"],
  [/opportunity intelligence|experience and the opportunity/i, "מכין ראיות מועמד ומשרה…"],
  [/selecting.*evidence|strategy|building the best resume|prepar/i, "מכין ראיות ואסטרטגיה…"],
  [/building.*tailor|tailor(ing)?|structure|generating your tailored|one intelligent/i, "מייצר את קורות החיים המותאמים…"],
  [/validat/i, "מאמת טענות מול ראיות…"],
  [/writing and validating|writing|persuasive|natural/i, "כותב ניסוח טבעי…"],
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
