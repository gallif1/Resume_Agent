/** Shared chart marker helpers: UnifiedDecision primary markers + LWC series markers. */

import type { UnifiedDecision } from "./api";

export type MarkerKind =
  | "fill_buy"
  | "fill_sell"
  | "signal"
  | "hold"
  | "blocked"
  | "vote_buy"
  | "vote_sell"
  | "vote_hold"
  | "rejected";

export type DecisionMarkerItem = {
  id: string;
  eventType: string;
  eventId: string;
  decisionId?: string;
  kind: MarkerKind;
  symbol: string;
  agentName?: string;
  action: string;
  confidence?: number | null;
  reason?: string;
  timestamp: number; // UTC seconds
  candleTs: number; // bucket open UTC seconds
  finalDecision?: string;
  filled?: boolean;
  skipReason?: string;
  price?: number | null;
  quantity?: number | null;
  totalValue?: number | null;
  status?: string;
  orderId?: string;
  fillId?: string;
  riskLevel?: string | null;
  agreement?: { supporting?: number; opposing?: number; total?: number };
  agents?: Array<Record<string, unknown>>;
  payload?: Record<string, unknown>;
  unified?: UnifiedDecision;
};

export type GroupedMarker = {
  time: number;
  count: number;
  kinds: MarkerKind[];
  items: DecisionMarkerItem[];
  primary: MarkerKind;
  label: string;
  fillCount: number;
};

const TF_SECS: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "4h": 14400,
  "1d": 86400,
};

export function normalizeSymbol(symbol: string): string {
  let s = (symbol || "").trim().toUpperCase().replace(/\s+/g, "");
  if (!s) return "";
  if (s.includes("/")) s = s.replace(/\//g, "-");
  if (!s.includes("-")) {
    for (const q of ["USDT", "USD", "EUR"] as const) {
      if (s.endsWith(q) && s.length > q.length) {
        return `${s.slice(0, -q.length)}-${q === "USDT" ? "USDT" : q}`;
      }
    }
  }
  return s;
}

/** Accept seconds or milliseconds → UTC unix seconds. */
export function toUnixSeconds(ts: number): number {
  if (!Number.isFinite(ts)) return 0;
  if (ts >= 1_000_000_000_000) return ts / 1000;
  return ts;
}

/** Map event time to candle open timestamp for the timeframe (UTC). */
export function candleBucketTs(ts: number, timeframe: string): number {
  const secs = TF_SECS[timeframe] || 300;
  const t = Math.floor(toUnixSeconds(ts));
  return Math.floor(t / secs) * secs;
}

export function markerKey(eventType: string, eventId: string): string {
  return `${eventType}:${eventId}`;
}

export function kindFromUnified(u: UnifiedDecision): MarkerKind {
  const status = String(u.status || "").toUpperCase();
  const action = String(u.final_action || "HOLD").toUpperCase();
  if (status === "FILLED") {
    return action === "SELL" ? "fill_sell" : "fill_buy";
  }
  if (status === "BLOCKED" || status === "REJECTED") return "blocked";
  if (status === "HOLD" || action === "HOLD") return "hold";
  return "signal";
}

/** Primary UI markers: one per UnifiedDecision (not every vote / raw event). */
export function unifiedToMarkerItems(
  rows: UnifiedDecision[],
  timeframe: string
): DecisionMarkerItem[] {
  const out: DecisionMarkerItem[] = [];
  for (const u of rows) {
    if (!u?.decision_id) continue;
    const action = String(u.final_action || "HOLD").toUpperCase();
    const ts = toUnixSeconds(Number(u.decision_time || 0));
    const candleTs =
      u.candle_time != null
        ? Math.floor(toUnixSeconds(Number(u.candle_time)))
        : candleBucketTs(ts, timeframe);
    const kind = kindFromUnified(u);
    const qty = u.quantity != null ? Number(u.quantity) : null;
    const price = u.fill_price != null ? Number(u.fill_price) : null;
    const execution = (u.execution || {}) as Record<string, unknown>;
    out.push({
      id: `unified:${u.decision_id}`,
      eventType: "unified",
      eventId: u.decision_id,
      decisionId: u.decision_id,
      kind,
      symbol: normalizeSymbol(String(u.symbol || "")),
      action,
      confidence: u.confidence != null ? Number(u.confidence) : null,
      reason: String(
        (u.primary_reasons && u.primary_reasons[0]) || u.summary || u.block_reason || ""
      ),
      timestamp: ts,
      candleTs,
      finalDecision: action,
      filled: kind === "fill_buy" || kind === "fill_sell",
      skipReason: u.block_reason ? String(u.block_reason) : undefined,
      price,
      quantity: qty,
      totalValue:
        u.total_value != null
          ? Number(u.total_value)
          : qty != null && price != null
            ? qty * price
            : null,
      status: String(u.status || ""),
      orderId: String(execution.order_id || ""),
      fillId: String(execution.fill_id || ""),
      riskLevel: u.risk_level != null ? String(u.risk_level) : null,
      agreement: u.agreement,
      agents: (u.agent_votes || []).map((a) => ({
        agent_id: a.agent_id,
        agent_name: a.agent_name,
        side: a.side,
        confidence: a.confidence,
        rationale: a.rationale,
      })),
      payload: {
        summary: u.summary,
        primary_reasons: u.primary_reasons,
        risk_factors: u.risk_factors,
      },
      unified: u,
    });
  }
  return out;
}

export function groupMarkersOnCandle(
  items: DecisionMarkerItem[],
  timeframe: string
): GroupedMarker[] {
  const map = new Map<number, DecisionMarkerItem[]>();
  for (const it of items) {
    const t = it.candleTs || candleBucketTs(it.timestamp, timeframe);
    const arr = map.get(t) || [];
    arr.push({ ...it, candleTs: t });
    map.set(t, arr);
  }
  const out: GroupedMarker[] = [];
  for (const [time, group] of map) {
    const kinds = [...new Set(group.map((g) => g.kind))];
    const fills = group.filter((g) => g.kind === "fill_buy" || g.kind === "fill_sell");
    const primary =
      kinds.find((k) => k === "fill_buy" || k === "fill_sell") ||
      kinds.find((k) => k === "blocked" || k === "rejected") ||
      kinds.find((k) => k === "signal" || k.startsWith("vote_")) ||
      kinds.find((k) => k === "hold") ||
      kinds[0];
    out.push({
      time,
      count: group.length,
      kinds,
      items: group,
      primary,
      label: buildGroupLabel(group, primary),
      fillCount: fills.length,
    });
  }
  return out.sort((a, b) => a.time - b.time);
}

function buildGroupLabel(group: DecisionMarkerItem[], primary: MarkerKind): string {
  const fills = group.filter((g) => g.kind === "fill_buy" || g.kind === "fill_sell");
  if (fills.length === 1) {
    const f = fills[0];
    const qty = f.quantity != null ? Number(f.quantity).toFixed(4) : "";
    const px = f.price != null ? Number(f.price).toFixed(2) : "";
    return `${f.action} ${qty} @ ${px}`.trim();
  }
  if (fills.length > 1) {
    return `${fills.length} fills`;
  }
  if (primary === "blocked") return "blocked";
  if (primary === "hold") return "HOLD";
  if (primary === "signal") {
    const top = [...group].sort((a, b) => (b.confidence || 0) - (a.confidence || 0))[0];
    const pct = top?.confidence != null ? `${Math.round(top.confidence * 100)}%` : "";
    return `${top?.action || "SIGNAL"} ${pct}`.trim();
  }
  return primary.replace(/_/g, " ");
}

export type SeriesMarkerOut = {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  text: string;
  size: number;
  id: string;
};

/** Convert grouped UnifiedDecisions to Lightweight Charts series markers (one per candle). */
export function toSeriesMarkers(groups: GroupedMarker[]): SeriesMarkerOut[] {
  return groups.map((g) => {
    let color = "#4da3ff";
    let shape: SeriesMarkerOut["shape"] = "circle";
    let position: SeriesMarkerOut["position"] = "belowBar";
    let size = 0.55;

    if (g.primary === "fill_buy") {
      color = "#22c55e";
      shape = "arrowUp";
      position = "belowBar";
      size = 1.4;
    } else if (g.primary === "fill_sell") {
      color = "#ef4444";
      shape = "arrowDown";
      position = "aboveBar";
      size = 1.4;
    } else if (g.primary === "blocked" || g.primary === "rejected") {
      color = "#f97316";
      shape = "square";
      position = "aboveBar";
      size = 0.9;
    } else if (g.primary === "hold" || g.primary === "vote_hold") {
      color = "#9aa4b2";
      shape = "square";
      position = "inBar";
      size = 0.5;
    } else if (g.primary === "signal" || g.primary === "vote_buy" || g.primary === "vote_sell") {
      color = "#4da3ff";
      shape = "circle";
      position = g.primary === "vote_sell" ? "aboveBar" : "belowBar";
      size = 0.8;
    }

    const fillCount = g.fillCount || g.items.filter((i) => i.kind.startsWith("fill_")).length;
    const text = fillCount > 1 ? `×${fillCount}` : g.count > 1 && fillCount === 0 ? `×${g.count}` : "";
    return {
      time: g.time,
      position,
      color,
      shape,
      text,
      size,
      id: `grp-${g.time}-${g.primary}`,
    };
  });
}

/** Legacy raw paper-event conversion — prefer unifiedToMarkerItems for primary UI. */
export function eventsToMarkerItems(
  events: Array<Record<string, unknown>>,
  timeframe: string
): DecisionMarkerItem[] {
  const out: DecisionMarkerItem[] = [];
  for (const ev of events) {
    const eventType = String(ev.event_type || ev.eventType || "");
    // Do not promote agent votes / raw dumps as primary chart markers.
    if (eventType === "agent_vote") continue;
    const eventId = String(ev.event_id || ev.eventId || ev.id || "");
    const side = String(ev.side || "HOLD").toUpperCase();
    const ts = toUnixSeconds(Number(ev.ts || 0));
    const payload = (ev.payload || {}) as Record<string, unknown>;
    const decisionId = String(
      ev.decision_id || payload.decision_id || (eventType === "decision" ? eventId : "") || ""
    );
    let kind: MarkerKind = "signal";
    if (eventType === "fill" || String(ev.status || "") === "FILLED") {
      kind = side === "SELL" ? "fill_sell" : "fill_buy";
    } else if (String(ev.status || "").toUpperCase() === "BLOCKED" || eventType === "rejected") {
      kind = "blocked";
    } else if (side === "HOLD") {
      kind = "hold";
    } else if (eventType === "decision") {
      if (String(ev.status || "") === "FILLED") {
        kind = side === "SELL" ? "fill_sell" : "fill_buy";
      } else if (String(ev.status || "").toUpperCase() === "BLOCKED") {
        kind = "blocked";
      } else if (side === "BUY" || side === "SELL") {
        kind = "signal";
      } else {
        kind = "hold";
      }
    }
    const qty = ev.quantity != null ? Number(ev.quantity) : null;
    const price = ev.price != null ? Number(ev.price) : null;
    out.push({
      id: String(ev.key || ev.id || markerKey(eventType, eventId)),
      eventType,
      eventId,
      decisionId: decisionId || undefined,
      kind,
      symbol: normalizeSymbol(String(ev.symbol || "")),
      agentName: String(
        payload.agent_name ||
          (Array.isArray(payload.agents)
            ? (payload.agents as Array<{ agent_name?: string }>)
                .map((a) => a.agent_name)
                .filter(Boolean)
                .join(", ")
            : "") ||
          ""
      ),
      action: side,
      confidence: ev.confidence != null ? Number(ev.confidence) : null,
      reason: String(payload.rationale || payload.reason || payload.skip_reason || ""),
      timestamp: ts,
      candleTs: Number(ev.candle_ts || candleBucketTs(ts, timeframe)),
      finalDecision: String(payload.final_decision || side),
      filled: kind === "fill_buy" || kind === "fill_sell",
      skipReason: payload.skip_reason != null ? String(payload.skip_reason) : undefined,
      price,
      quantity: qty,
      totalValue:
        payload.total_value != null
          ? Number(payload.total_value)
          : qty != null && price != null
            ? qty * price
            : null,
      status: String(ev.status || ""),
      orderId: String(payload.order_id || eventId),
      fillId: String(payload.fill_id || (eventType === "fill" ? eventId : "")),
      agents: Array.isArray(payload.agents) ? (payload.agents as Array<Record<string, unknown>>) : [],
      payload,
    });
  }
  return out;
}

/** Merge by stable id without duplicates (prefer decision_id when present). */
export function mergeMarkerItems(
  prev: DecisionMarkerItem[],
  incoming: DecisionMarkerItem[]
): DecisionMarkerItem[] {
  const map = new Map<string, DecisionMarkerItem>();
  const byDecision = new Map<string, string>();
  for (const p of prev) {
    map.set(p.id, p);
    if (p.decisionId) byDecision.set(p.decisionId, p.id);
  }
  for (const n of incoming) {
    if (n.decisionId && byDecision.has(n.decisionId)) {
      const prevId = byDecision.get(n.decisionId)!;
      const existing = map.get(prevId);
      // Prefer richer unified payload.
      if (n.unified || !existing?.unified) {
        map.delete(prevId);
        map.set(n.id, { ...existing, ...n, unified: n.unified || existing?.unified });
        byDecision.set(n.decisionId, n.id);
      }
      continue;
    }
    map.set(n.id, n);
    if (n.decisionId) byDecision.set(n.decisionId, n.id);
  }
  return [...map.values()].sort((a, b) => a.timestamp - b.timestamp);
}

export function mergeUnifiedDecisions(
  prev: UnifiedDecision[],
  incoming: UnifiedDecision[]
): UnifiedDecision[] {
  const map = new Map<string, UnifiedDecision>();
  for (const p of prev) {
    if (p?.decision_id) map.set(p.decision_id, p);
  }
  for (const n of incoming) {
    if (n?.decision_id) map.set(n.decision_id, n);
  }
  return [...map.values()].sort(
    (a, b) => toUnixSeconds(Number(a.decision_time)) - toUnixSeconds(Number(b.decision_time))
  );
}
