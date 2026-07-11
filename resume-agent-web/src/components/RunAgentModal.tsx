import { useEffect, useState } from "react";
import { type JobSite } from "../lib/api";

interface Props {
  cvName: string;
  sites: JobSite[];
  loading: boolean;
  onConfirm: (siteIds: string[]) => void;
  onCancel: () => void;
}

export default function RunAgentModal({
  cvName,
  sites,
  loading,
  onConfirm,
  onCancel,
}: Props) {
  const enabledIds = sites.filter((s) => s.enabled).map((s) => s.id);
  const [selected, setSelected] = useState<string[]>(enabledIds);

  useEffect(() => {
    setSelected(sites.filter((s) => s.enabled).map((s) => s.id));
  }, [sites]);

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  };

  const selectAll = () => setSelected(enabledIds);
  const clearAll = () => setSelected([]);

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal run-agent-modal" onClick={(e) => e.stopPropagation()}>
        <h3>מאיזה אתרים לחפש משרות?</h3>
        <p>
          בחר אתר אחד או יותר לסריקה של <b>{cvName}</b>. ניתן לסמן כמה אתרים במקביל.
        </p>

        {loading ? (
          <p className="site-loading">טוען אתרים זמינים…</p>
        ) : (
          <div className="site-options" role="group" aria-label="בחירת אתרי חיפוש">
            {sites.map((site) => {
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
          <button className="btn btn-ghost" onClick={onCancel}>
            ביטול
          </button>
          <button
            className="btn btn-primary"
            disabled={loading || selected.length === 0}
            onClick={() => onConfirm(selected)}
          >
            ▶ התחל סריקה
          </button>
        </div>
      </div>
    </div>
  );
}
