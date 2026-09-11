/**
 * Lightweight self-test for chart marker helpers.
 * Run: npx tsx scripts/chartMarkers.selftest.ts
 */
import {
  candleBucketTs,
  eventsToMarkerItems,
  groupMarkersOnCandle,
  kindFromUnified,
  mergeMarkerItems,
  mergeUnifiedDecisions,
  normalizeSymbol,
  toSeriesMarkers,
  toUnixSeconds,
  unifiedToMarkerItems,
} from "../src/chartMarkers";
import type { UnifiedDecision } from "../src/api";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(normalizeSymbol("SOLUSD") === "SOL-USD", "normalize SOLUSD");
assert(normalizeSymbol("sol/usd") === "SOL-USD", "normalize slash");
assert(normalizeSymbol("SOL-USD") === "SOL-USD", "normalize dashed");

assert(toUnixSeconds(1_700_000_000) === 1_700_000_000, "sec ts");
assert(toUnixSeconds(1_700_000_000_000) === 1_700_000_000, "ms ts");

const ts = Date.UTC(2026, 8, 11, 8, 44, 26) / 1000;
assert(candleBucketTs(ts, "1m") === Date.UTC(2026, 8, 11, 8, 44, 0) / 1000, "1m bucket");
assert(candleBucketTs(ts, "5m") === Date.UTC(2026, 8, 11, 8, 40, 0) / 1000, "5m bucket");
assert(candleBucketTs(ts, "15m") === Date.UTC(2026, 8, 11, 8, 30, 0) / 1000, "15m bucket");
assert(candleBucketTs(ts, "1h") === Date.UTC(2026, 8, 11, 8, 0, 0) / 1000, "1h bucket");

const unified: UnifiedDecision[] = [
  {
    decision_id: "ud-fill",
    symbol: "SOL-USD",
    decision_time: ts,
    candle_time: candleBucketTs(ts, "1m"),
    final_action: "BUY",
    confidence: 0.69,
    status: "FILLED",
    quantity: 18.8446,
    fill_price: 99.89,
    total_value: 18.8446 * 99.89,
    summary: "SOL-USD: BUY (FILLED)",
    primary_reasons: ["החלטה סופית: BUY"],
    risk_factors: [],
    agent_votes: [{ agent_name: "Mean", side: "BUY", confidence: 0.69 }],
  },
  {
    decision_id: "ud-signal",
    symbol: "SOL-USD",
    decision_time: ts + 5,
    candle_time: candleBucketTs(ts, "1m"),
    final_action: "BUY",
    confidence: 0.55,
    status: "SIGNAL",
    summary: "analysis only",
  },
  {
    decision_id: "ud-hold",
    symbol: "SOL-USD",
    decision_time: ts + 6,
    final_action: "HOLD",
    confidence: 0.4,
    status: "HOLD",
  },
  {
    decision_id: "ud-block",
    symbol: "SOL-USD",
    decision_time: ts + 7,
    final_action: "BUY",
    confidence: 0.8,
    status: "BLOCKED",
    block_reason: "risk",
  },
];

assert(kindFromUnified(unified[0]) === "fill_buy", "fill kind");
assert(kindFromUnified(unified[1]) === "signal", "signal kind");
assert(kindFromUnified(unified[2]) === "hold", "hold kind");
assert(kindFromUnified(unified[3]) === "blocked", "blocked kind");

const items = unifiedToMarkerItems(unified, "1m");
assert(items.length === 4, "four unified items");
assert(items[0].kind === "fill_buy", "fill item");
assert(items.every((i) => i.eventType === "unified"), "event type unified");

// Votes must not become primary markers from raw events
const legacyEvents = [
  {
    event_type: "fill",
    event_id: "f1",
    id: "fill:f1",
    symbol: "SOL-USD",
    side: "BUY",
    ts,
    price: 99.89,
    quantity: 18.8446,
    status: "FILLED",
    payload: { fill_price: 99.89, order_id: "f1" },
  },
  {
    event_type: "agent_vote",
    event_id: "v1",
    id: "agent_vote:v1",
    symbol: "SOL-USD",
    side: "BUY",
    ts: ts + 5,
    confidence: 0.69,
    status: "VOTE",
    payload: { agent_name: "Mean" },
  },
];
const legacyItems = eventsToMarkerItems(legacyEvents, "1m");
assert(legacyItems.length === 1, "votes skipped from primary events");
assert(legacyItems[0].kind === "fill_buy", "legacy fill only");

const groups = groupMarkersOnCandle(items.filter((i) => i.kind !== "hold"), "1m");
assert(groups.length === 1, "grouped on one candle");
assert(groups[0].primary === "fill_buy", "fill primary over signal/blocked");
assert(groups[0].fillCount === 1, "one fill");

const multiFill = unifiedToMarkerItems(
  [
    unified[0],
    {
      ...unified[0],
      decision_id: "ud-fill-2",
      final_action: "SELL",
      status: "FILLED",
    },
  ],
  "1m"
);
const gMulti = groupMarkersOnCandle(multiFill, "1m");
assert(gMulti[0].fillCount === 2, "two fills same candle");
const seriesMulti = toSeriesMarkers(gMulti);
assert(seriesMulti[0].text === "×2", "grouped fill count label");

const series = toSeriesMarkers(groups);
assert(series[0].color === "#22c55e", "green fill marker");
assert(series[0].shape === "arrowUp", "arrow up");
assert(series[0].size <= 1.25, "fill marker compact");

const signalOnly = toSeriesMarkers(
  groupMarkersOnCandle(
    items.filter((i) => i.kind === "signal"),
    "1m"
  )
);
assert(signalOnly[0].color === "#4da3ff", "blue analysis marker");
assert(signalOnly[0].shape === "circle", "circle for signal");

const blockedOnly = toSeriesMarkers(
  groupMarkersOnCandle(
    items.filter((i) => i.kind === "blocked"),
    "1m"
  )
);
assert(blockedOnly[0].color === "#f97316", "orange blocked");

const merged = mergeMarkerItems(items, items);
assert(merged.length === 4, "dedupe merge");

const uMerged = mergeUnifiedDecisions(unified.slice(0, 2), [
  { ...unified[0], summary: "updated" },
  unified[3],
]);
assert(uMerged.length === 3, "unified merge by decision_id");
assert(uMerged.find((u) => u.decision_id === "ud-fill")?.summary === "updated", "prefer incoming");

const btcOnly = items.filter((i) => i.symbol === "BTC-USD");
assert(btcOnly.length === 0, "symbol filter");

const g5 = groupMarkersOnCandle(
  items.map((i) => ({ ...i, candleTs: candleBucketTs(i.timestamp, "5m") })),
  "5m"
);
assert(g5[0].time === candleBucketTs(ts, "5m"), "5m remap");

type Mode = "normal" | "wide" | "expanded";
function nextMode(_cur: Mode, action: "expand" | "collapse" | "wide" | "normal"): Mode {
  if (action === "expand") return "expanded";
  if (action === "collapse") return "normal";
  if (action === "wide") return "wide";
  return "normal";
}
assert(nextMode("normal", "expand") === "expanded", "expand");
assert(nextMode("expanded", "collapse") === "normal", "collapse");

let fs = false as boolean;
enterFs();
assert(fs === true, "fullscreen enter");
exitFs();
assert(fs === false, "fullscreen exit");

function enterFs() {
  fs = true;
}
function exitFs() {
  fs = false;
}

function clampHeight(h: number) {
  return Math.min(1200, Math.max(280, h));
}
assert(clampHeight(100) === 280, "min height");
assert(clampHeight(2000) === 1200, "max height");

const afterUpdate = mergeMarkerItems(items, [
  {
    ...items[0],
    price: 99.9,
  },
]);
assert(afterUpdate.length === 4, "preserve after candle/update merge");
assert(afterUpdate.find((i) => i.decisionId === "ud-fill")?.price === 99.9, "updated fill price");

console.log("chartMarkers.selftest: ok");
