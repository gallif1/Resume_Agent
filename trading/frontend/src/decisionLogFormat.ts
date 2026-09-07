/** Plain-text formatter for structured decision logs (mirrors backend decision_log.py). */

import type { DecisionLog } from "./api";

function fmtTs(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
}

function pct(v?: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function yesNo(v?: boolean | null): string {
  if (v === true) return "YES";
  if (v === false) return "NO";
  return "PENDING";
}

function formatOutcome(log: DecisionLog): string[] {
  const lines = ["OUTCOME"];
  const outcome = log.outcome;
  if (!outcome) {
    lines.push("PENDING (no actionable outcome tracked)");
    return lines;
  }
  lines.push(`Entry price: ${outcome.entry_price ?? "—"}`);
  const horizons = outcome.horizons || {};
  for (const key of ["5m", "15m", "60m"] as const) {
    const h = horizons[key] || {};
    const status = h.status || "pending";
    if (status === "resolved") {
      lines.push(`${key} price: ${h.price ?? "—"}`);
      lines.push(`${key} return: ${pct(h.return_pct)}`);
      lines.push(`${key} direction correct: ${yesNo(h.direction_correct)}`);
    } else if (status === "unavailable" || status === "no_history") {
      lines.push(`${key}: ${status.toUpperCase()} (price not recoverable)`);
    } else {
      lines.push(`${key}: PENDING`);
    }
  }
  return lines;
}

export function formatDecisionLogsText(logs: DecisionLog[], limit?: number): string {
  const rows = logs.slice(0, limit ?? logs.length);
  const chunks: string[] = [];
  rows.forEach((log, idx) => {
    const market = log.market || {};
    const decision = log.decision || {};
    const execution = log.execution || {};
    const agents = log.signal?.agents || [];
    const dbg = decision.confidence_debug || {};
    const weights = decision.weights || {};
    const counts = decision.vote_counts || {};
    const pretrade = log.pretrade || null;

    const lines: string[] = [
      `=== DECISION ${idx + 1} ===`,
      `Kind: ${log.kind}`,
      `Timestamp: ${fmtTs(log.timestamp)}`,
      `Symbol: ${log.symbol}`,
      "",
      "MARKET",
      `Price: ${market.price ?? "—"}`,
      `1m: ${pct(market.change_1m_pct)}`,
      `5m: ${pct(market.change_5m_pct)}`,
      `15m: ${pct(market.change_15m_pct)}`,
      `RSI: ${market.rsi_14 ?? "—"}`,
      `SMA fast: ${market.sma_fast ?? "—"}`,
      `SMA slow: ${market.sma_slow ?? "—"}`,
      `EMA fast: ${market.ema_fast ?? "—"}`,
      `Trend: ${market.trend ?? "—"}`,
      `Volume state: ${market.volume_state ?? "—"}`,
      `Volatility: ${market.volatility ?? "—"}`,
      `Events: ${(market.detected_events || []).join(", ") || "—"}`,
      `Provider: ${market.provider ?? "—"}`,
      `Session/freshness: ${market.session ?? "—"}/${market.freshness ?? "—"}`,
      "",
      "AGENTS",
    ];

    for (const a of agents) {
      lines.push(`${a.agent_name || a.agent_id}:`);
      lines.push(`Action: ${a.action}`);
      lines.push(
        a.confidence != null ? `Confidence: ${(a.confidence * 100).toFixed(0)}%` : "Confidence: —"
      );
      lines.push(`Reason: ${a.reason || "—"}`);
      if (a.agent_id === "ai_analyst" || a.source) {
        lines.push(`Source: ${a.source || "—"}`);
        lines.push(`Last analysis: ${fmtTs(a.ts)}`);
      }
      const inputs = a.inputs || {};
      const keys = Object.keys(inputs);
      if (keys.length) {
        lines.push(`Inputs: ${keys.slice(0, 8).map((k) => `${k}=${inputs[k]}`).join(", ")}`);
      }
      lines.push("");
    }

    if (pretrade) {
      lines.push(
        "AI PRETRADE",
        `Source: ${pretrade.source ?? "—"}`,
        `Skip reason: ${pretrade.skip_reason ?? "—"}`,
        `Action: ${pretrade.action ?? "—"}`,
        `Confidence: ${pretrade.confidence ?? "—"}`,
        ""
      );
    }

    lines.push(
      "DECISION ENGINE",
      `Votes: BUY ${counts.BUY ?? 0} / SELL ${counts.SELL ?? 0} / HOLD ${counts.HOLD ?? 0}`,
      `Weighted scores: BUY=${weights.BUY ?? 0} SELL=${weights.SELL ?? 0} HOLD=${weights.HOLD ?? 0}`,
      `Threshold (min_confidence): ${decision.threshold ?? "—"}`,
      `Action score: ${decision.action_score ?? "—"}`,
      `Hold gate (hold*0.85): ${decision.hold_gate ?? "—"}`,
      `Final action: ${decision.action ?? "—"}`,
      `Final confidence: ${((decision.final_confidence ?? 0) * 100).toFixed(0)}%`,
      `Explanation: ${decision.explanation || decision.rationale || "—"}`,
      "",
      "CONFIDENCE DEBUG",
      `winning_action = ${dbg.winning_action ?? "—"}`,
      `winning_score = ${dbg.winning_score ?? dbg.raw_score ?? "—"}`,
      `total_weight = ${dbg.total_weight ?? dbg.total_all_weights ?? "—"}`,
      `action_support = ${dbg.action_support ?? "—"}`,
      `agreement_factor = ${dbg.agreement_factor ?? "—"}`,
      `hold_ratio = ${dbg.hold_ratio ?? "—"}`,
      `opposition_ratio = ${dbg.opposition_ratio ?? "—"}`,
      `final_confidence = ${dbg.final_confidence ?? "—"}`,
      `formula = ${dbg.formula ?? "—"}`,
      `note: ${dbg.note ?? "—"}`,
      "",
      "EXECUTION",
      `Status: ${execution.status ?? "—"}`,
      `Reason: ${execution.reason ?? "—"}`
    );
    if (execution.cooldown_remaining_sec != null) {
      lines.push(`Cooldown remaining: ${execution.cooldown_remaining_sec}s`);
    }
    if (execution.status === "FILLED") {
      lines.push(`Fill price: ${execution.fill_price ?? "—"}`);
      lines.push(`Quantity: ${execution.quantity ?? "—"}`);
    }
    lines.push("", ...formatOutcome(log), "", "=".repeat(32), "");
    chunks.push(lines.join("\n"));
  });

  const header = [
    "AI Trading System — decision logs",
    `Exported: ${fmtTs(Date.now() / 1000)}`,
    `Entries: ${rows.length}`,
    "",
  ].join("\n");
  return header + chunks.join("\n");
}
