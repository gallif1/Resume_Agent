/** Shared chart marker helpers: symbol/time bucketing + LWC series markers. */

export type MarkerKind =
  | "vote_buy"
  | "vote_sell"
  | "vote_hold"
  | "fill_buy"
  | "fill_sell"
  | "rejected";

export type DecisionMarkerItem = {
  id: string; // stable unique key event_type:event_id
  eventType: string;
  eventId: string;
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
  agents?: Array<Record<string, unknown>>;
  payload?: Record<string, unknown>;
};

export type GroupedMarker = {
  time: number;
  count: number;
  kinds: MarkerKind[];
  items: DecisionMarkerItem[];
  primary: MarkerKind;
  label: string;
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
    const primary =
      kinds.find((k) => k.startsWith("fill_")) ||
      kinds.find((k) => k.startsWith("vote_")) ||
      kinds.find((k) => k === "rejected") ||
      kinds[0];
    out.push({
      time,
      count: group.length,
      kinds,
      items: group,
      primary,
      label: buildGroupLabel(group, primary),
    });
  }
  return out.sort((a, b) => a.time - b.time);
}

function buildGroupLabel(group: DecisionMarkerItem[], primary: MarkerKind): string {
  const fills = group.filter((g) => g.kind.startsWith("fill_"));
  if (fills.length === 1) {
    const f = fills[0];
    const qty = f.quantity != null ? Number(f.quantity).toFixed(4) : "";
    const px = f.price != null ? Number(f.price).toFixed(2) : "";
    return `${f.action} ${qty} @ ${px}`.trim();
  }
  if (fills.length > 1) {
    return `${fills.length} fills`;
  }
  const votes = group.filter((g) => g.kind.startsWith("vote_"));
  if (votes.length) {
    const buy = votes.filter((v) => v.action === "BUY").length;
    const sell = votes.filter((v) => v.action === "SELL").length;
    const top = [...votes].sort((a, b) => (b.confidence || 0) - (a.confidence || 0))[0];
    const pct = top?.confidence != null ? `${Math.round(top.confidence * 100)}%` : "";
    if (buy && !sell) return `${buy} agent${buy > 1 ? "s" : ""} voted BUY · ${pct}`;
    if (sell && !buy) return `${sell} agent${sell > 1 ? "s" : ""} voted SELL · ${pct}`;
    return `${votes.length} agent votes`;
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

/** Convert grouped events to Lightweight Charts series markers (one per candle). */
export function toSeriesMarkers(groups: GroupedMarker[]): SeriesMarkerOut[] {
  return groups.map((g) => {
    const isFill = g.primary.startsWith("fill_");
    const isBuy = g.primary.endsWith("_buy") || g.items.some((i) => i.action === "BUY" && i.filled);
    let color = "#4da3ff";
    let shape: SeriesMarkerOut["shape"] = "arrowUp";
    let position: SeriesMarkerOut["position"] = "belowBar";
    let size = 1;

    if (g.primary === "fill_buy") {
      color = "#22c55e";
      shape = "arrowUp";
      position = "belowBar";
      size = 2.5;
    } else if (g.primary === "fill_sell") {
      color = "#ef4444";
      shape = "arrowDown";
      position = "aboveBar";
      size = 2.5;
    } else if (g.primary === "vote_buy") {
      color = "#4da3ff";
      shape = "arrowUp";
      position = "belowBar";
      size = 1;
    } else if (g.primary === "vote_sell") {
      color = "#c084fc";
      shape = "arrowDown";
      position = "aboveBar";
      size = 1;
    } else if (g.primary === "vote_hold") {
      color = "#9aa4b2";
      shape = "square";
      position = "inBar";
      size = 0.8;
    } else if (g.primary === "rejected") {
      color = "#f0b429";
      shape = "square";
      position = "aboveBar";
      size = 1;
    } else if (isFill && isBuy) {
      color = "#22c55e";
      shape = "arrowUp";
      position = "belowBar";
      size = 2.5;
    }

    const text = g.count > 1 ? `${g.count}· ${g.label}` : g.label;
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

export function eventsToMarkerItems(
  events: Array<Record<string, unknown>>,
  timeframe: string
): DecisionMarkerItem[] {
  const out: DecisionMarkerItem[] = [];
  for (const ev of events) {
    const eventType = String(ev.event_type || ev.eventType || "");
    const eventId = String(ev.event_id || ev.eventId || ev.id || "");
    const side = String(ev.side || "HOLD").toUpperCase();
    const ts = toUnixSeconds(Number(ev.ts || 0));
    const payload = (ev.payload || {}) as Record<string, unknown>;
    let kind: MarkerKind = "rejected";
    if (eventType === "fill") {
      kind = side === "SELL" ? "fill_sell" : "fill_buy";
    } else if (eventType === "agent_vote") {
      if (side === "BUY") kind = "vote_buy";
      else if (side === "SELL") kind = "vote_sell";
      else kind = "vote_hold";
    } else if (eventType === "decision") {
      if (String(ev.status || "") === "FILLED") {
        kind = side === "SELL" ? "fill_sell" : "fill_buy";
      } else if (side === "BUY") kind = "vote_buy";
      else if (side === "SELL") kind = "vote_sell";
      else kind = "rejected";
    }
    const qty = ev.quantity != null ? Number(ev.quantity) : null;
    const price = ev.price != null ? Number(ev.price) : null;
    out.push({
      id: String(ev.key || ev.id || markerKey(eventType, eventId)),
      eventType,
      eventId,
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
      filled: eventType === "fill" || String(ev.status || "") === "FILLED",
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

/** Merge by stable id without duplicates. */
export function mergeMarkerItems(
  prev: DecisionMarkerItem[],
  incoming: DecisionMarkerItem[]
): DecisionMarkerItem[] {
  const map = new Map<string, DecisionMarkerItem>();
  for (const p of prev) map.set(p.id, p);
  for (const n of incoming) map.set(n.id, n);
  return [...map.values()].sort((a, b) => a.timestamp - b.timestamp);
}
