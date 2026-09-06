/** API helpers — paths are relative to the public /trading prefix. */

export type SystemState = "stopped" | "running" | "paused";

export type Tick = {
  symbol: string;
  price: number;
  change_pct: number;
  volume: number;
  ts: number;
};

export type MarketEvent = {
  id: string;
  kind: string;
  symbol: string;
  message: string;
  severity: string;
  ts: number;
};

export type AgentVote = {
  agent_id: string;
  agent_name: string;
  symbol: string;
  side: "BUY" | "SELL" | "HOLD";
  confidence: number;
  rationale: string;
  ts: number;
};

export type Decision = {
  id: string;
  symbol: string;
  side: "BUY" | "SELL" | "HOLD";
  confidence: number;
  rationale: string;
  executed: boolean;
  fill_price?: number | null;
  quantity?: number | null;
  ts: number;
};

export type Portfolio = {
  cash: number;
  realized_pnl: number;
  positions: Record<string, { symbol: string; quantity: number; avg_price: number }>;
};

export type Snapshot = {
  state: SystemState;
  tick_count: number;
  started_at: number | null;
  last_error: string | null;
  symbols: string[];
  market: Tick[];
  events: MarketEvent[];
  decisions: Decision[];
  agents: { id: string; name: string }[];
  portfolio: Portfolio;
  tick_interval_sec: number;
};

const API_BASE = "/trading/api";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function fetchSnapshot() {
  return jsonFetch<Snapshot>("/snapshot");
}

export function fetchConfig() {
  return jsonFetch<{ resume_agent_home: string; ws_path: string }>("/config");
}

export function startSystem() {
  return jsonFetch<Snapshot>("/start", { method: "POST" });
}

export function pauseSystem() {
  return jsonFetch<Snapshot>("/pause", { method: "POST" });
}

export function stopSystem() {
  return jsonFetch<Snapshot>("/stop", { method: "POST" });
}

export function tradingWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/trading/ws`;
}
