/**
 * AI Trading Command Center helpers — filters, labels, insights, replay.
 * Uses only persisted UnifiedDecision / DecisionLog data (no fabricated values).
 */
import type {
  DecisionLog,
  PerformanceStats,
  UnifiedAgentVote,
  UnifiedDecision,
} from "./api";
import type { DecisionMarkerItem, MarkerKind } from "./chartMarkers";
import { toUnixSeconds } from "./chartMarkers";

export const EXPLANATION_UNAVAILABLE = "Explanation unavailable";
export const EXPLANATION_UNAVAILABLE_HE = "הסבר לא זמין";

export type CommandCenterFilters = {
  agentId: string | "all";
  showExecuted: boolean;
  showSignals: boolean;
  showBlocked: boolean;
  showHold: boolean;
  showSystemOnly: boolean;
  showIndicators: boolean;
  showBuy: boolean;
  showSell: boolean;
};

export const DEFAULT_CC_FILTERS: CommandCenterFilters = {
  agentId: "all",
  showExecuted: true,
  showSignals: true,
  showBlocked: true,
  showHold: false,
  showSystemOnly: false,
  showIndicators: true,
  showBuy: true,
  showSell: true,
};

export type ReplayState = {
  active: boolean;
  playing: boolean;
  index: number;
  speed: number; // decisions per second
};

export const DEFAULT_REPLAY: ReplayState = {
  active: false,
  playing: false,
  index: -1,
  speed: 1,
};

export function fmtConfidence(raw: number | null | undefined): string | null {
  if (raw == null || !Number.isFinite(Number(raw))) return null;
  const n = Number(raw);
  const pct = n <= 1 ? n * 100 : n;
  return `${Math.round(pct)}%`;
}

export function sideClass(side: string | null | undefined): "buy" | "sell" | "hold" {
  const s = String(side || "HOLD").toUpperCase();
  if (s === "BUY") return "buy";
  if (s === "SELL") return "sell";
  return "hold";
}

export function agentExplanation(v: UnifiedAgentVote | null | undefined): string {
  const r = (v?.rationale || "").trim();
  return r || EXPLANATION_UNAVAILABLE_HE;
}

/** Primary evidence line from measured components / used_for_decision — never invent. */
export function agentEvidenceLine(v: UnifiedAgentVote): string | null {
  const used = v.used_for_decision || {};
  const comps = v.components || {};
  const preferKeys = [
    "rsi_14",
    "macd_hist",
    "macd",
    "ema_spread_pct",
    "price_momentum_pct",
    "sma20_dev_pct",
    "vwap_dev_pct",
    "bb_position",
    "atr_pct",
    "relative_volume",
  ];
  for (const k of preferKeys) {
    if (used[k] != null && used[k] !== "") {
      const val = used[k];
      return `${k}: ${typeof val === "number" ? Number(val).toFixed(3) : String(val)}`;
    }
  }
  const compEntries = Object.entries(comps).filter(
    ([, val]) => typeof val === "number" && Number.isFinite(val)
  ) as Array<[string, number]>;
  if (compEntries.length) {
    compEntries.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
    const [k, val] = compEntries[0];
    return `${k}: ${val >= 0 ? "+" : ""}${val.toFixed(3)}`;
  }
  if (v.score != null && Number.isFinite(Number(v.score))) {
    return `score: ${Number(v.score).toFixed(3)}`;
  }
  if (v.volatility_regime) return `regime: ${v.volatility_regime}`;
  return null;
}

export function voteBreakdown(u: UnifiedDecision | null | undefined): {
  buy: number;
  sell: number;
  hold: number;
  total: number;
  supporting: number;
  consensusPct: number | null;
} {
  const votes = u?.agent_votes || [];
  let buy = 0;
  let sell = 0;
  let hold = 0;
  for (const v of votes) {
    const s = String(v.side || "HOLD").toUpperCase();
    if (s === "BUY") buy += 1;
    else if (s === "SELL") sell += 1;
    else hold += 1;
  }
  const total = votes.length || Number(u?.agreement?.total || 0);
  const supporting = Number(u?.agreement?.supporting ?? 0);
  const consensusPct =
    total > 0 ? Math.round((supporting / total) * 100) : null;
  return { buy, sell, hold, total, supporting, consensusPct };
}

export function executionLabel(u: UnifiedDecision | null | undefined): string {
  if (!u) return "—";
  const status = String(u.status || "").toUpperCase();
  if (status === "FILLED") return "FILLED";
  if (status === "BLOCKED" || status === "REJECTED") return "BLOCKED";
  if (status === "PENDING" || status === "SUBMITTED") return "PENDING";
  if (status === "HOLD") return "NO TRADE";
  return status || "SIGNAL";
}

export function riskLabel(u: UnifiedDecision | null | undefined): string {
  if (!u) return "—";
  const status = String(u.status || "").toUpperCase();
  if (status === "BLOCKED" || status === "REJECTED") {
    return u.block_reason ? `BLOCKED` : "BLOCKED";
  }
  if (status === "FILLED") return "APPROVED";
  if (u.risk_level) return String(u.risk_level);
  if (status === "HOLD") return "N/A";
  return "PASSED";
}

export function discoverAgents(rows: UnifiedDecision[]): Array<{ id: string; name: string }> {
  const map = new Map<string, string>();
  for (const u of rows) {
    for (const v of u.agent_votes || []) {
      const id = String(v.agent_id || v.agent_name || "").trim();
      if (!id) continue;
      if (!map.has(id)) map.set(id, String(v.agent_name || v.agent_id || id));
    }
  }
  return [...map.entries()].map(([id, name]) => ({ id, name }));
}

export function filterUnifiedDecisions(
  rows: UnifiedDecision[],
  filters: CommandCenterFilters
): UnifiedDecision[] {
  return rows.filter((u) => {
    const status = String(u.status || "").toUpperCase();
    const action = String(u.final_action || "HOLD").toUpperCase();
    if (filters.showSystemOnly) {
      // System decisions only — always keep (no per-agent filter).
    } else if (filters.agentId !== "all") {
      const hit = (u.agent_votes || []).some(
        (v) => String(v.agent_id || "") === filters.agentId
      );
      if (!hit) return false;
    }
    if (status === "FILLED" && !filters.showExecuted) return false;
    if (
      (status === "SIGNAL" || status === "PENDING" || status === "SUBMITTED") &&
      !filters.showSignals
    ) {
      return false;
    }
    if ((status === "BLOCKED" || status === "REJECTED") && !filters.showBlocked) {
      return false;
    }
    if ((status === "HOLD" || action === "HOLD") && !filters.showHold) return false;
    if (action === "BUY" && !filters.showBuy) return false;
    if (action === "SELL" && !filters.showSell) return false;
    return true;
  });
}

export function filterMarkerItems(
  items: DecisionMarkerItem[],
  filters: CommandCenterFilters
): DecisionMarkerItem[] {
  return items.filter((it) => {
    const kind = it.kind as MarkerKind;
    if (filters.agentId !== "all" && !filters.showSystemOnly) {
      const agents = it.agents || it.unified?.agent_votes || [];
      const hit = agents.some(
        (a) => String((a as { agent_id?: string }).agent_id || "") === filters.agentId
      );
      if (!hit) return false;
    }
    if ((kind === "fill_buy" || kind === "fill_sell") && !filters.showExecuted) return false;
    if (
      (kind === "signal" || kind.startsWith("vote_")) &&
      !filters.showSignals
    ) {
      return false;
    }
    if ((kind === "blocked" || kind === "rejected") && !filters.showBlocked) return false;
    if ((kind === "hold" || kind === "vote_hold") && !filters.showHold) return false;
    if (kind === "pending" && !filters.showSignals) return false;
    if (it.action === "BUY" && !filters.showBuy) return false;
    if (it.action === "SELL" && !filters.showSell) return false;
    if (it.action === "HOLD" && !filters.showHold) return false;
    return true;
  });
}

export type TimelineCell = {
  decisionId: string;
  agentId: string;
  side: string;
  confidence: number | null;
  ts: number;
  candleTs: number;
  status: string;
  finalAction: string;
};

export function buildAgentTimeline(
  rows: UnifiedDecision[],
  timeframeSec: number
): { agents: Array<{ id: string; name: string }>; cells: TimelineCell[] } {
  const agents = discoverAgents(rows);
  const cells: TimelineCell[] = [];
  const seen = new Set<string>();
  for (const u of rows) {
    const ts = toUnixSeconds(Number(u.decision_time || 0));
    const candleTs =
      u.candle_time != null
        ? Math.floor(toUnixSeconds(Number(u.candle_time)))
        : Math.floor(ts / timeframeSec) * timeframeSec;
    for (const v of u.agent_votes || []) {
      const agentId = String(v.agent_id || v.agent_name || "");
      if (!agentId) continue;
      const key = `${u.decision_id}:${agentId}`;
      if (seen.has(key)) continue;
      seen.add(key);
      cells.push({
        decisionId: u.decision_id,
        agentId,
        side: String(v.side || "HOLD").toUpperCase(),
        confidence: v.confidence != null ? Number(v.confidence) : null,
        ts,
        candleTs,
        status: String(u.status || ""),
        finalAction: String(u.final_action || "HOLD").toUpperCase(),
      });
    }
  }
  cells.sort((a, b) => a.ts - b.ts);
  return { agents, cells };
}

export type AgentInsightRow = {
  id: string;
  name: string;
  total: number;
  buy: number;
  sell: number;
  hold: number;
  agreementWithFinal: number;
  agreementRate: number | null;
  ledToFill: number;
  fillRate: number | null;
  /** From outcomes module when available — directional accuracy, not P&L. */
  accuracy5mPct: number | null;
  methodology: string;
};

export function computeAgentInsights(
  rows: UnifiedDecision[],
  performance?: PerformanceStats | null
): AgentInsightRow[] {
  const agents = discoverAgents(rows);
  const byId = new Map<string, AgentInsightRow>();
  for (const a of agents) {
    byId.set(a.id, {
      id: a.id,
      name: a.name,
      total: 0,
      buy: 0,
      sell: 0,
      hold: 0,
      agreementWithFinal: 0,
      agreementRate: null,
      ledToFill: 0,
      fillRate: null,
      accuracy5mPct: null,
      methodology:
        "Agreement = agent side matches final_action. Fill rate = recommendations on FILLED decisions. Accuracy from outcome horizons when available — not trade P&L attribution.",
    });
  }
  for (const u of rows) {
    const final = String(u.final_action || "HOLD").toUpperCase();
    const filled = String(u.status || "").toUpperCase() === "FILLED";
    for (const v of u.agent_votes || []) {
      const id = String(v.agent_id || v.agent_name || "");
      const row = byId.get(id);
      if (!row) continue;
      row.total += 1;
      const side = String(v.side || "HOLD").toUpperCase();
      if (side === "BUY") row.buy += 1;
      else if (side === "SELL") row.sell += 1;
      else row.hold += 1;
      if (side === final) row.agreementWithFinal += 1;
      if (filled && side === final && side !== "HOLD") row.ledToFill += 1;
    }
  }
  for (const row of byId.values()) {
    row.agreementRate = row.total > 0 ? row.agreementWithFinal / row.total : null;
    const actionable = row.buy + row.sell;
    row.fillRate = actionable > 0 ? row.ledToFill / actionable : null;
    const perf = performance?.agents?.[row.id];
    if (perf?.accuracy_5m_pct != null) {
      row.accuracy5mPct = Number(perf.accuracy_5m_pct);
    }
  }
  return [...byId.values()].sort((a, b) => b.total - a.total);
}

/** Find matching DecisionLog for richer DE explanation (same decision id). */
export function findDecisionLog(
  logs: DecisionLog[] | undefined,
  decisionId: string
): DecisionLog | null {
  if (!logs?.length || !decisionId) return null;
  return logs.find((l) => l.id === decisionId) || null;
}

export function sortedDecisionsForReplay(rows: UnifiedDecision[]): UnifiedDecision[] {
  return [...rows].sort(
    (a, b) =>
      toUnixSeconds(Number(a.decision_time)) - toUnixSeconds(Number(b.decision_time))
  );
}

export function latestDecisionForSymbol(
  rows: UnifiedDecision[],
  symbol: string
): UnifiedDecision | null {
  const norm = symbol.toUpperCase().replace(/\//g, "-");
  const filtered = rows.filter(
    (u) => String(u.symbol || "").toUpperCase().replace(/\//g, "-") === norm
  );
  if (!filtered.length) return null;
  return sortedDecisionsForReplay(filtered).at(-1) || null;
}
