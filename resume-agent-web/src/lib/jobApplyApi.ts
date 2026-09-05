/** Client for the standalone job-apply automation (/api/job-apply). */

export type JobApplyResult = {
  success: boolean;
  status: string;
  message: string;
  provider?: string;
  job_url?: string;
  final_url?: string | null;
  filled_fields?: string[];
  skipped_fields?: string[];
  confirmation_text?: string | null;
  screenshot_path?: string | null;
  failure_category?: string | null;
};

export type JobApplyPayload = {
  jobUrl: string;
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  cv: File;
  dryRun?: boolean;
  /** When true, open a visible Chromium window so you can watch Playwright live. */
  showBrowser?: boolean;
};

export async function submitJobApplication(
  payload: JobApplyPayload
): Promise<JobApplyResult> {
  const body = new FormData();
  body.append("job_url", payload.jobUrl.trim());
  body.append("first_name", payload.firstName.trim());
  body.append("last_name", payload.lastName.trim());
  body.append("email", payload.email.trim());
  body.append("phone", payload.phone.trim());
  body.append("cv", payload.cv, payload.cv.name);
  body.append("dry_run", payload.dryRun ? "true" : "false");
  // headless=false → live visible browser
  body.append("headless", payload.showBrowser === false ? "true" : "false");

  const res = await fetch("/api/job-apply/apply", {
    method: "POST",
    body,
  });

  let data: JobApplyResult;
  try {
    data = (await res.json()) as JobApplyResult;
  } catch {
    throw new Error(
      res.ok
        ? "השרת החזיר תשובה לא תקינה"
        : `שגיאת שרת (${res.status}) — ודא שמודול ההגשה האוטומטית זמין`
    );
  }

  if (!res.ok && !data?.message) {
    throw new Error(`ההגשה נכשלה (HTTP ${res.status})`);
  }
  if (!res.ok && data?.message) {
    // Keep body message, but make HTTP status visible for debugging (e.g. 405/422).
    data.message = `${data.message} (HTTP ${res.status})`;
  }

  return data;
}
