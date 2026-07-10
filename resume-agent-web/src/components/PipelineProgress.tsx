import type { CvScanStatus } from "../lib/api";

const STEP_ICONS: Record<string, string> = {
  pending: "○",
  running: "◐",
  success: "✓",
  failed: "✗",
  skipped: "−",
};

interface Props {
  scanStatus: CvScanStatus | null;
  compact?: boolean;
}

export default function PipelineProgress({
  scanStatus,
  compact = false,
}: Props) {
  if (!scanStatus?.running && !scanStatus?.error) {
    return null;
  }

  const steps = scanStatus.steps ?? [];
  const running = scanStatus.running ?? false;
  const detail = scanStatus.detail?.trim();
  const logLines = scanStatus.log ?? [];

  return (
    <div className={`pipeline-panel ${compact ? "pipeline-panel-compact" : ""}`}>
      {running && (
        <div className="pipeline-live">
          <span className="pipeline-live-label">מתבצע עכשיו</span>
          <p className="pipeline-live-detail">{detail || "מעבד…"}</p>
        </div>
      )}

      {steps.length > 0 && (
        <div className="pipeline-steps">
          {steps.map((step) => (
            <div key={step.key} className={`pipeline-step ${step.status}`}>
              <span className="step-icon">{STEP_ICONS[step.status]}</span>
              <span>{step.name}</span>
            </div>
          ))}
        </div>
      )}

      {scanStatus.error && <div className="error-box">{scanStatus.error}</div>}

      {logLines.length > 0 && (
        <div className="pipeline-log-wrap">
          <div className="pipeline-log-label">יומן פעילות</div>
          <pre className="pipeline-log pipeline-log-visible" dir="ltr">
            {logLines.join("\n")}
          </pre>
        </div>
      )}
    </div>
  );
}
