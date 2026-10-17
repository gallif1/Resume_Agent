/**
 * Self-test: decision log copy formatter must not throw / OOM on dirty data.
 * Run: npx tsx scripts/decisionLogCopy.selftest.ts
 */
import {
  formatDecisionLogsText,
  formatInputValue,
  MAX_COPY_CHARS,
  MAX_COPY_ENTRIES,
} from "../src/decisionLogFormat";
import type { DecisionLog } from "../src/api";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

// pct / toFixed crash regression: string numbers must not throw.
const dirty: DecisionLog = {
  id: "x1",
  timestamp: 1_700_000_000,
  symbol: "SOL-USD",
  kind: "NEW_DECISION",
  signal: {
    action: "BUY",
    agents: [
      {
        agent_id: "momentum",
        agent_name: "Momentum",
        action: "BUY",
        confidence: "0.78" as unknown as number,
        reason: "up",
        inputs: {
          USED_FOR_DECISION: { rsi_14: 61.4, nested: { a: 1, b: [1, 2, 3] } },
          INFORMATIONAL_ONLY: {
            chart_indicators_informational: { rsi_14: 61 },
            huge: new Array(5000).fill({ x: 1 }),
          },
          data_quality: { stale: false },
          score: 0.56,
        },
      },
    ],
  },
  market: {
    price: 142,
    change_1m_pct: "0.3" as unknown as number,
    change_5m_pct: "bad" as unknown as number,
    change_15m_pct: null,
    rsi_14: 63,
    detected_events: ["volume_surge"],
  },
  decision: {
    action: "BUY",
    final_confidence: "0.71" as unknown as number,
    vote_counts: { BUY: 2, SELL: 0, HOLD: 1 },
    weights: { BUY: 1.5, SELL: 0, HOLD: 0.4 },
    confidence_debug: { formula: "x", final_confidence: 0.71 },
  },
  execution: { status: "BLOCKED", reason: "MONITOR_ONLY" },
};

let text = "";
try {
  text = formatDecisionLogsText([dirty], 1);
} catch (e) {
  throw new Error(`format threw on dirty log: ${e}`);
}
assert(text.includes("SOL-USD"), "includes symbol");
assert(text.includes("Momentum"), "includes agent");
assert(!text.includes("huge"), "skips informational blob key content dump");
assert(formatInputValue({ a: 1, b: { c: 2 } }).includes("a:"), "compact object");
assert(formatInputValue(new Array(20).fill(1)).includes("+"), "array truncated");

// Size cap: many logs must stay under MAX_COPY_CHARS and entry cap.
const many: DecisionLog[] = Array.from({ length: 120 }, (_, i) => ({
  ...dirty,
  id: `id-${i}`,
  symbol: i % 2 ? "BTC-USD" : "SOL-USD",
}));
const big = formatDecisionLogsText(many, 999);
assert(big.length <= MAX_COPY_CHARS + 200, `length ${big.length} exceeds soft cap`);
assert(big.includes(`מוגבל ל-${MAX_COPY_ENTRIES}`) || big.includes("Entries:"), "header present");

// Empty / null-safe
assert(formatDecisionLogsText([], 10).includes("Entries: 0"), "empty ok");

console.log("decisionLogCopy.selftest: OK");
