import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { type JobSite } from "../lib/api";

interface Props {
  cvName: string;
  sites: JobSite[];
  loading: boolean;
  onConfirm: (siteIds: string[]) => void;
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
  cvName,
  sites,
  loading,
  onConfirm,
  onCancel,
}: Props) {
  const openedAtRef = useRef(Date.now());
  const displaySites = sites.length > 0 ? sites : DEFAULT_SITES;
  const enabledIds = displaySites.filter((s) => s.enabled).map((s) => s.id);
  const [selected, setSelected] = useState<string[]>(enabledIds);

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
    // Ignore the click that opened the modal (common on touch devices).
    if (Date.now() - openedAtRef.current < 350) return;
    onCancel();
  };

  return createPortal(
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal run-agent-modal" onClick={(e) => e.stopPropagation()}>
        <h3>מאיזה אתרים לחפש משרות?</h3>
        <p>
          בחר אתר אחד או יותר לסריקה של <b>{cvName}</b>. הסוכן יאחד את כל הקבצים
          לפרופיל מועמד אחד ויחפש משרות מתאימות.
        </p>

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
          <button
            type="button"
            className="btn btn-primary"
            disabled={loading || selected.length === 0}
            onClick={() => onConfirm(selected)}
          >
            ▶ התחל סריקה
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
