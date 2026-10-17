/** Compact per-agent cards for the selected decision. */
import type { UnifiedAgentVote } from "./api";
import {
  agentEvidenceLine,
  agentExplanation,
  fmtConfidence,
  sideClass,
} from "./commandCenter";
import { he } from "./i18n/he";
import { toUnixSeconds } from "./chartMarkers";

type Props = {
  votes: UnifiedAgentVote[];
  finalAction?: string;
  selectedAgentId?: string | null;
  onSelectAgent?: (agentId: string) => void;
};

export default function AgentDecisionCards({
  votes,
  finalAction,
  selectedAgentId,
  onSelectAgent,
}: Props) {
  if (!votes.length) {
    return <p className="muted">{he.ccNoAgentVotes}</p>;
  }
  const final = String(finalAction || "").toUpperCase();

  return (
    <div className="agent-decision-cards">
      {votes.map((v, i) => {
        const id = String(v.agent_id || v.agent_name || i);
        const side = String(v.side || "HOLD").toUpperCase();
        const conf = fmtConfidence(v.confidence);
        const evidence = agentEvidenceLine(v);
        const reason = agentExplanation(v);
        const agree =
          side === final ? "support" : side === "HOLD" ? "hold" : "oppose";
        const selected = selectedAgentId === id;
        const ts =
          v.ts != null
            ? new Date(toUnixSeconds(Number(v.ts)) * 1000).toLocaleString()
            : null;
        return (
          <button
            key={`${id}-${i}`}
            type="button"
            className={`adc-card ${agree}${selected ? " selected" : ""}`}
            onClick={() => onSelectAgent?.(id)}
          >
            <div className="adc-head">
              <strong>{v.agent_name || v.agent_id || "—"}</strong>
              <span className={`tag ${sideClass(side)}`}>{side}</span>
            </div>
            <div className="adc-meta">
              {conf ? (
                <span className="mono">
                  {he.confidence} {conf}
                </span>
              ) : (
                <span className="muted">{he.ccConfidenceUnavailable}</span>
              )}
              {ts ? (
                <span className="muted mono" dir="ltr">
                  {ts}
                </span>
              ) : null}
            </div>
            {evidence ? (
              <div className="adc-evidence mono" dir="ltr">
                {evidence}
              </div>
            ) : null}
            <p className="adc-reason">{reason}</p>
            {v.components && Object.keys(v.components).length ? (
              <details className="adc-advanced">
                <summary>{he.ccAdvanced}</summary>
                <ul className="adc-comps mono" dir="ltr">
                  {Object.entries(v.components).map(([k, val]) => (
                    <li key={k}>
                      {k}:{" "}
                      {typeof val === "number" ? Number(val).toFixed(4) : String(val)}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
