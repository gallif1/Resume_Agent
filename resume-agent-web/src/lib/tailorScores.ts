import type { GenerationReport, TailoredCvResponse } from "./api";

export type ScoreCalculationStatus =
  | "pending"
  | "calculating"
  | "complete"
  | "failed";

export interface ScoreBreakdown {
  original_score: number | null;
  tailored_score: number | null;
  score_delta: number | null;
  requirements_coverage?: number | null;
  ats_keyword_alignment?: number | null;
  evidence_strength?: number | null;
  role_relevance?: number | null;
  seniority_fit?: number | null;
  missing_required_requirements?: string[];
  missing_preferred_requirements?: string[];
  unsupported_requirements?: string[];
  improved_because?: string[];
  still_missing?: string[];
  calculation_status: ScoreCalculationStatus;
  score_version?: string | null;
  resume_version_id?: string | number | null;
}

/** Final tailored ATS/job-fit score — prefers fields computed from the final resume. */
export function getTailoredScore(result: TailoredCvResponse): number | null {
  if (typeof result.tailored_match_score === "number") {
    return result.tailored_match_score;
  }
  if (typeof result.score_after === "number") {
    return result.score_after;
  }
  if (typeof result.realistic_match_score === "number") {
    return result.realistic_match_score;
  }
  if (typeof result.estimated_ats_score === "number") {
    return result.estimated_ats_score;
  }
  const fromFeedback =
    result.matcher_feedback?.current?.match_score ??
    result.matcher_feedback?.current?.ats_score;
  return typeof fromFeedback === "number" ? fromFeedback : null;
}

/** Previous tailored score (regenerate progression). */
export function getPreviousTailoredScore(
  result: TailoredCvResponse,
  fallbackBaseline?: number | null
): number | null {
  if (typeof result.score_before === "number") {
    return result.score_before;
  }
  const fromFeedback =
    result.matcher_feedback?.previous?.match_score ??
    result.matcher_feedback?.previous?.ats_score;
  if (typeof fromFeedback === "number") {
    return fromFeedback;
  }
  return getOriginalMatchScore(result, fallbackBaseline);
}

/** Original resume ↔ job match score (never the tailored score). */
export function getOriginalMatchScore(
  result: TailoredCvResponse,
  fallbackBaseline?: number | null
): number | null {
  if (typeof result.original_match_score === "number") {
    return result.original_match_score;
  }
  if (typeof result.initial_match_score === "number") {
    return result.initial_match_score;
  }
  return fallbackBaseline ?? null;
}

export function formatScoreProgression(
  before: number | null,
  after: number | null,
  { original = false }: { original?: boolean } = {}
): string | null {
  if (after == null) return null;
  if (before == null) {
    return original
      ? `ציון ההתאמה למשרה: ${after}`
      : `ציון ההתאמה אחרי התאמה: ${after}`;
  }
  if (before === after) {
    return `ציון ההתאמה למשרה: ${after}`;
  }
  if (before < after) {
    return original
      ? `שיפרנו את ההתאמה למשרה מ־${before} ל־${after}`
      : `שיפרנו עוד את ההתאמה מ־${before} ל־${after}`;
  }
  return `ציון ההתאמה אחרי התאמה: ${after}`;
}

/**
 * Build the score lifecycle view-model.
 * During generation, tailored score stays null / calculating — never reuse the
 * original job-card score as if it were the tailored result.
 */
export function buildScoreBreakdown(options: {
  result?: TailoredCvResponse | null;
  generationReport?: GenerationReport | null;
  originalBaseline?: number | null;
  isGenerating?: boolean;
}): ScoreBreakdown {
  const { result, generationReport, originalBaseline, isGenerating } = options;
  const fromReport = generationReport?.score_breakdown;
  const fromResult = result?.score_breakdown;

  const original =
    (typeof fromReport?.original_score === "number"
      ? fromReport.original_score
      : null) ??
    (typeof fromResult?.original_score === "number"
      ? fromResult.original_score
      : null) ??
    (result ? getOriginalMatchScore(result, originalBaseline) : null) ??
    originalBaseline ??
    null;

  // While agents run, never present a prior/job-card score as the tailored score.
  if (isGenerating) {
    return {
      original_score: original,
      tailored_score: null,
      score_delta: null,
      calculation_status: "calculating",
      missing_required_requirements:
        fromReport?.missing_required_requirements ||
        fromResult?.missing_required_requirements ||
        [],
      still_missing:
        fromReport?.still_missing || fromResult?.still_missing || [],
    };
  }

  const tailored =
    (typeof fromReport?.tailored_score === "number"
      ? fromReport.tailored_score
      : null) ??
    (typeof fromResult?.tailored_score === "number"
      ? fromResult.tailored_score
      : null) ??
    (result ? getTailoredScore(result) : null);

  const rawStatus =
    fromReport?.calculation_status ||
    fromResult?.calculation_status ||
    (tailored != null ? "complete" : "pending");
  const status: ScoreCalculationStatus =
    rawStatus === "pending" ||
    rawStatus === "calculating" ||
    rawStatus === "complete" ||
    rawStatus === "failed"
      ? rawStatus
      : tailored != null
        ? "complete"
        : "pending";

  const delta =
    (typeof fromReport?.score_delta === "number"
      ? fromReport.score_delta
      : null) ??
    (typeof fromResult?.score_delta === "number"
      ? fromResult.score_delta
      : null) ??
    (original != null && tailored != null ? tailored - original : null);

  return {
    original_score: original,
    tailored_score: status === "complete" ? tailored : null,
    score_delta: status === "complete" ? delta : null,
    requirements_coverage:
      fromReport?.requirements_coverage ?? fromResult?.requirements_coverage,
    ats_keyword_alignment:
      fromReport?.ats_keyword_alignment ?? fromResult?.ats_keyword_alignment,
    evidence_strength:
      fromReport?.evidence_strength ?? fromResult?.evidence_strength,
    role_relevance: fromReport?.role_relevance ?? fromResult?.role_relevance,
    seniority_fit: fromReport?.seniority_fit ?? fromResult?.seniority_fit,
    missing_required_requirements:
      fromReport?.missing_required_requirements ||
      fromResult?.missing_required_requirements ||
      result?.missing_requirements ||
      [],
    missing_preferred_requirements:
      fromReport?.missing_preferred_requirements ||
      fromResult?.missing_preferred_requirements ||
      [],
    unsupported_requirements:
      fromReport?.unsupported_requirements ||
      fromResult?.unsupported_requirements ||
      [],
    improved_because:
      fromReport?.improved_because ||
      fromResult?.improved_because ||
      result?.top_interview_reasons ||
      generationReport?.top_interview_reasons ||
      [],
    still_missing:
      fromReport?.still_missing ||
      fromResult?.still_missing ||
      result?.missing_critical_skills ||
      result?.missing_requirements ||
      [],
    calculation_status: status,
    score_version: fromReport?.score_version ?? fromResult?.score_version,
    resume_version_id:
      fromReport?.resume_version_id ??
      fromResult?.resume_version_id ??
      result?.version_id ??
      null,
  };
}
