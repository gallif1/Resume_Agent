/** Persistent live system decision summary above the chart. */
import type { UnifiedDecision } from "./api";
import {
  executionLabel,
  fmtConfidence,
  riskLabel,
  sideClass,
  voteBreakdown,
} from "./commandCenter";
import { he } from "./i18n/he";
import { toUnixSeconds } from "./chartMarkers";

type Props = {
  symbol: string;
  decision: UnifiedDecision | null;
  replayActive?: boolean;
};

export default function LiveDecisionBar({ symbol, decision, replayActive }: Props) {
  const vb = voteBreakdown(decision);
  const action = String(decision?.final_action || "—").toUpperCase();
  const conf = fmtConfidence(decision?.confidence);
  const exec = executionLabel(decision);
  const risk = riskLabel(decision);
  const ts =
    decision?.decision_time != null
      ? new Date(toUnixSeconds(Number(decision.decision_time)) * 1000).toLocaleString()
      : "—";

  return (
    <div className="live-decision-bar" dir="rtl" role="status" aria-live="polite">
      <div className="ldb-asset" dir="ltr">
        {symbol}
        {replayActive ? <span className="ldb-replay-pill">{he.ccReplayMode}</span> : null}
      </div>
      <div className="ldb-cell">
        <span className="ldb-label">{he.ccSystem}</span>
        <span className={`ldb-value tag ${sideClass(action)}`}>{action}</span>
        {conf ? <span className="ldb-muted mono">{conf}</span> : null}
      </div>
      <div className="ldb-cell">
        <span className="ldb-label">{he.ccConsensus}</span>
        <span className="ldb-value mono" dir="ltr">
          {vb.total > 0 ? `${vb.supporting} / ${vb.total}` : "—"}
        </span>
        <span className="ldb-muted">
          {vb.total > 0
            ? `${he.ccAgents} · B${vb.buy}/S${vb.sell}/H${vb.hold}`
            : he.ccNoVotes}
        </span>
      </div>
      <div className="ldb-cell">
        <span className="ldb-label">{he.ccRisk}</span>
        <span
          className={`ldb-value ${
            risk === "BLOCKED" ? "warn" : risk === "APPROVED" ? "ok" : ""
          }`}
        >
          {risk}
        </span>
        {decision?.block_reason ? (
          <span className="ldb-muted" title={decision.block_reason}>
            {String(decision.block_reason).slice(0, 40)}
          </span>
        ) : null}
      </div>
      <div className="ldb-cell">
        <span className="ldb-label">{he.ccExecution}</span>
        <span
          className={`ldb-value ${
            exec === "FILLED" ? "ok" : exec === "BLOCKED" ? "warn" : ""
          }`}
        >
          {exec}
        </span>
      </div>
      <div className="ldb-cell ldb-ts">
        <span className="ldb-label">{he.ccLastDecision}</span>
        <span className="ldb-muted mono" dir="ltr">
          {ts}
        </span>
      </div>
    </div>
  );
}
