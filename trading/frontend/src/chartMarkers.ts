/** Group decision markers that fall on the same candle bucket. */

export type MarkerKind = "vote_buy" | "vote_sell" | "fill_buy" | "fill_sell" | "rejected";

export type DecisionMarkerItem = {
  id: string;
  kind: MarkerKind;
  agentName?: string;
  action: string;
  confidence?: number;
  reason?: string;
  timestamp: number;
  finalDecision?: string;
  filled?: boolean;
  skipReason?: string;
  price?: number | null;
};

export type GroupedMarker = {
  time: number;
  count: number;
  kinds: MarkerKind[];
  items: DecisionMarkerItem[];
  primary: MarkerKind;
};

function bucketTs(ts: number, timeframe: string): number {
  const secs: Record<string, number> = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
  };
  const s = secs[timeframe] || 300;
  return Math.floor(ts / s) * s;
}

export function groupMarkersOnCandle(
  items: DecisionMarkerItem[],
  timeframe: string
): GroupedMarker[] {
  const map = new Map<number, DecisionMarkerItem[]>();
  for (const it of items) {
    const t = bucketTs(it.timestamp, timeframe);
    const arr = map.get(t) || [];
    arr.push(it);
    map.set(t, arr);
  }
  const out: GroupedMarker[] = [];
  for (const [time, group] of map) {
    const kinds = [...new Set(group.map((g) => g.kind))];
    const primary =
      kinds.find((k) => k.startsWith("fill_")) ||
      kinds.find((k) => k.startsWith("vote_")) ||
      kinds[0];
    out.push({ time, count: group.length, kinds, items: group, primary });
  }
  return out.sort((a, b) => a.time - b.time);
}
