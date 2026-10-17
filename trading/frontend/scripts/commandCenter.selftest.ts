/**
 * Command Center helper self-test.
 * Run: npx tsx scripts/commandCenter.selftest.ts
 */
import type { UnifiedDecision } from "../src/api";
import {
  agentEvidenceLine,
  agentExplanation,
  buildAgentTimeline,
  computeAgentInsights,
  DEFAULT_CC_FILTERS,
  filterMarkerItems,
  filterUnifiedDecisions,
  latestDecisionForSymbol,
  voteBreakdown,
} from "../src/commandCenter";
import {
  kindFromUnified,
  markerLabelFromDecision,
  toSeriesMarkers,
  groupMarkersOnCandle,
  unifiedToMarkerItems,
} from "../src/chartMarkers";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const ts = 1_725_000_000;
const rows: UnifiedDecision[] = [
  {
    decision_id: "d1",
    symbol: "SOL-USD",
    decision_time: ts,
    final_action: "BUY",
    confidence: 0.7,
    status: "FILLED",
    agreement: { supporting: 2, opposing: 1, total: 3 },
    agent_votes: [
      {
        agent_id: "momentum",
        agent_name: "מומנטום",
        side: "BUY",
        confidence: 0.8,
        rationale: "EMA cross",
        components: { rsi: 0.4, macd: 0.3 },
        used_for_decision: { rsi_14: 31.4 },
      },
      {
        agent_id: "mean_reversion",
        agent_name: "ממוצע",
        side: "BUY",
        confidence: 0.6,
        rationale: "",
      },
      {
        agent_id: "volatility",
        agent_name: "תנודתיות",
        side: "HOLD",
        confidence: 0.5,
        rationale: "calm",
      },
    ],
  },
  {
    decision_id: "d2",
    symbol: "SOL-USD",
    decision_time: ts + 60,
    final_action: "BUY",
    confidence: 0.55,
    status: "BLOCKED",
    block_reason: "cooldown",
    agreement: { supporting: 1, opposing: 2, total: 3 },
    agent_votes: [
      { agent_id: "momentum", agent_name: "מומנטום", side: "BUY", confidence: 0.7 },
      { agent_id: "mean_reversion", agent_name: "ממוצע", side: "SELL", confidence: 0.6 },
      { agent_id: "volatility", agent_name: "תנודתיות", side: "HOLD", confidence: 0.4 },
    ],
  },
  {
    decision_id: "d3",
    symbol: "SOL-USD",
    decision_time: ts + 120,
    final_action: "HOLD",
    confidence: 0.4,
    status: "HOLD",
    agent_votes: [
      { agent_id: "momentum", agent_name: "מומנטום", side: "HOLD", confidence: 0.4 },
    ],
  },
];

assert(kindFromUnified(rows[0]) === "fill_buy", "fill kind");
assert(kindFromUnified(rows[1]) === "blocked", "blocked kind");
assert(markerLabelFromDecision(rows[0]).includes("BUY"), "buy label");
assert(markerLabelFromDecision(rows[0]).includes("2/3"), "consensus in label");
assert(markerLabelFromDecision(rows[1]).startsWith("BLOCKED"), "blocked label");
assert(markerLabelFromDecision(rows[2]) === "SIGNAL · HOLD", "hold label");

const vb = voteBreakdown(rows[0]);
assert(vb.buy === 2 && vb.hold === 1 && vb.total === 3, "vote breakdown");
assert(vb.supporting === 2, "supporting");

const evidence = agentEvidenceLine(rows[0].agent_votes![0]);
assert(evidence?.includes("rsi_14"), "evidence from used_for_decision");
assert(agentExplanation(rows[0].agent_votes![1]).includes("לא זמין") || agentExplanation(rows[0].agent_votes![1]).includes("unavailable"), "empty rationale");

const filtered = filterUnifiedDecisions(rows, {
  ...DEFAULT_CC_FILTERS,
  showHold: false,
  showBlocked: true,
  showExecuted: true,
  showSignals: true,
});
assert(filtered.every((u) => u.status !== "HOLD"), "hold filtered");
assert(filtered.some((u) => u.status === "BLOCKED"), "blocked kept");

const agentOnly = filterUnifiedDecisions(rows, {
  ...DEFAULT_CC_FILTERS,
  agentId: "volatility",
  showHold: true,
});
assert(agentOnly.length === 2, "agent filter");

const items = unifiedToMarkerItems(rows, "1m");
const markers = filterMarkerItems(items, { ...DEFAULT_CC_FILTERS, showHold: false });
assert(markers.every((m) => m.kind !== "hold"), "marker hold filter");

const groups = groupMarkersOnCandle(markers, "1m");
const series = toSeriesMarkers(groups);
assert(series.some((s) => s.color === "#22c55e"), "green fill");
assert(series.some((s) => s.text.includes("BUY") || s.text.includes("BLOCKED") || s.text.includes("FILL")), "label text");

const { agents, cells } = buildAgentTimeline(rows, 60);
assert(agents.length === 3, "3 agents discovered");
assert(cells.length >= 5, "timeline cells");
assert(new Set(cells.map((c) => `${c.decisionId}:${c.agentId}`)).size === cells.length, "no dup cells");

const insights = computeAgentInsights(rows);
const mom = insights.find((i) => i.id === "momentum");
assert(mom && mom.total === 3, "momentum insights");
assert(mom!.agreementRate != null && mom!.agreementRate > 0, "agreement rate");

assert(latestDecisionForSymbol(rows, "SOL-USD")?.decision_id === "d3", "latest");

console.log("commandCenter.selftest: ok");
