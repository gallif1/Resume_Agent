import { authFetch, authJsonRequest } from "./api";

export type LiveScannerStatus =
  | "STOPPED"
  | "RUNNING"
  | "PAUSED"
  | "BUILDING_BASELINE";

export type LiveSourceStatus =
  | "NOT_INITIALIZED"
  | "BASELINE_SCANNING"
  | "LIVE"
  | "ERROR"
  | "DISABLED";

export interface LiveScannerState {
  status: LiveScannerStatus;
  session_id?: string | null;
  session_started_at?: string | null;
  last_scan_at?: string | null;
  next_scan_at?: string | null;
  jobs_checked: number;
  new_jobs: number;
  duplicates_skipped: number;
  relevant_jobs: number;
  baseline_jobs: number;
  jobs_fetched?: number;
  foreign_filtered?: number;
  israel_jobs?: number;
  sources_monitored: number;
  sources_initialized: number;
  sources_failed: number;
  market?: string;
}

export interface LiveSource {
  id: string;
  company_name: string;
  provider: string;
  board_identifier: string;
  careers_url?: string | null;
  enabled: number | boolean;
  scan_interval_seconds: number;
  status: LiveSourceStatus | string;
  last_scan_at?: string | null;
  last_success_at?: string | null;
  last_error?: string | null;
  baseline_created_at?: string | null;
  baseline_job_count?: number | null;
  notes?: string | null;
  is_demo?: number | boolean;
}

export interface LiveActivity {
  id: number;
  created_at: string;
  level: string;
  message: string;
  source_id?: string | null;
  job_id?: number | null;
}

export interface LiveSessionJob {
  id: number;
  job_id: number;
  discovered_at: string;
  title?: string;
  company?: string;
  location?: string;
  provider?: string;
  source_id?: string;
  job_url?: string;
  match_score?: number | null;
  is_baseline?: number;
}

export interface LiveScannerSnapshot {
  state: LiveScannerState;
  market?: string;
  worker_alive: boolean;
  sources: LiveSource[];
  activity: LiveActivity[];
  /** All jobs discovered this session (baseline + new), newest first. */
  discovered_jobs: LiveSessionJob[];
  /** Subset of discovered_jobs where is_baseline is falsy. */
  new_jobs: LiveSessionJob[];
  providers: Array<{ id: string; supported: boolean; requires_browser: boolean }>;
}

export async function getLiveScannerStatus(): Promise<LiveScannerSnapshot> {
  return authJsonRequest<LiveScannerSnapshot>("/api/live-scanner/status");
}

export async function liveScannerPlay(): Promise<{ state: LiveScannerState }> {
  return authJsonRequest("/api/live-scanner/play", { method: "POST" });
}

export async function liveScannerPause(): Promise<{ state: LiveScannerState }> {
  return authJsonRequest("/api/live-scanner/pause", { method: "POST" });
}

export async function liveScannerStop(): Promise<{ state: LiveScannerState }> {
  return authJsonRequest("/api/live-scanner/stop", { method: "POST" });
}

export async function liveScannerClear(): Promise<{ state: LiveScannerState }> {
  return authJsonRequest("/api/live-scanner/clear", { method: "POST" });
}

export async function addLiveSource(body: {
  company_name: string;
  provider: string;
  board_identifier: string;
  careers_url?: string;
  enabled?: boolean;
  scan_interval_seconds?: number;
}): Promise<{ source: LiveSource }> {
  return authJsonRequest("/api/live-scanner/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateLiveSource(
  sourceId: string,
  body: Record<string, unknown>
): Promise<{ source: LiveSource }> {
  return authJsonRequest(`/api/live-scanner/sources/${sourceId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteLiveSource(sourceId: string): Promise<void> {
  const res = await authFetch(`/api/live-scanner/sources/${sourceId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Delete failed (${res.status})`);
  }
}
