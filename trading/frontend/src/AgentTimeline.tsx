/** Synchronized agent decision timeline under the price chart. */
import { useMemo, useState } from "react";
import type { UnifiedDecision } from "./api";
import {
  buildAgentTimeline,
  sideClass,
  type TimelineCell,
} from "./commandCenter";
import { he } from "./i18n/he";
import { toUnixSeconds } from "./chartMarkers";

type Props = {
  decisions: UnifiedDecision[];
  timeframe: string;
  selectedDecisionId?: string | null;
  highlightTs?: number | null;
  onSelectDecision?: (decisionId: string) => void;
  compact?: boolean;
};

const TF_SECS: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "4h": 14400,
  "1d": 86400,
};

export default function AgentTimeline({
  decisions,
  timeframe,
  selectedDecisionId,
  highlightTs,
  onSelectDecision,
  compact: compactProp,
}: Props) {
  const [compact, setCompact] = useState(compactProp ?? true);
  const tfSec = TF_SECS[timeframe] || 300;

  const { agents, cells } = useMemo(
    () => buildAgentTimeline(decisions, tfSec),
    [decisions, tfSec]
  );

  const byAgent = useMemo(() => {
    const map = new Map<string, TimelineCell[]>();
    for (const a of agents) map.set(a.id, []);
    for (const c of cells) {
      const arr = map.get(c.agentId) || [];
      arr.push(c);
      map.set(c.agentId, arr);
    }
    return map;
  }, [agents, cells]);

  // Cap visible cells for performance — show most recent N per agent in compact.
  const maxCells = compact ? 24 : 80;

  if (!agents.length) {
    return (
      <div className="agent-timeline empty" dir="rtl">
        <div className="at-head">
          <h3>{he.ccTimeline}</h3>
        </div>
        <p className="muted">{he.ccTimelineEmpty}</p>
      </div>
    );
  }

  return (
    <div className={`agent-timeline${compact ? " compact" : " expanded"}`} dir="rtl">
      <div className="at-head">
        <h3>{he.ccTimeline}</h3>
        <button
          type="button"
          className="chart-ctrl-btn"
          onClick={() => setCompact((c) => !c)}
        >
          {compact ? he.ccExpandTimeline : he.ccCompactTimeline}
        </button>
      </div>
      <div className="at-rows">
        {agents.map((a) => {
          const row = (byAgent.get(a.id) || []).slice(-maxCells);
          return (
            <div key={a.id} className="at-row">
              <div className="at-agent" title={a.id}>
                {a.name}
              </div>
              <div className="at-track" role="list">
                {row.map((c) => {
                  const selected = c.decisionId === selectedDecisionId;
                  const hi =
                    highlightTs != null &&
                    Math.abs(c.candleTs - highlightTs) < tfSec;
                  return (
                    <button
                      key={`${c.decisionId}:${c.agentId}`}
                      type="button"
                      role="listitem"
                      className={`at-cell ${sideClass(c.side)}${
                        selected ? " selected" : ""
                      }${hi ? " highlight" : ""}`}
                      title={`${c.side} · ${new Date(
                        toUnixSeconds(c.ts) * 1000
                      ).toLocaleString()}`}
                      onClick={() => onSelectDecision?.(c.decisionId)}
                    >
                      <span className="at-side">{c.side}</span>
                      {!compact && c.confidence != null ? (
                        <span className="at-conf mono">
                          {Math.round(
                            (Number(c.confidence) <= 1
                              ? Number(c.confidence) * 100
                              : Number(c.confidence))
                          )}
                          %
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      {highlightTs != null ? (
        <div className="at-cursor-label muted mono" dir="ltr">
          {he.ccHighlightTs}: {new Date(highlightTs * 1000).toLocaleString()}
        </div>
      ) : null}
    </div>
  );
}
