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
  inputs?: Record<string, unknown>;
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
  engine?: Record<string, unknown>;
  ts: number;
};

export type DecisionLogAgent = {
  agent_id?: string;
  agent_name?: string;
  action?: string;
  confidence?: number;
  reason?: string;
  inputs?: Record<string, unknown>;
  source?: string;
  ts?: number;
};

export type DecisionLog = {
  id: string;
  timestamp: number;
  symbol: string;
  kind: string;
  signal: { action?: string; agents?: DecisionLogAgent[] };
  market: {
    symbol?: string;
    price?: number;
    timestamp?: number;
    volume?: number;
    change_1m_pct?: number | null;
    change_5m_pct?: number | null;
    change_15m_pct?: number | null;
    change_1h_pct?: number | null;
    sma_fast?: number | null;
    sma_slow?: number | null;
    ema_fast?: number | null;
    rsi_14?: number | null;
    volatility?: number | null;
    volume_state?: string;
    trend?: string;
    detected_events?: string[];
    provider?: string;
    session?: string;
    freshness?: string;
    stale_reason?: string | null;
    asset_class?: string;
  };
  decision: {
    action?: string;
    final_confidence?: number;
    rationale?: string;
    explanation?: string;
    vote_counts?: Record<string, number>;
    weighted_contributions?: Array<Record<string, unknown>>;
    weights?: Record<string, number>;
    action_score?: number;
    hold_score?: number;
    hold_gate?: number;
    threshold?: number;
    winning_action?: string;
    confidence_debug?: {
      winning_action?: string;
      winning_score?: number;
      total_weight?: number;
      action_support?: number;
      agreement_factor?: number;
      hold_ratio?: number;
      opposition_ratio?: number;
      raw_score?: number;
      action_total_buy_sell?: number;
      total_all_weights?: number;
      denominator?: number;
      formula?: string;
      confidence_before_cap?: number;
      cap?: number;
      final_confidence?: number;
      note?: string;
    };
  };
  execution: {
    status?: string;
    reason?: string;
    fill_price?: number | null;
    quantity?: number | null;
    cooldown_remaining_sec?: number | null;
    last_fill_ts?: number | null;
  };
  pretrade?: {
    source?: string;
    skip_reason?: string | null;
    action?: string;
    confidence?: number;
    reason?: string;
    ts?: number;
  } | null;
  outcome?: {
    outcome_id?: string;
    entry_price?: number;
    action?: string;
    executed?: boolean;
    trade?: Record<string, unknown> | null;
    horizons?: Record<
      string,
      {
        status?: string;
        price?: number | null;
        return_pct?: number | null;
        direction_correct?: boolean | null;
        due_at?: number;
      }
    >;
  } | null;
};

export type ActorPerformance = {
  id?: string;
  name?: string;
  total_actionable?: number;
  buy_predictions?: number;
  sell_predictions?: number;
  accuracy_5m_pct?: number | null;
  accuracy_15m_pct?: number | null;
  accuracy_60m_pct?: number | null;
  avg_buy_return_5m?: number | null;
  avg_buy_return_15m?: number | null;
  avg_buy_return_60m?: number | null;
  n_5m?: number;
  n_15m?: number;
  n_60m?: number;
};

export type PerformanceStats = {
  decision_engine?: ActorPerformance;
  agents?: Record<string, ActorPerformance>;
  horizons?: string[];
  outcome_count?: number;
  resolved_enough?: boolean;
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

export type OhlcCandle = {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type ChartAnnotation = {
  id: string;
  user_id?: string | null;
  symbol: string;
  timeframe_scope: string;
  annotation_type: string;
  coordinates: Record<string, unknown>;
  price?: number | null;
  label?: string | null;
  note?: string | null;
  color?: string | null;
  line_style?: string | null;
  importance?: string | null;
  created_at?: number;
  updated_at?: number;
  active?: boolean;
};

export type CandlesResponse = {
  symbol: string;
  timeframe: string;
  candles: OhlcCandle[];
  has_more?: boolean;
  provider?: string;
  source?: string;
  unavailable?: boolean;
  reason?: string;
  indicators?: Record<string, Array<{ time: number; value: number; color?: string }>>;
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
  decision_logs?: DecisionLog[];
  trades?: TradeMarker[];
  agents: { id: string; name: string }[];
  ai?: AIStatus;
  portfolio: Portfolio;
  performance?: PerformanceStats;
  tick_interval_sec: number;
  data_mode?: string;
  paper_trading_only?: boolean;
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

export function clearDecisionLogs() {
  return jsonFetch<Snapshot & { cleared_logs?: number }>("/clear-logs", { method: "POST" });
}

export function resetPaperSystem() {
  return jsonFetch<
    Snapshot & {
      reset?: {
        cash?: number;
        logs_cleared?: number;
        decisions_cleared?: number;
        outcomes_cleared?: number;
        paper_trading_only?: boolean;
      };
    }
  >("/reset", { method: "POST" });
}

export function setChartTimeframe(timeframe: string) {
  return jsonFetch<Snapshot>("/chart-timeframe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ timeframe }),
  });
}

export function fetchCandles(
  symbol: string,
  timeframe: string,
  limit = 500,
  before?: number
) {
  const q = new URLSearchParams({
    timeframe,
    limit: String(limit),
  });
  if (before != null) q.set("before", String(before));
  return jsonFetch<CandlesResponse>(`/candles/${encodeURIComponent(symbol)}?${q}`);
}

export function fetchAnnotations(symbol: string) {
  return jsonFetch<{ symbol: string; annotations: ChartAnnotation[] }>(
    `/annotations/${encodeURIComponent(symbol)}`
  );
}

export function createAnnotation(payload: Record<string, unknown>) {
  return jsonFetch<{ ok: boolean; annotation?: ChartAnnotation; error?: string }>(
    "/annotations",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
}

export function updateAnnotation(id: string, payload: Record<string, unknown>) {
  return jsonFetch<{ ok: boolean; annotation?: ChartAnnotation; error?: string }>(
    `/annotations/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
}

export function deleteAnnotation(id: string) {
  return jsonFetch<{ ok: boolean }>(`/annotations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function clearAnnotations(symbol: string) {
  return jsonFetch<{ ok: boolean; cleared: number }>(
    `/annotations/symbol/${encodeURIComponent(symbol)}`,
    { method: "DELETE" }
  );
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
