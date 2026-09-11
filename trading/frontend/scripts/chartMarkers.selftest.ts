/**
 * Lightweight self-test for chart marker helpers.
 * Run: npx tsx scripts/chartMarkers.selftest.ts
 */
import {
  candleBucketTs,
  eventsToMarkerItems,
  groupMarkersOnCandle,
  mergeMarkerItems,
  normalizeSymbol,
  toSeriesMarkers,
  toUnixSeconds,
} from "../src/chartMarkers";

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

const events = [
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

const items = eventsToMarkerItems(events, "1m");
assert(items.length === 2, "two items");
assert(items[0].kind === "fill_buy", "fill kind");
assert(items[1].kind === "vote_buy", "vote kind");

const groups = groupMarkersOnCandle(items, "1m");
assert(groups.length === 1, "grouped on one candle");
assert(groups[0].count === 2, "group count");
assert(groups[0].primary === "fill_buy", "fill primary");

const series = toSeriesMarkers(groups);
assert(series[0].color === "#22c55e", "green fill marker");
assert(series[0].shape === "arrowUp", "arrow up");
assert(series[0].size >= 2, "fill more prominent");

const merged = mergeMarkerItems(items, items);
assert(merged.length === 2, "dedupe merge");

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

// Preserve markers after candle update: merge keeps prior ids
const afterUpdate = mergeMarkerItems(items, [
  {
    ...items[0],
    price: 99.9,
  },
]);
assert(afterUpdate.length === 2, "preserve after candle/update merge");
assert(afterUpdate[0].price === 99.9, "updated fill price");

console.log("chartMarkers.selftest: ok");
