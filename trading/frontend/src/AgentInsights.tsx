/** Optional agent performance insights — recommendation stats, not trade P&L. */
import type { PerformanceStats, UnifiedDecision } from "./api";
import { computeAgentInsights } from "./commandCenter";
import { he } from "./i18n/he";

type Props = {
  decisions: UnifiedDecision[];
  performance?: PerformanceStats | null;
};

export default function AgentInsights({ decisions, performance }: Props) {
  const rows = computeAgentInsights(decisions, performance);
  if (!rows.length) {
    return (
      <section className="panel agent-insights-panel" dir="rtl">
        <h2>{he.ccInsights}</h2>
        <p className="muted">{he.ccInsightsEmpty}</p>
      </section>
    );
  }
  return (
    <section className="panel agent-insights-panel" dir="rtl">
      <h2>{he.ccInsights}</h2>
      <p className="muted">{he.ccInsightsDisclaimer}</p>
      <div className="insights-table-wrap">
        <table className="insights-table">
          <thead>
            <tr>
              <th>{he.agents}</th>
              <th>{he.ccTotalRecs}</th>
              <th>B / S / H</th>
              <th>{he.ccAgreementRate}</th>
              <th>{he.ccLedToFill}</th>
              <th>{he.ccAccuracy5m}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td className="mono" dir="ltr">
                  {r.total}
                </td>
                <td className="mono" dir="ltr">
                  {r.buy}/{r.sell}/{r.hold}
                </td>
                <td className="mono" dir="ltr">
                  {r.agreementRate != null
                    ? `${Math.round(r.agreementRate * 100)}%`
                    : "—"}
                </td>
                <td className="mono" dir="ltr">
                  {r.ledToFill}
                  {r.fillRate != null ? ` (${Math.round(r.fillRate * 100)}%)` : ""}
                </td>
                <td className="mono" dir="ltr">
                  {r.accuracy5mPct != null ? `${r.accuracy5mPct.toFixed(1)}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted tiny">{rows[0]?.methodology}</p>
    </section>
  );
}
