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
