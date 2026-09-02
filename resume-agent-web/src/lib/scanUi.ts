import type { Cv } from "./api";

/** True when this CV already has saved scan history or matches. */
export function cvHasPriorScanResults(cv: Cv | null | undefined): boolean {
  if (!cv) return false;
  return (
    Boolean(cv.last_scan_at) ||
    (cv.match_count ?? 0) > 0 ||
    (cv.scan_count ?? 0) > 0
  );
}

/** Header / card CTA: first scan vs rescan. */
export function scanActionLabel(cv: Cv | null | undefined): string {
  return cvHasPriorScanResults(cv) ? "סריקה מחדש" : "סרוק עכשיו";
}

export function scanActionTitle(cv: Cv | null | undefined): string {
  return cvHasPriorScanResults(cv)
    ? "סריקה מחדש של משרות לפי קורות החיים הנבחרים"
    : "סרוק עכשיו משרות לפי קורות החיים הנבחרים";
}

export function scanEmptyHint(cv: Cv | null | undefined): string {
  const label = scanActionLabel(cv);
  return `לחצו על "${label}" בסרגל העליון כדי לאסוף ולדרג משרות עבור קורות החיים האלה.`;
}

export type PipelineStageId =
  | "parse"
  | "strategy"
  | "collect"
  | "enrich"
  | "match";

export type PipelineStageStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "skipped";

/**
 * During the inline collect→enrich→match pipeline, scored jobs stream while
 * the global backend step is still "collect". Once any fully-processed job
 * has appeared, treat enrich + match as actively running so the stepper
 * matches what users see in the live list.
 */
export function applyInlinePipelineStageHints<
  T extends { id: string; status: PipelineStageStatus },
>(
  stages: T[],
  opts: { running: boolean; liveMatchCount: number }
): T[] {
  if (!opts.running || opts.liveMatchCount <= 0) {
    return stages;
  }
  const collect = stages.find((s) => s.id === "collect");
  if (!collect || (collect.status !== "running" && collect.status !== "success")) {
    return stages;
  }
  return stages.map((stage) => {
    if (
      (stage.id === "enrich" || stage.id === "match") &&
      stage.status === "pending"
    ) {
      return { ...stage, status: "running" as const };
    }
    return stage;
  });
}
