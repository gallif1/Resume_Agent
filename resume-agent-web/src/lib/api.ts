// HTTP client for the ai-job-agent backend (FastAPI, separate repository).

// In dev, Vite proxies /api and /cvs to the FastAPI server (see vite.config.ts).
// In production the frontend is served by the same FastAPI app, so default to
// same-origin requests unless an explicit API URL override is provided.
const BASE_URL: string = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

const TOKEN_KEY = "resume_agent_jwt";

export type AuthUser = {
  id: string;
  email: string | null;
  display_name: string | null;
  created_at?: string | null;
};

export type AuthResponse = {
  access_token: string;
  token_type: string;
  user: AuthUser;
};

let onUnauthorized: (() => void) | null = null;

/** Register a callback invoked when any API call returns 401. */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode / storage blocked */
  }
}

export function clearAuthSession() {
  setStoredToken(null);
}

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const token = getStoredToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

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

export type MatchSortBy = "date" | "score" | "site";
export type MatchSortOrder = "asc" | "desc";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = authHeaders(init?.headers);
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  } catch (err) {
    if (
      (err instanceof DOMException && err.name === "AbortError") ||
      (err instanceof Error && err.name === "AbortError")
    ) {
      throw err;
    }
    throw new Error("השרת לא זמין כרגע — נסה לרענן בעוד דקה");
  }
  if (res.status === 401) {
    clearAuthSession();
    onUnauthorized?.();
    throw new Error("נדרשת התחברות מחדש");
  }
  if (!res.ok) {
    let detail = `שגיאה ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail?.message) detail = body.detail.message;
      else if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* keep generic message */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

async function authRequest(path: string, email: string, password: string): Promise<AuthResponse> {
  // Auth forms must not trigger the global 401 logout handler on bad credentials.
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new Error("השרת לא זמין כרגע — נסה לרענן בעוד דקה");
  }
  if (!res.ok) {
    let detail = `שגיאה ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep generic */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<AuthResponse>;
}

export function registerUser(email: string, password: string): Promise<AuthResponse> {
  return authRequest(`/api/auth/register`, email, password);
}

export function loginUser(email: string, password: string): Promise<AuthResponse> {
  return authRequest(`/api/auth/login`, email, password);
}

export function getCurrentUser(): Promise<{ user: AuthUser }> {
  return request(`/api/auth/me`);
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
  /** Full job description text (enriched), for the expanded job view. */
  description?: string | null;
  /** Board publication date as YYYY-MM-DD (preferred for chronological sort). */
  posted_date?: string | null;
  job_created_at?: string | null;
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
  match_score?: number | null;
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

export interface ChangeLogItem {
  original_text?: string;
  new_text?: string;
  reason?: string;
  supporting_evidence?: string;
  related_job_requirement?: string;
  section?: string;
  change_type?: string;
  evidence_type?: string;
  source_fact_ids?: string[];
  confidence?: number;
  inference_category?:
    | "Explicit"
    | "Strongly Inferred"
    | "Weakly Inferred"
    | "Unsupported"
    | string;
  confidence_score?: number;
  accepted?: boolean | null;
}

export interface InferredCompetency {
  statement: string;
  supporting_evidence?: string;
  reasoning?: string;
  confidence_score?: number;
  related_requirement?: string;
  ontology_rule_id?: string;
  inference_category?: string;
}

export interface ValidationWarningItem {
  statement: string;
  reason?: string;
  inference_category?: string;
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
  /** Frozen scan baseline from cv_job_matches.initial_score */
  initial_match_score?: number | null;
  /** Score of the previous tailored version (or baseline on first generate) */
  score_before?: number | null;
  /** Deterministic score after tailoring/optimization */
  score_after?: number | null;
  version_id?: number | null;
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
  /** Requirement-level evaluation behind the score (additive, optional). */
  realistic_match_score?: number | null;
  requirement_extraction?: {
    hard_requirements?: RequirementAssessment[];
    soft_requirements?: RequirementAssessment[];
  } | null;
  key_matching_points?: string[];
  missing_critical_skills?: string[];
  transferable_skills_framing?: TransferableFraming[];
  score_validation?: {
    model_reported_score?: number | null;
    recomputed_composite_score?: number | null;
    score_overridden?: boolean;
    cap?: number | null;
    dropped_unsupported_skills?: string[];
  } | null;
  recommendation?: string | null;
  /** Intelligent Resume Tailoring structured report fields */
  tailored_resume?: Record<string, unknown> | null;
  matched_requirements?: string[];
  missing_requirements?: string[];
  inferred_competencies?: InferredCompetency[];
  removed_or_deprioritized_content?: string[];
  ats_keywords_added?: string[];
  change_log?: ChangeLogItem[];
  validation_warnings?: ValidationWarningItem[];
  original_match_score?: number | null;
  tailored_match_score?: number | null;
  /** Structured ATS/job-fit breakdown computed after final validation. */
  score_breakdown?: ScoreBreakdown | null;
  language?: string | null;
  claim_validator_passed?: boolean;
  pipeline_version?: string | null;
  truthfulness_statement?: string | null;
  quality_report?: {
    overall_tailoring_score?: number;
    job_requirement_coverage?: number;
    high_value_fact_utilization?: number;
    summary_specificity?: number;
    warnings?: string[];
    regeneration_required?: boolean;
  } | null;
  extraction_coverage?: {
    extraction_coverage_score?: number;
    extracted_fact_count?: number;
    source_fact_count?: number;
    parsing_warnings?: string[];
    fallback_applied?: boolean;
  } | null;
  missed_evidence_report?: {
    additional_relevant_facts_found?: Array<{ text?: string; requirement?: string }>;
    facts_still_uncovered?: string[];
  } | null;
  knowledge_base_summary?: {
    fact_count?: number;
    content_hash?: string;
    coverage?: Record<string, unknown>;
  } | null;
  tailoring_report?: Record<string, unknown> | null;
  run_id?: string | null;
  decision_log?: TailorDecision[];
  generation_report?: GenerationReport | null;
  top_interview_reasons?: string[];
  writing_report?: Record<string, unknown> | null;
  recruiter_review?: Record<string, unknown> | null;
  hiring_manager_feedback?: Record<string, unknown> | null;
  agent_trace?: Array<Record<string, unknown>>;
  one_page?: Record<string, unknown> | null;
  sections_changed?: string[];
  quality_gates?: {
    passed?: boolean;
    failures?: string[];
    warnings?: string[];
    critical_failures?: string[];
    download_blocked?: boolean;
    preview_allowed?: boolean;
    review_mode?: boolean;
    user_messages?: Array<{ code?: string; severity?: string; message?: string }>;
  } | null;
  preview_allowed?: boolean;
  download_blocked?: boolean;
  review_mode?: boolean;
  gate_user_messages?: Array<{ code?: string; severity?: string; message?: string }>;
  pipeline_metrics?: Record<string, unknown> | null;
}

export interface TailorDecision {
  action?: string;
  text: string;
  target?: string;
  reason?: string;
  stage?: string;
}

export interface TailorStageEvent {
  run_id?: string;
  cv_id?: string;
  job_id?: number;
  stage?: string;
  agent_id?: string;
  status?: "started" | "running" | "completed" | "failed" | string;
  message?: string;
  index?: number;
  total?: number;
  decision?: TailorDecision;
  event?: string;
}

export interface ScoreBreakdown {
  original_score?: number | null;
  tailored_score?: number | null;
  score_delta?: number | null;
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
  calculation_status?: "pending" | "calculating" | "complete" | "failed" | string;
  score_version?: string | null;
  resume_version_id?: string | number | null;
}

export interface GenerationReport {
  status?: string;
  job_requirements_analyzed?: number;
  candidate_strengths_identified?: number;
  transferable_skills_inferred?: number;
  resume_revisions?: number;
  ats_optimization_completed?: boolean;
  recruiter_review_completed?: boolean;
  hiring_manager_review_completed?: boolean;
  would_interview?: boolean;
  top_interview_reasons?: string[];
  review_cycles?: number | null;
  hm_refine_pass?: boolean;
  generation_time_seconds?: number | null;
  pipeline_version?: string | null;
  sections_changed?: string[];
  run_id?: string;
  score_breakdown?: ScoreBreakdown | null;
  agents_completed?: number | null;
  agents_total?: number | null;
  overall_progress?: number | null;
  /** True when the API served a saved draft instead of running the pipeline. */
  from_cache?: boolean;
}

export interface RequirementAssessment {
  requirement: string;
  candidate_status: "MATCH" | "PARTIAL" | "MISSING" | string;
  evidence: string;
}

export interface TransferableFraming {
  gap: string;
  how_to_honestly_frame_existing_experience: string;
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
  match_count?: number;
}

export function parseScanSummary(summary: string | null | undefined): {
  matches: number | null;
  newJobs: number | null;
  newMatches: number | null;
  warnings: string[];
  collection: CollectionSummary | null;
} {
  if (!summary) {
    return { matches: null, newJobs: null, newMatches: null, warnings: [], collection: null };
  }
  try {
    const data = JSON.parse(summary) as {
      matches?: number;
      new_jobs?: number;
      new_matches?: number;
      warnings?: string[];
      collection?: CollectionSummary;
    };
    return {
      matches: typeof data.matches === "number" ? data.matches : null,
      newJobs: typeof data.new_jobs === "number" ? data.new_jobs : null,
      newMatches: typeof data.new_matches === "number" ? data.new_matches : null,
      warnings: Array.isArray(data.warnings) ? data.warnings : [],
      collection: data.collection ?? null,
    };
  } catch {
    return { matches: null, newJobs: null, newMatches: null, warnings: [], collection: null };
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

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}/cvs/upload`, {
      method: "POST",
      body: form,
      headers: authHeaders(),
    });
  } catch {
    throw new Error("השרת לא זמין כרגע — לא ניתן להעלות קבצים");
  }
  if (res.status === 401) {
    clearAuthSession();
    onUnauthorized?.();
    throw new Error("נדרשת התחברות מחדש");
  }
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
      else if (body?.detail?.message) detail = body.detail.message;
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

/** Clear workspace match/scan results; keep uploaded CV files. */
export function resetJobMatches(): Promise<{
  ok: boolean;
  reset: string;
  user_id: string;
}> {
  return request(`/jobs/matches/reset`, { method: "POST" });
}

/** Delete all uploaded CVs and clear workspace results/profiles. */
export function resetAllCvs(): Promise<{
  ok: boolean;
  reset: string;
  deleted_count: number;
  deleted_cv_ids: string[];
}> {
  return request(`/cvs/reset`, { method: "POST" });
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
    domains?: string[];
  }
): Promise<{ started: boolean; cv_id: string }> {
  return request(`/cvs/${cvId}/run-agent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options ?? {}),
  });
}

export interface AnalyzeCvResponse {
  cv_id: string;
  domains: string[];
  candidate_summary?: string;
  career_notes?: string;
  best_fit_roles?: Array<{
    role: string;
    score?: number;
    reason?: string;
    missing_skills?: string[];
    realistic_for_application?: boolean;
  }>;
}

/** Parse the CV and return recommended job domains/roles. */
export function analyzeCv(cvId: string): Promise<AnalyzeCvResponse> {
  return request(`/cvs/${cvId}/analyze`, { method: "POST" });
}

/** Start an incremental job search for selected domains. */
export function searchJobsForCv(
  cvId: string,
  options: {
    domains: string[];
    skip_enrich?: boolean;
    job_sites?: string[];
    delta?: boolean;
    max_age_days?: number;
  }
): Promise<{
  started: boolean;
  cv_id: string;
  domains: string[];
  delta?: boolean;
  max_age_days?: number;
}> {
  return request(`/cvs/${cvId}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      domains: options.domains,
      skip_enrich: options.skip_enrich,
      job_sites: options.job_sites,
      delta: options.delta,
      max_age_days: options.max_age_days,
    }),
  });
}

/** Delta refresh using the last successful scan's domains and boards.
 *  Stops each keyword/channel at the first already-known job (incremental).
 */
export function refreshCvJobs(
  cvId: string
): Promise<{
  started: boolean;
  cv_id: string;
  domains: string[];
  job_sites?: string[] | null;
  delta: boolean;
}> {
  return request(`/cvs/${cvId}/refresh`, { method: "POST" });
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

/** Build the SSE URL for live scan events (EventSource cannot set Auth headers). */
export function scanStreamUrl(): string {
  const token = getStoredToken() ?? "";
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${BASE_URL}/api/scan/stream${qs}`;
}

/** Build the SSE URL for live resume-generation progress. */
export function tailorStreamUrl(options?: {
  cvId?: string;
  jobId?: number;
}): string {
  const token = getStoredToken() ?? "";
  const params = new URLSearchParams();
  if (token) params.set("token", token);
  if (options?.cvId) params.set("cv_id", options.cvId);
  if (options?.jobId != null) params.set("job_id", String(options.jobId));
  const qs = params.toString();
  return `${BASE_URL}/api/tailor/stream${qs ? `?${qs}` : ""}`;
}

export function getJobMatches(
  options?: {
    latest?: boolean;
    minScore?: number;
    sortBy?: MatchSortBy;
    order?: MatchSortOrder;
  }
): Promise<{ matches: CvMatch[] }> {
  const params = new URLSearchParams();
  params.set("latest", String(options?.latest ?? false));
  if (options?.minScore != null) params.set("min_score", String(options.minScore));
  if (options?.sortBy) params.set("sort_by", options.sortBy);
  if (options?.order) params.set("order", options.order);
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
  options?: {
    force?: boolean;
    regenerate?: boolean;
    sourceCvId?: string;
    signal?: AbortSignal;
  }
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
    signal: options?.signal,
  });
}

export function getCvScanStatus(cvId: string): Promise<CvScanStatus> {
  return request(`/cvs/${cvId}/scan-status`);
}

export function getCvMatches(
  cvId: string,
  options?: {
    latest?: boolean;
    minScore?: number;
    sortBy?: MatchSortBy;
    order?: MatchSortOrder;
  }
): Promise<{ matches: CvMatch[] }> {
  const params = new URLSearchParams();
  params.set("latest", String(options?.latest ?? false));
  if (options?.minScore != null) params.set("min_score", String(options.minScore));
  if (options?.sortBy) params.set("sort_by", options.sortBy);
  if (options?.order) params.set("order", options.order);
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
  options?: { force?: boolean; regenerate?: boolean; signal?: AbortSignal }
): Promise<TailoredCvResponse> {
  const regenerate = Boolean(options?.regenerate);
  const force = Boolean(options?.force) || regenerate;
  const qs = regenerate ? "?regenerate=true" : "";
  return request(`/cvs/${cvId}/jobs/${jobId}/tailor-cv${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
    signal: options?.signal,
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

async function fetchTailoredCvPdfBlob(
  cvId: string,
  jobId: number,
  path: "download-pdf" | "preview-pdf"
): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(
    `${BASE_URL}/cvs/${cvId}/jobs/${jobId}/tailored-cv/${path}`,
    { headers: authHeaders() }
  );
  if (res.status === 401) {
    clearAuthSession();
    onUnauthorized?.();
    throw new Error("נדרשת התחברות מחדש");
  }
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
  return { blob, filename };
}

/** Reopen a saved tailored CV without regenerating (no export gates). */
export function getTailoredCvPreview(
  cvId: string,
  jobId: number
): Promise<TailoredCvResponse> {
  return request(`/cvs/${cvId}/jobs/${jobId}/tailored-cv/preview`);
}

/** Open the tailored CV PDF in a new tab for on-screen preview (no export gates). */
export async function openTailoredCvPdfPreview(
  cvId: string,
  jobId: number
): Promise<void> {
  const { blob } = await fetchTailoredCvPdfBlob(cvId, jobId, "preview-pdf");
  const url = URL.createObjectURL(blob);
  const opened = window.open(url, "_blank", "noopener,noreferrer");
  if (!opened) {
    // Popup blocked — fall back to same-tab navigation.
    window.location.href = url;
  } else {
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
}

/** Download the tailored CV as a professionally rendered PDF (Playwright). */
export async function downloadTailoredCvPdf(
  cvId: string,
  jobId: number
): Promise<void> {
  const { blob, filename } = await fetchTailoredCvPdfBlob(
    cvId,
    jobId,
    "download-pdf"
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Download the approved tailored CV as an ATS-friendly DOCX. */
export async function downloadTailoredCvDocx(
  cvId: string,
  jobId: number
): Promise<void> {
  const res = await fetch(
    `${BASE_URL}/cvs/${cvId}/jobs/${jobId}/tailored-cv/download-docx`,
    { headers: authHeaders() }
  );
  if (res.status === 401) {
    clearAuthSession();
    onUnauthorized?.();
    throw new Error("נדרשת התחברות מחדש");
  }
  if (!res.ok) {
    let detail = `שגיאה ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* keep generic */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const filename =
    filenameFromContentDisposition(res.headers.get("Content-Disposition")) ||
    "CV_Tailored.docx";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function applyTailoredChangeDecisions(
  cvId: string,
  jobId: number,
  decisions: { index: number; accepted: boolean }[]
): Promise<TailoredCvResponse> {
  return request(`/cvs/${cvId}/jobs/${jobId}/tailored-cv/changes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decisions }),
  });
}

export function regenerateTailoredSection(
  cvId: string,
  jobId: number,
  section: string,
  options?: { language?: string }
): Promise<TailoredCvResponse> {
  return request(`/cvs/${cvId}/jobs/${jobId}/tailored-cv/regenerate-section`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      section,
      force: true,
      language: options?.language ?? null,
    }),
  });
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
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ force: options?.force ?? false }),
  });
  if (res.status === 401) {
    clearAuthSession();
    onUnauthorized?.();
    throw new Error("נדרשת התחברות מחדש");
  }
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
    headers: authHeaders(),
  });
  if (res.status === 401) {
    clearAuthSession();
    onUnauthorized?.();
    throw new Error("נדרשת התחברות מחדש");
  }
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
