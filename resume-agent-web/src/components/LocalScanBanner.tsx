interface Props {
  onOpenLocalGuide?: () => void;
}

export default function LocalScanBanner({ onOpenLocalGuide }: Props) {
  return (
    <div className="local-scan-banner" role="note">
      <div className="local-scan-banner-text">
        <strong>סריקה על השרת איטית ומעמיסה?</strong>
        <span>
          {" "}
          אפשר לאסוף משרות על המחשב שלך (מהיר יותר, בלי עומס על Render) ולהעלות
          לכאן לחישוב התאמות.
        </span>
      </div>
      {onOpenLocalGuide ? (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onOpenLocalGuide}>
          איך עושים?
        </button>
      ) : null}
    </div>
  );
}
