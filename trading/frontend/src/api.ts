/** API helpers — paths are relative to the public /trading prefix. */

export type SystemState = "stopped" | "running" | "paused";

export type Tick = {
  symbol: string;
  price: number;
  change_pct: number;
  volume: number;
  ts: number;
  provider?: string;
  session?: string;
  freshness?: string;
  stale_reason?: string | null;
  asset_class?: string;
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
  votes?: AgentVote[];
  ts: number;
};

export type PricePoint = {
  ts: number;
  price: number;
  volume?: number;
  change_pct?: number;
  open?: number;
  high?: number;
  low?: number;
};

export type TradeAgent = {
  id?: string;
  name?: string;
  side?: "BUY" | "SELL" | "HOLD";
  confidence?: number;
};

export type TradeMarker = {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  price?: number | null;
  quantity?: number | null;
  ts: number;
  confidence?: number;
  agents?: TradeAgent[];
};

export type Portfolio = {
  cash: number;
  realized_pnl: number;
  positions: Record<string, { symbol: string; quantity: number; avg_price: number }>;
};

export type SymbolMeta = {
  provider: string;
  asset_class: string;
  session: string;
  freshness: string;
  stale_reason?: string | null;
  last_update_ts?: number;
  price?: number;
  change_pct?: number;
};

export type MarketMeta = {
  source?: string;
  simulated?: boolean;
  crypto_provider?: string;
  stock_provider?: string;
  stock_provider_configured?: boolean;
  us_equity_session?: string;
  default_timeframe?: string;
  timeframes?: string[];
  symbols?: Record<string, SymbolMeta>;
};

export type AIStatus = {
  enabled?: boolean;
  provider?: string;
  model?: string;
  api_key_configured?: boolean;
  calls_this_hour?: number;
  max_calls_per_hour?: number;
  min_interval_sec?: number;
  last_by_symbol?: Record<
    string,
    {
      action?: string;
      confidence?: number;
      reason?: string;
      source?: string;
      skip_reason?: string;
      ts?: number;
    }
  >;
};

export type Snapshot = {
  state: SystemState;
  tick_count: number;
  started_at: number | null;
  last_error: string | null;
  symbols: string[];
  market: Tick[];
  price_history?: Record<string, PricePoint[]>;
  chart_timeframe?: string;
  market_meta?: MarketMeta;
  events: MarketEvent[];
  decisions: Decision[];
  trades?: TradeMarker[];
  agents: { id: string; name: string }[];
  ai?: AIStatus;
  portfolio: Portfolio;
  tick_interval_sec: number;
  data_mode?: string;
};

export type TradingConfig = {
  base_path: string;
  resume_agent_home: string;
  ws_path: string;
  ws_paths?: string[];
  api_base: string;
  data_mode?: string;
  chart_timeframes?: string[];
  default_chart_timeframe?: string;
  market_meta?: MarketMeta;
  ai?: AIStatus;
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
  return jsonFetch<TradingConfig>("/config");
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

export function setChartTimeframe(timeframe: string) {
  return jsonFetch<Snapshot>("/chart-timeframe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ timeframe }),
  });
}

export function tradingWsUrls(config?: TradingConfig | null): string[] {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  const paths =
    config?.ws_paths?.length
      ? config.ws_paths
      : [config?.ws_path || "/trading/ws", "/trading/api/ws"];
  const seen = new Set<string>();
  const urls: string[] = [];
  for (const p of paths) {
    const path = p.startsWith("/") ? p : `/${p}`;
    const url = `${proto}//${host}${path}`;
    if (!seen.has(url)) {
      seen.add(url);
      urls.push(url);
    }
  }
  return urls;
}
