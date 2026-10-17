/**
 * Historical decision replay — display-only.
 * Does NOT trigger trades, agent inference, or live execution.
 */
import type { UnifiedDecision } from "./api";
import { sortedDecisionsForReplay, type ReplayState } from "./commandCenter";
import { he } from "./i18n/he";
import { toUnixSeconds } from "./chartMarkers";

type Props = {
  decisions: UnifiedDecision[];
  replay: ReplayState;
  onChange: (next: ReplayState) => void;
  earliestReplayTs?: number | null;
};

export default function ReplayControls({
  decisions,
  replay,
  onChange,
  earliestReplayTs,
}: Props) {
  const sorted = sortedDecisionsForReplay(decisions);
  const n = sorted.length;
  const idx = replay.index >= 0 && replay.index < n ? replay.index : n > 0 ? n - 1 : -1;
  const current = idx >= 0 ? sorted[idx] : null;

  const enter = () => {
    if (!n) return;
    onChange({ ...replay, active: true, playing: false, index: Math.max(0, n - 1) });
  };
  const exit = () => onChange({ active: false, playing: false, index: -1, speed: replay.speed });
  const step = (delta: number) => {
    if (!n) return;
    const base = idx < 0 ? (delta > 0 ? -1 : n) : idx;
    const next = Math.min(n - 1, Math.max(0, base + delta));
    onChange({ ...replay, active: true, playing: false, index: next });
  };

  return (
    <div className="replay-controls" dir="rtl">
      <div className="replay-head">
        <h3>{he.ccReplay}</h3>
        <span className="muted">{he.ccReplaySafe}</span>
      </div>
      {earliestReplayTs != null ? (
        <p className="muted mono" dir="ltr">
          {he.ccEarliestReplay}:{" "}
          {new Date(toUnixSeconds(earliestReplayTs) * 1000).toLocaleString()}
        </p>
      ) : null}
      {!replay.active ? (
        <button type="button" className="btn btn-start" disabled={!n} onClick={enter}>
          {he.ccEnterReplay}
        </button>
      ) : (
        <div className="replay-toolbar">
          <button type="button" className="chart-ctrl-btn" onClick={() => step(-1)} disabled={idx <= 0}>
            {he.ccStepBack}
          </button>
          <button
            type="button"
            className="chart-ctrl-btn"
            onClick={() => onChange({ ...replay, playing: !replay.playing })}
            disabled={!n}
          >
            {replay.playing ? he.ccPause : he.ccPlay}
          </button>
          <button
            type="button"
            className="chart-ctrl-btn"
            onClick={() => step(1)}
            disabled={idx < 0 || idx >= n - 1}
          >
            {he.ccStepForward}
          </button>
          <label className="replay-speed">
            {he.ccReplaySpeed}
            <select
              value={replay.speed}
              onChange={(e) =>
                onChange({ ...replay, speed: Number(e.target.value) || 1 })
              }
            >
              <option value={0.5}>0.5×</option>
              <option value={1}>1×</option>
              <option value={2}>2×</option>
              <option value={4}>4×</option>
            </select>
          </label>
          <button type="button" className="chart-ctrl-btn chart-ctrl-primary" onClick={exit}>
            {he.ccReturnLive}
          </button>
          <span className="mono muted" dir="ltr">
            {idx + 1}/{n}
            {current
              ? ` · ${new Date(toUnixSeconds(Number(current.decision_time)) * 1000).toLocaleString()}`
              : ""}
          </span>
        </div>
      )}
      {!n ? <p className="muted">{he.ccReplayEmpty}</p> : null}
    </div>
  );
}
