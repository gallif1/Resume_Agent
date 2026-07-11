import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  isRemoteHostedServer,
  localCollectCommand,
  type JobSite,
} from "../lib/api";

interface Props {
  cvId: string;
  cvName: string;
  sites: JobSite[];
  loading: boolean;
  onConfirmCloud: (siteIds: string[]) => void;
  onPrepareLocal: (siteIds: string[]) => Promise<void>;
  onCancel: () => void;
}

const DEFAULT_SITES: JobSite[] = [
  {
    id: "drushim",
    label: "Drushim",
    label_he: "דרושים",
    description_he: "drushim.co.il",
    enabled: true,
  },
  {
    id: "linkedin",
    label: "LinkedIn",
    label_he: "לינקדאין",
    description_he: "משרות ציבוריות בישראל",
    enabled: true,
  },
  {
    id: "gotfriends",
    label: "GotFriends",
    label_he: "גוטפרנדס",
    description_he: "gotfriends.co.il",
    enabled: true,
  },
];

export default function RunAgentModal({
  cvId,
  cvName,
  sites,
  loading,
  onConfirmCloud,
  onPrepareLocal,
  onCancel,
}: Props) {
  const openedAtRef = useRef(Date.now());
  const displaySites = sites.length > 0 ? sites : DEFAULT_SITES;
  const enabledIds = displaySites.filter((s) => s.enabled).map((s) => s.id);
  const [selected, setSelected] = useState<string[]>(enabledIds);
  const [mode, setMode] = useState<"cloud" | "local">(
    isRemoteHostedServer() ? "local" : "cloud"
  );
  const [localCommand, setLocalCommand] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);

  useEffect(() => {
    openedAtRef.current = Date.now();
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  useEffect(() => {
    setSelected(displaySites.filter((s) => s.enabled).map((s) => s.id));
  }, [displaySites]);

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  };

  const selectAll = () => setSelected(enabledIds);
  const clearAll = () => setSelected([]);

  const handleOverlayClick = () => {
    if (Date.now() - openedAtRef.current < 350) return;
    onCancel();
  };

  const handlePrepareLocal = async () => {
    if (selected.length === 0) return;
    setPreparing(true);
    try {
      await onPrepareLocal(selected);
      setLocalCommand(localCollectCommand(cvId, selected));
    } finally {
      setPreparing(false);
    }
  };

  const copyCommand = async () => {
    if (!localCommand) return;
    try {
      await navigator.clipboard.writeText(localCommand);
    } catch {
      /* ignore */
    }
  };

  return createPortal(
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal run-agent-modal" onClick={(e) => e.stopPropagation()}>
        <h3>איך לסרוק משרות?</h3>
        <p>
          בחר מצב סריקה עבור <b>{cvName}</b>.
        </p>

        <div className="scan-mode-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            className={`scan-mode-tab ${mode === "local" ? "active" : ""}`}
            onClick={() => setMode("local")}
          >
            מחשב שלי (מומלץ)
          </button>
          <button
            type="button"
            role="tab"
            className={`scan-mode-tab ${mode === "cloud" ? "active" : ""}`}
            onClick={() => setMode("cloud")}
          >
            שרת בענן
          </button>
        </div>

        {mode === "local" ? (
          <div className="local-scan-panel">
            <p>
              הסריקה תרוץ על המחשב שלך (Windows/Mac/Linux). הטלפון לא יכול לגשת
              ישירות לאתרי משרות — רק המחשב.
            </p>
            <ol className="local-scan-steps">
              <li>לחץ «הכן סריקה מקומית» — השרת ינתח את קו&quot;ח ויבנה אסטרטגיה.</li>
              <li>העתק את הפקודה והרץ אותה בטרמינל על המחשב (פעם אחת clone + pip install).</li>
              <li>רענן את האתר — ההתאמות יופיעו אחרי שהסקריפט מסיים.</li>
            </ol>
            {localCommand ? (
              <div className="local-scan-command">
                <code>{localCommand}</code>
                <button type="button" className="btn btn-ghost btn-sm" onClick={copyCommand}>
                  העתק
                </button>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="cloud-scan-warning">
            סריקה בענן עלולה להיות איטית ולהעמיס על השרת (במיוחד ב-Render Free).
          </p>
        )}

        {loading && sites.length === 0 ? (
          <p className="site-loading">טוען אתרים זמינים…</p>
        ) : (
          <div className="site-options" role="group" aria-label="בחירת אתרי חיפוש">
            {displaySites.map((site) => {
              const checked = selected.includes(site.id);
              return (
                <label
                  key={site.id}
                  className={`site-option ${site.enabled ? "" : "site-option-disabled"}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!site.enabled}
                    onChange={() => toggle(site.id)}
                  />
                  <span className="site-option-text">
                    <span className="site-option-title">{site.label_he}</span>
                    <span className="site-option-desc">
                      {site.enabled ? site.description_he : "לא זמין בשרת"}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        )}

        <div className="site-quick-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={loading || enabledIds.length === 0}
            onClick={selectAll}
          >
            סמן הכל
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={loading}
            onClick={clearAll}
          >
            נקה הכל
          </button>
        </div>

        {selected.length === 0 && !loading && (
          <div className="error-box site-error">יש לבחור לפחות אתר אחד.</div>
        )}

        <div className="modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            ביטול
          </button>
          {mode === "local" ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={loading || preparing || selected.length === 0}
              onClick={handlePrepareLocal}
            >
              {preparing ? "מכין…" : localCommand ? "הוכן ✓" : "הכן סריקה מקומית"}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              disabled={loading || selected.length === 0}
              onClick={() => onConfirmCloud(selected)}
            >
              ▶ סריקה בענן
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
