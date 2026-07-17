// HTTP client for the ai-job-agent backend (FastAPI, separate repository).

// In dev, Vite proxies /api and /cvs to the FastAPI server (see vite.config.ts).
const BASE_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  (import.meta.env.DEV ? "" : "http://127.0.0.1:8000");

function fetchWithTimeout(
  url: string,
  init: RequestInit = {},
  timeoutMs = 5000
): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...init, signal: controller.signal }).finally(() =>
    window.clearTimeout(timer)
  );
}

export interface PipelineStep {
  key: string;
  name: string;
  status: "pending" | "running" | "success" | "failed" | "skipped";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, init);
  if (!res.ok) {
    let detail = `שגיאה ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* keep generic message */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetchWithTimeout(`${BASE_URL}/api/health`, {}, 15000);
    return res.ok;
  } catch {
    return false;
  }
}

export function uploadCvToServer(
  file: File
): Promise<{ saved: boolean; name: string }> {
  const form = new FormData();
  form.append("file", file);
  return request(`/api/cv`, { method: "POST", body: form });
}

// ---------------------------------------------------------------------------
// Multi-CV API
// ---------------------------------------------------------------------------

export type ApplicationStatus =
  | "not_sent"
  | "sent"
  | "interested"
  | "not_relevant"
  | "applied_manually";

export interface CvProfileSummary {
  name: string | null;
  seniority: string | null;
  best_fit_roles: string[];
  skills_count: number | null;
}

export interface Cv {
  id: string;
  file_name: string | null;
  display_name: string | null;
  file_ext: string | null;
  file_size: number | null;
  created_at: string | null;
  updated_at: string | null;
  last_scan_at: string | null;
  match_count: number | null;
  scan_count: number | null;
  profile: CvProfileSummary | null;
}

export interface CvScan {
  id: number;
  cv_id: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  summary: string | null;
  error_message: string | null;
}

export interface SiteCollectionSummary {
  raw: number;
  new: number;
  already_in_db: number;
  excluded: number;
  queries: number;
  queries_with_raw: number;
  issues: string[];
}

export interface CollectionSummary {
  warnings?: string[];
  drushim?: SiteCollectionSummary;
  linkedin?: SiteCollectionSummary;
  gotfriends?: SiteCollectionSummary;
}

export type JobApplicationStatus =
  | "pending"
  | "in_progress"
  | "submitted"
  | "failed"
  | "requires_user_action";

export interface JobApplicationStep {
  id: number;
  application_id: string;
  step_name: string;
  status: string;
  message: string | null;
  created_at: string | null;
}

export interface JobApplication {
  application_id: string;
  cv_id: string;
  job_id: number;
  status: JobApplicationStatus;
  application_url: string | null;
  started_at: string | null;
  completed_at: string | null;
  submitted_at: string | null;
  failure_reason: string | null;
  failure_category: string | null;
  requires_user_action_reason: string | null;
  external_confirmation_text: string | null;
  external_confirmation_url: string | null;
  attempt_number: number | null;
  provider_name: string | null;
  current_step_url: string | null;
  created_at: string | null;
  updated_at: string | null;
  steps?: JobApplicationStep[];
  active?: boolean;
}

export interface CvMatch {
  match_id: number;
  job_id: number;
  scan_id: number | null;
  title: string | null;
  company: string | null;
  location: string | null;
  job_url: string | null;
  source: string | null;
  match_score: number | null;
  match_reason: string | null;
  explanation: string | null;
  matched_skills: string[];
  missing_skills: string[];
  score_label: string | null;
  missing_mandatory: string[];
  relevant_experience: string[];
  score_reasons: string[];
  cv_improvements: string[];
  is_potential_junior_match?: boolean;
  has_tailored_cv?: boolean;
  tailored_cv_updated_at?: string | null;
  application_status: ApplicationStatus;
  application_notes: string | null;
  job_application: JobApplication | null;
  updated_at: string | null;
}

export interface MatcherFeedbackSnapshot {
  ats_score?: number | null;
  score_label?: string | null;
  matched_required_skills?: string[];
  missing_required_skills?: string[];
  missing_mandatory_requirements?: string[];
  missing_keywords?: string[];
  cv_improvements?: string[];
  score_reasons?: string[];
  component_scores?: Record<string, number>;
  profile_match_score?: number | null;
  profile_missing_skills?: string[];
  mandatory_failed?: boolean;
}

export interface TailoredCvResponse {
  cv_id: string;
  job_id: number;
  title: string | null;
  company: string | null;
  markdown: string;
  /** Resume body only (after ---); preferred for copy/download. */
  cv_markdown?: string;
  changes_breakdown?: string[];
  estimated_ats_score?: number | null;
  highlights: string[];
  caveats: string[];
  from_cache: boolean;
  saved_path: string;
  generated_at?: string | null;
  regenerated?: boolean;
  improved?: boolean;
  no_improvement?: boolean;
  message?: string | null;
  matcher_feedback?: {
    previous?: MatcherFeedbackSnapshot;
    current?: MatcherFeedbackSnapshot;
    discarded?: MatcherFeedbackSnapshot;
  } | null;
}

export interface CvScanStatus {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  warnings?: string[];
  collection?: CollectionSummary | null;
  current_step: string | null;
  detail: string | null;
  steps: PipelineStep[];
  log: string[];
  latest_scan: CvScan | null;
}

export function parseScanSummary(summary: string | null | undefined): {
  matches: number | null;
  warnings: string[];
  collection: CollectionSummary | null;
} {
  if (!summary) {
    return { matches: null, warnings: [], collection: null };
  }
  try {
    const data = JSON.parse(summary) as {
      matches?: number;
      warnings?: string[];
      collection?: CollectionSummary;
    };
    return {
      matches: typeof data.matches === "number" ? data.matches : null,
      warnings: Array.isArray(data.warnings) ? data.warnings : [],
      collection: data.collection ?? null,
    };
  } catch {
    return { matches: null, warnings: [], collection: null };
  }
}

export class DuplicateCvError extends Error {
  existing: Cv;
  constructor(existing: Cv) {
    super("duplicate");
    this.name = "DuplicateCvError";
    this.existing = existing;
  }
}

export function listServerCvs(): Promise<{
  cvs: Cv[];
  workspace_match_count?: number;
  active_cv_count?: number;
}> {
  return request(`/cvs`);
}

export function getServerCv(cvId: string): Promise<{ cv: Cv }> {
  return request(`/cvs/${cvId}`);
}

export async function uploadCv(
  file: File,
  options?: { asNewVersion?: boolean; displayName?: string }
): Promise<Cv> {
  const form = new FormData();
  form.append("file", file);
  if (options?.asNewVersion) form.append("as_new_version", "true");
  if (options?.displayName) form.append("display_name", options.displayName);

  const res = await fetch(`${BASE_URL}/cvs/upload`, {
    method: "POST",
    body: form,
  });
  if (res.status === 409) {
    const body = await res.json().catch(() => null);
    const existing = body?.detail?.existing as Cv | undefined;
    if (existing) throw new DuplicateCvError(existing);
    throw new Error("קובץ זהה כבר הועלה");
  }
  if (!res.ok) {
    let detail = `שגיאה ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep generic message */
    }
    throw new Error(detail);
  }
  const data = (await res.json()) as { cv: Cv };
  return data.cv;
}

export function deleteServerCv(cvId: string): Promise<{ deleted: boolean }> {
  return request(`/cvs/${cvId}`, { method: "DELETE" });
}

export interface JobSite {
  id: string;
  label: string;
  label_he: string;
  description_he: string;
  enabled: boolean;
}

export function listJobSites(): Promise<{ sites: JobSite[] }> {
  return request(`/api/job-sites`);
}

export function runAgentForCv(
  cvId: string,
  options?: {
    skip_collect?: boolean;
    skip_enrich?: boolean;
    job_sites?: string[];
  }
): Promise<{ started: boolean; cv_id: string }> {
  return request(`/cvs/${cvId}/run-agent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options ?? {}),
  });
}

/** Run the job-matching agent across all uploaded CV files. */
export function runJobMatcher(
  options?: {
    skip_collect?: boolean;
    skip_enrich?: boolean;
    job_sites?: string[];
  }
): Promise<{ started: boolean; user_id: string; cv_count: number }> {
  return request(`/jobs/match`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options ?? {}),
  });
}

export function stopJobMatcher(): Promise<{ stopping: boolean; user_id: string }> {
  return request(`/jobs/match/stop`, { method: "POST" });
}

export function getJobMatchStatus(): Promise<CvScanStatus & {
  match_count?: number;
  cv_count?: number;
  can_stop?: boolean;
}> {
  return request(`/jobs/match-status`);
}

export function getJobMatches(
  options?: { latest?: boolean; minScore?: number }
): Promise<{ matches: CvMatch[] }> {
  const params = new URLSearchParams();
  params.set("latest", String(options?.latest ?? true));
  if (options?.minScore != null) params.set("min_score", String(options.minScore));
  return request(`/jobs/matches?${params.toString()}`);
}

export function updateWorkspaceMatchStatus(
  matchId: number,
  status: ApplicationStatus,
  notes?: string
): Promise<{ updated: boolean; match: CvMatch }> {
  return request(`/jobs/matches/${matchId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, notes: notes ?? null }),
  });
}

export function tailorWorkspaceJob(
  jobId: number,
  options?: { force?: boolean; regenerate?: boolean; sourceCvId?: string }
): Promise<TailoredCvResponse> {
  const regenerate = Boolean(options?.regenerate);
  const force = Boolean(options?.force) || regenerate;
  const params = new URLSearchParams();
  if (regenerate) params.set("regenerate", "true");
  if (options?.sourceCvId) params.set("source_cv_id", options.sourceCvId);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return request(`/jobs/${jobId}/tailor-cv${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
}

export function getCvScanStatus(cvId: string): Promise<CvScanStatus> {
  return request(`/cvs/${cvId}/scan-status`);
}

export function getCvMatches(
  cvId: string,
  options?: { latest?: boolean; minScore?: number }
): Promise<{ matches: CvMatch[] }> {
  const params = new URLSearchParams();
  params.set("latest", String(options?.latest ?? true));
  if (options?.minScore != null) params.set("min_score", String(options.minScore));
  return request(`/cvs/${cvId}/matches?${params.toString()}`);
}

export function updateMatchStatus(
  cvId: string,
  matchId: number,
  status: ApplicationStatus,
  notes?: string
): Promise<{ updated: boolean; match: CvMatch }> {
  return request(`/cvs/${cvId}/matches/${matchId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, notes: notes ?? null }),
  });
}

export function tailorCvForJob(
  cvId: string,
  jobId: number,
  options?: { force?: boolean; regenerate?: boolean }
): Promise<TailoredCvResponse> {
  const regenerate = Boolean(options?.regenerate);
  const force = Boolean(options?.force) || regenerate;
  const qs = regenerate ? "?regenerate=true" : "";
  return request(`/cvs/${cvId}/jobs/${jobId}/tailor-cv${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
}

function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const utfMatch = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (utfMatch?.[1]) {
    try {
      return decodeURIComponent(utfMatch[1].trim());
    } catch {
      /* fall through */
    }
  }
  const plainMatch = /filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;]+)/i.exec(header);
  const raw = (plainMatch?.[1] ?? plainMatch?.[2] ?? "").trim();
  return raw || null;
}

/** Download the tailored CV as a professionally rendered PDF (Playwright). */
export async function downloadTailoredCvPdf(
  cvId: string,
  jobId: number
): Promise<void> {
  const res = await fetch(
    `${BASE_URL}/cvs/${cvId}/jobs/${jobId}/tailored-cv/download-pdf`
  );
  if (!res.ok) {
    let detail = `שגיאה ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* keep generic message */
    }
    throw new Error(detail);
  }

  const blob = await res.blob();
  const filename =
    filenameFromContentDisposition(res.headers.get("Content-Disposition")) ||
    "Gal_Lifshitz_CV_Tailored.pdf";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export class DuplicateApplicationError extends Error {
  code = "duplicate_application";
  constructor(message: string) {
    super(message);
    this.name = "DuplicateApplicationError";
  }
}

export class ApplicationInProgressError extends Error {
  code = "application_in_progress";
  constructor(message: string) {
    super(message);
    this.name = "ApplicationInProgressError";
  }
}

function parseApplicationError(res: Response, body: unknown): Error {
  const detail = (body as { detail?: unknown })?.detail;
  if (detail && typeof detail === "object" && detail !== null) {
    const code = (detail as { code?: string }).code;
    const message = (detail as { message?: string }).message ?? `שגיאה ${res.status}`;
    if (code === "duplicate_application") return new DuplicateApplicationError(message);
    if (code === "application_in_progress") return new ApplicationInProgressError(message);
    return new Error(message);
  }
  if (typeof detail === "string") return new Error(detail);
  return new Error(`שגיאה ${res.status}`);
}

export async function applyToJob(
  cvId: string,
  jobId: number,
  options?: { force?: boolean }
): Promise<{ application_id: string; status: JobApplicationStatus; application: JobApplication }> {
  const res = await fetch(`${BASE_URL}/cvs/${cvId}/jobs/${jobId}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force: options?.force ?? false }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw parseApplicationError(res, body);
  }
  return res.json();
}

export function getJobApplication(
  cvId: string,
  applicationId: string
): Promise<JobApplication> {
  return request(`/cvs/${cvId}/job-applications/${applicationId}`);
}

export function getJobApplicationStatus(
  cvId: string,
  jobId: number
): Promise<{ status: JobApplicationStatus | null; application: JobApplication | null }> {
  return request(`/cvs/${cvId}/jobs/${jobId}/application-status`);
}

export async function retryJobApplication(
  cvId: string,
  applicationId: string
): Promise<{ application_id: string; status: JobApplicationStatus; application: JobApplication }> {
  const res = await fetch(`${BASE_URL}/cvs/${cvId}/job-applications/${applicationId}/retry`, {
    method: "POST",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw parseApplicationError(res, body);
  }
  return res.json();
}

export interface SiteCredentialPublic {
  email: string;
  password_set: boolean;
  configured: boolean;
}

export interface SiteCredentialsResponse {
  credentials: {
    linkedin: SiteCredentialPublic;
    drushim: SiteCredentialPublic;
  };
}

export interface SiteCredentialInput {
  email: string;
  password?: string;
}

export function getSiteCredentials(cvId: string): Promise<SiteCredentialsResponse> {
  return request(`/cvs/${cvId}/site-credentials`);
}

export function saveSiteCredentials(
  cvId: string,
  payload: {
    linkedin?: SiteCredentialInput;
    drushim?: SiteCredentialInput;
  }
): Promise<SiteCredentialsResponse & { saved: boolean }> {
  return request(`/cvs/${cvId}/site-credentials`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
