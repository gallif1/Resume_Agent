/** Plain-text formatter for structured decision logs (mirrors backend decision_log.py).

Hardened so copy/export never throws on dirty payloads (string numbers, nested
objects in agent inputs, missing fields) and stays bounded in size.
*/

import type { DecisionLog } from "./api";

/** Soft cap so clipboard / React textarea do not OOM the tab. */
export const MAX_COPY_CHARS = 180_000;
export const MAX_COPY_ENTRIES = 50;

function fmtTs(ts?: number | null): string {
  if (ts == null || !Number.isFinite(Number(ts))) return "—";
  try {
    return new Date(Number(ts) * 1000)
      .toISOString()
      .replace("T", " ")
      .replace(/\.\d+Z$/, " UTC");
  } catch {
    return "—";
  }
}

function asFiniteNumber(v: unknown): number | null {
  if (v == null || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function pct(v: unknown): string {
  const n = asFiniteNumber(v);
  if (n == null) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function confPct(v: unknown): string {
  const n = asFiniteNumber(v);
  if (n == null) return "—";
  return `${(n * 100).toFixed(0)}%`;
}

function yesNo(v?: boolean | null): string {
  if (v === true) return "כן";
  if (v === false) return "לא";
  return "ממתין";
}

/** Compact scalar/object values for Inputs lines — never dump huge trees. */
export function formatInputValue(v: unknown, depth = 0): string {
  if (v == null) return "—";
  if (typeof v === "string") return v.length > 80 ? `${v.slice(0, 77)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (depth >= 1) {
    if (Array.isArray(v)) return `[${v.length}]`;
    if (typeof v === "object") return "{…}";
    return String(v);
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return "[]";
    const head = v
      .slice(0, 3)
      .map((x) => formatInputValue(x, depth + 1))
      .join(",");
    return v.length > 3 ? `[${head},…+${v.length - 3}]` : `[${head}]`;
  }
  if (typeof v === "object") {
    try {
      const keys = Object.keys(v as object);
      if (!keys.length) return "{}";
      const shown = keys
        .slice(0, 4)
        .map((k) => `${k}:${formatInputValue((v as Record<string, unknown>)[k], depth + 1)}`)
        .join(",");
      return keys.length > 4 ? `{${shown},…}` : `{${shown}}`;
    } catch {
      return "{…}";
    }
  }
  return String(v);
}

function formatOutcome(log: DecisionLog): string[] {
  const lines = ["תוצאה"];
  const outcome = log.outcome;
  if (!outcome) {
    lines.push("ממתין (no actionable outcome tracked)");
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
      lines.push(`${key}: ממתין`);
    }
  }
  return lines;
}

function formatOneLog(log: DecisionLog, idx: number): string {
  const market = log.market || {};
  const decision = log.decision || {};
  const execution = log.execution || {};
  const agents = Array.isArray(log.signal?.agents) ? log.signal!.agents! : [];
  const dbg = decision.confidence_debug || {};
  const weights = decision.weights || {};
  const counts = decision.vote_counts || {};
  const pretrade = log.pretrade || null;

  const lines: string[] = [
    `=== החלטה ${idx + 1} ===`,
    `Kind: ${log.kind ?? "—"}`,
    `Timestamp: ${fmtTs(log.timestamp)}`,
    `Symbol: ${log.symbol ?? "—"}`,
    "",
    "שוק",
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
    `Events: ${(Array.isArray(market.detected_events) ? market.detected_events : []).join(", ") || "—"}`,
    `Provider: ${market.provider ?? "—"}`,
    `Session/freshness: ${market.session ?? "—"}/${market.freshness ?? "—"}`,
    "",
    "סוכנים",
  ];

  for (const a of agents) {
    lines.push(`${a.agent_name || a.agent_id || "agent"}:`);
    lines.push(`Action: ${a.action ?? "—"}`);
    lines.push(
      a.confidence != null ? `Confidence: ${confPct(a.confidence)}` : "Confidence: —"
    );
    lines.push(`Reason: ${a.reason || "—"}`);
    if (a.agent_id === "ai_analyst" || a.source) {
      lines.push(`Source: ${a.source || "—"}`);
      lines.push(`Last analysis: ${fmtTs(a.ts)}`);
    }
    const inputs = a.inputs && typeof a.inputs === "object" ? a.inputs : {};
    const keys = Object.keys(inputs);
    if (keys.length) {
      // Prefer compact used-for-decision keys; skip bulky informational blobs.
      const prefer = keys.filter(
        (k) =>
          k !== "INFORMATIONAL_ONLY" &&
          k !== "market_snapshot_keys" &&
          k !== "chart_indicators_informational" &&
          k !== "timeframe_signals" &&
          k !== "portfolio_risk" &&
          k !== "human_levels" &&
          k !== "data_quality"
      );
      const useKeys = (prefer.length ? prefer : keys).slice(0, 8);
      lines.push(
        `Inputs: ${useKeys.map((k) => `${k}=${formatInputValue(inputs[k])}`).join(", ")}`
      );
    }
    lines.push("");
  }

  if (pretrade) {
    lines.push(
      "AI לפני עסקה",
      `Source: ${pretrade.source ?? "—"}`,
      `Skip reason: ${pretrade.skip_reason ?? "—"}`,
      `Action: ${pretrade.action ?? "—"}`,
      `Confidence: ${pretrade.confidence ?? "—"}`,
      ""
    );
  }

  lines.push(
    "מנוע החלטות",
    `Votes: BUY ${counts.BUY ?? 0} / SELL ${counts.SELL ?? 0} / HOLD ${counts.HOLD ?? 0}`,
    `Weighted scores: BUY=${weights.BUY ?? 0} SELL=${weights.SELL ?? 0} HOLD=${weights.HOLD ?? 0}`,
    `Threshold (min_confidence): ${decision.threshold ?? "—"}`,
    `Action score: ${decision.action_score ?? "—"}`,
    `Hold gate (hold*0.85): ${decision.hold_gate ?? "—"}`,
    `Final action: ${decision.action ?? "—"}`,
    `Final confidence: ${confPct(decision.final_confidence ?? 0)}`,
    `Explanation: ${decision.explanation || decision.rationale || "—"}`,
    "",
    "ניפוי ביטחון",
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
    "ביצוע",
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
  return lines.join("\n");
}

export function formatDecisionLogsText(logs: DecisionLog[], limit?: number): string {
  const safeLogs = Array.isArray(logs) ? logs : [];
  const requested = limit ?? safeLogs.length;
  const capped = Math.min(
    Math.max(0, requested),
    MAX_COPY_ENTRIES,
    safeLogs.length
  );
  const rows = safeLogs.slice(0, capped);
  const chunks: string[] = [];

  for (let idx = 0; idx < rows.length; idx++) {
    try {
      chunks.push(formatOneLog(rows[idx], idx));
    } catch {
      chunks.push(
        `=== החלטה ${idx + 1} ===\n(דילוג — רשומה פגומה)\n${"=".repeat(32)}\n`
      );
    }
  }

  const truncatedEntries = safeLogs.length > capped;
  const header = [
    "מערכת מסחר AI — יומני החלטות",
    `Exported: ${fmtTs(Date.now() / 1000)}`,
    `Entries: ${rows.length}${truncatedEntries ? ` (מתוך ${safeLogs.length}, מוגבל ל-${MAX_COPY_ENTRIES})` : ""}`,
    "",
  ].join("\n");

  let text = header + chunks.join("\n");
  if (text.length > MAX_COPY_CHARS) {
    text =
      text.slice(0, MAX_COPY_CHARS) +
      `\n\n… נחתך ב-${MAX_COPY_CHARS.toLocaleString()} תווים כדי למנוע קריסת דפדפן.\n`;
  }
  return text;
}
