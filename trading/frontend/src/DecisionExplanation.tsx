/** Structured decision explanation panel — five observability questions. */
import type { ReactNode } from "react";
import type { DecisionLog, UnifiedDecision } from "./api";
import AgentDecisionCards from "./AgentDecisionCards";
import {
  EXPLANATION_UNAVAILABLE_HE,
  findDecisionLog,
  fmtConfidence,
  riskLabel,
  voteBreakdown,
} from "./commandCenter";
import { he } from "./i18n/he";
import { toUnixSeconds } from "./chartMarkers";

type Props = {
  decision: UnifiedDecision;
  decisionLogs?: DecisionLog[];
  onCopy?: () => void;
  copyMsg?: string | null;
};

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="dx-section">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

export default function DecisionExplanation({
  decision,
  decisionLogs,
  onCopy,
  copyMsg,
}: Props) {
  const log = findDecisionLog(decisionLogs, decision.decision_id);
  const vb = voteBreakdown(decision);
  const engine = decision.engine || log?.decision || {};
  const exec = decision.execution || {};
  const status = String(decision.status || "").toUpperCase();

  const consensusText =
    vb.total > 0
      ? `${vb.supporting}/${vb.total} ${he.ccAgents} (${he.ccBuy}: ${vb.buy}, ${he.ccSell}: ${vb.sell}, HOLD: ${vb.hold})`
      : he.ccNoVotes;

  const deExplanation =
    (typeof engine === "object" &&
      engine &&
      ("explanation" in engine
        ? String((engine as { explanation?: string }).explanation || "")
        : "")) ||
    log?.decision?.explanation ||
    decision.summary ||
    "";

  const riskReason =
    decision.block_reason ||
    (typeof exec.reason === "string" ? exec.reason : "") ||
    (decision.risk_factors || [])[0] ||
    "";

  let execResult = "";
  if (status === "FILLED") {
    const px =
      decision.fill_price != null ? Number(decision.fill_price).toFixed(4) : "—";
    const qty =
      decision.quantity != null ? Number(decision.quantity).toFixed(4) : "—";
    execResult = `${he.filled}: ${decision.final_action} ${qty} @ ${px}`;
  } else if (status === "BLOCKED" || status === "REJECTED") {
    execResult = `${he.ccBlocked}: ${riskReason || EXPLANATION_UNAVAILABLE_HE}`;
  } else if (status === "HOLD") {
    execResult = he.ccNoTradeHold;
  } else {
    execResult = `${he.ccSignalOnly}: ${decision.final_action}`;
  }

  return (
    <div className="decision-explanation" dir="rtl">
      <div className="dx-banner">
        <span className={`tag ${String(decision.final_action || "").toLowerCase()}`}>
          {decision.final_action}
        </span>
        <span className="dx-status">{status}</span>
        <span className="mono muted" dir="ltr">
          {new Date(toUnixSeconds(Number(decision.decision_time)) * 1000).toLocaleString()}
        </span>
      </div>

      <Section title={he.ccAgentAnalysis}>
        <AgentDecisionCards
          votes={decision.agent_votes || []}
          finalAction={decision.final_action}
        />
      </Section>

      <Section title={he.ccConsensusSection}>
        <p>{consensusText}</p>
        {vb.consensusPct != null ? (
          <p className="mono">
            {he.ccConsensusPct}: {vb.consensusPct}%
          </p>
        ) : null}
        <p className="muted">{he.ccConsensusNote}</p>
      </Section>

      <Section title={he.ccFinalDecision}>
        <dl className="ud-dl">
          <div className="ud-field">
            <dt>{he.action}</dt>
            <dd dir="ltr" className="mono">
              {decision.final_action}
            </dd>
          </div>
          <div className="ud-field">
            <dt>{he.confidence}</dt>
            <dd dir="ltr" className="mono">
              {fmtConfidence(decision.confidence) || "—"}
            </dd>
          </div>
        </dl>
        <p>{deExplanation || EXPLANATION_UNAVAILABLE_HE}</p>
        {decision.primary_reasons?.length ? (
          <ul className="ud-list">
            {decision.primary_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
        {log?.decision?.vote_counts ? (
          <pre className="detail-pre tiny" dir="ltr">
            {JSON.stringify(log.decision.vote_counts)}
          </pre>
        ) : null}
      </Section>

      <Section title={he.ccRiskChecks}>
        <p>
          {he.ccRisk}: <strong>{riskLabel(decision)}</strong>
          {decision.risk_level ? ` · ${decision.risk_level}` : ""}
        </p>
        {riskReason ? <p>{riskReason}</p> : <p className="muted">{he.ccNoRiskBlock}</p>}
        {decision.risk_factors?.length ? (
          <ul className="ud-list risk">
            {decision.risk_factors.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
      </Section>

      <Section title={he.ccExecutionResult}>
        <p>{execResult}</p>
        {decision.fill_price != null || decision.quantity != null ? (
          <dl className="ud-dl">
            {decision.fill_price != null ? (
              <div className="ud-field">
                <dt>{he.fillPrice}</dt>
                <dd dir="ltr" className="mono">
                  {Number(decision.fill_price).toFixed(4)}
                </dd>
              </div>
            ) : null}
            {decision.quantity != null ? (
              <div className="ud-field">
                <dt>{he.quantity}</dt>
                <dd dir="ltr" className="mono">
                  {Number(decision.quantity).toFixed(4)}
                </dd>
              </div>
            ) : null}
          </dl>
        ) : null}
      </Section>

      <details className="ud-collapse">
        <summary>
          {he.technicalDetails}
          {onCopy ? (
            <button
              type="button"
              className="btn-copy tiny"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onCopy();
              }}
            >
              {he.copyTechnical}
            </button>
          ) : null}
          {copyMsg ? <span className="copy-toast mono">{copyMsg}</span> : null}
        </summary>
        <pre className="detail-pre" dir="ltr">
          {JSON.stringify(
            {
              decision_id: decision.decision_id,
              status: decision.status,
              agreement: decision.agreement,
              indicators_used: decision.indicators_used,
              engine: decision.engine,
              execution: decision.execution,
              block_reason: decision.block_reason,
            },
            null,
            2
          )}
        </pre>
      </details>
    </div>
  );
}
