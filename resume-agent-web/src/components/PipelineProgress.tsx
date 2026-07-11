import type { CollectionSummary, CvScanStatus, SiteCollectionSummary } from "../lib/api";

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
  if (!scanStatus) {
    return null;
  }

  const steps = scanStatus.steps ?? [];
  const running = scanStatus.running ?? false;
  const detail = scanStatus.detail?.trim();
  const logLines = scanStatus.log ?? [];
  const warnings = scanStatus.warnings ?? [];
  const hasWarnings = warnings.length > 0;
  const hasCollectionDetails = Boolean(scanStatus.collection);
  const showPanel =
    running || Boolean(scanStatus.error) || hasWarnings || hasCollectionDetails;

  if (!showPanel) {
    return null;
  }

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

      {hasWarnings && (
        <div className="warning-box">
          <div className="warning-box-title">שימו לב — בעיות באיסוף משרות</div>
          <ul className="warning-list">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {!running && scanStatus.collection && (
        <CollectionDetails summary={scanStatus.collection} />
      )}

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

function CollectionDetails({ summary }: { summary: CollectionSummary }) {
  const siteEntries: Array<[string, SiteCollectionSummary]> = [];
  for (const site of ["drushim", "linkedin", "gotfriends"] as const) {
    const data = summary[site];
    if (data) siteEntries.push([site, data]);
  }

  if (siteEntries.length === 0) {
    return null;
  }

  const labels: Record<string, string> = {
    drushim: "דרושים",
    linkedin: "לינקדאין",
    gotfriends: "GotFriends",
  };

  return (
    <div className="collection-summary">
      <div className="collection-summary-title">סיכום איסוף משרות</div>
      <div className="collection-summary-grid">
        {siteEntries.map(([site, data]) => (
          <div key={site} className="collection-summary-card">
            <div className="collection-summary-site">{labels[site] ?? site}</div>
            <div className="collection-summary-stats">
              <span>נמצאו: {data.raw}</span>
              <span>חדשות: {data.new}</span>
              <span>כבר במערכת: {data.already_in_db}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
