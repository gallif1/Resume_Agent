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

export interface Job {
  id: number;
  title: string;
  company: string | null;
  location: string | null;
  job_url: string;
  source: string | null;
  match_score: number | null;
  match_reason: string | null;
  match_category: string | null;
  ai_decision: string | null;
  ai_strengths: string | null;
  ai_missing_skills: string | null;
  ai_explanation: string | null;
  matched_at: string | null;
  first_seen_at: string | null;
  application_status: string | null;
}

export interface PipelineStep {
  key: string;
  name: string;
  status: "pending" | "running" | "success" | "failed" | "skipped";
}

export interface PipelineStatus {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  steps: PipelineStep[];
  log: string[];
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

export function getJobs(minScore: number): Promise<{ jobs: Job[] }> {
  return request(`/api/jobs?min_score=${minScore}`);
}

export function runPipeline(options?: {
  skip_collect?: boolean;
  skip_enrich?: boolean;
}): Promise<{ started: boolean }> {
  return request(`/api/pipeline/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options ?? {}),
  });
}

export function getPipelineStatus(): Promise<PipelineStatus> {
  return request(`/api/pipeline/status`);
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
  application_status: ApplicationStatus;
  application_notes: string | null;
  updated_at: string | null;
}

export interface CvScanStatus {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  current_step: string | null;
  detail: string | null;
  steps: PipelineStep[];
  log: string[];
  latest_scan: CvScan | null;
}

export class DuplicateCvError extends Error {
  existing: Cv;
  constructor(existing: Cv) {
    super("duplicate");
    this.name = "DuplicateCvError";
    this.existing = existing;
  }
}

export function listServerCvs(): Promise<{ cvs: Cv[] }> {
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

export function runAgentForCv(
  cvId: string,
  options?: { skip_collect?: boolean; skip_enrich?: boolean }
): Promise<{ started: boolean; cv_id: string }> {
  return request(`/cvs/${cvId}/run-agent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options ?? {}),
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
