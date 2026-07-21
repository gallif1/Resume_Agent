import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { Briefcase, Check, Globe, Loader2, Play, Plus } from "lucide-react";
import { type JobSite } from "../lib/api";

interface Props {
  cvId: string;
  cvName: string;
  sites: JobSite[];
  loading: boolean;
  analyzing: boolean;
  suggestedDomains: string[];
  candidateSummary?: string;
  /** When true, modal copy reflects a rescan rather than a first scan. */
  hasPriorResults?: boolean;
  onAnalyze: (cvId: string) => Promise<void>;
  onConfirm: (siteIds: string[], domains: string[]) => void;
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
  analyzing,
  suggestedDomains,
  candidateSummary = "",
  hasPriorResults = false,
  onAnalyze,
  onConfirm,
  onCancel,
}: Props) {
  const openedAtRef = useRef(Date.now());
  const displaySites = sites.length > 0 ? sites : DEFAULT_SITES;
  const enabledIds = displaySites.filter((s) => s.enabled).map((s) => s.id);
  const [selected, setSelected] = useState<string[]>(enabledIds);
  const [selectedDomains, setSelectedDomains] = useState<string[]>([]);
  const [customDomain, setCustomDomain] = useState("");
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const requestedAnalysisRef = useRef<string | null>(null);

  useEffect(() => {
    openedAtRef.current = Date.now();
    const scrollY = window.scrollY;
    const previous = {
      overflow: document.body.style.overflow,
      position: document.body.style.position,
      top: document.body.style.top,
      width: document.body.style.width,
      htmlOverflow: document.documentElement.style.overflow,
    };
    // Lock background scroll without breaking iOS touch scrolling inside the modal.
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    document.body.style.position = "fixed";
    document.body.style.top = `-${scrollY}px`;
    document.body.style.width = "100%";
    return () => {
      document.documentElement.style.overflow = previous.htmlOverflow;
      document.body.style.overflow = previous.overflow;
      document.body.style.position = previous.position;
      document.body.style.top = previous.top;
      document.body.style.width = previous.width;
      window.scrollTo(0, scrollY);
    };
  }, []);

  useEffect(() => {
    setSelected(displaySites.filter((s) => s.enabled).map((s) => s.id));
  }, [displaySites]);

  useEffect(() => {
    setSelectedDomains(suggestedDomains);
  }, [suggestedDomains]);

  useEffect(() => {
    if (requestedAnalysisRef.current === cvId) return;
    requestedAnalysisRef.current = cvId;
    setAnalysisError(null);
    void onAnalyze(cvId).catch((error) => {
      setAnalysisError(
        error instanceof Error ? error.message : "ניתוח קורות החיים נכשל"
      );
    });
  }, [cvId, onAnalyze]);

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  };

  const selectAll = () => setSelected(enabledIds);
  const clearAll = () => setSelected([]);

  const suggestedSelectedCount = useMemo(
    () => suggestedDomains.filter((domain) => selectedDomains.includes(domain)).length,
    [selectedDomains, suggestedDomains]
  );
  const allSuggestedSelected =
    suggestedDomains.length > 0 && suggestedSelectedCount === suggestedDomains.length;

  const toggleDomain = (domain: string) => {
    setSelectedDomains((prev) =>
      prev.includes(domain)
        ? prev.filter((item) => item !== domain)
        : [...prev, domain]
    );
  };

  const toggleAllSuggestedDomains = () => {
    if (allSuggestedSelected) {
      setSelectedDomains((prev) =>
        prev.filter((domain) => !suggestedDomains.includes(domain))
      );
      return;
    }
    setSelectedDomains((prev) => {
      const extras = prev.filter((domain) => !suggestedDomains.includes(domain));
      return [...suggestedDomains, ...extras];
    });
  };

  const addCustomDomain = (event?: FormEvent) => {
    event?.preventDefault();
    const value = customDomain.trim();
    if (!value) return;
    setSelectedDomains((prev) =>
      prev.some((domain) => domain.toLowerCase() === value.toLowerCase())
        ? prev
        : [...prev, value]
    );
    setCustomDomain("");
  };

  const handleOverlayClick = () => {
    // Ignore the click that opened the modal (common on touch devices).
    if (Date.now() - openedAtRef.current < 350) return;
    onCancel();
  };

  return createPortal(
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div
        className="modal run-agent-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-agent-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="run-agent-modal-scroll">
          <h3 id="run-agent-modal-title">
            {hasPriorResults ? "סריקה מחדש" : "סרוק עכשיו"}
          </h3>
          <p>
            בחרו אתרי דרושים ותחומי חיפוש עבור <b>{cvName}</b>.
            {hasPriorResults
              ? " התוצאות החדשות יתווספו למשרות שכבר נשמרו."
              : " נתחיל לאסוף ולדרג משרות לפי קורות החיים שלכם."}
          </p>

          <div className="scan-modal-section">
            <div className="scan-modal-section-head">
              <span className="scan-config-label">לוחות דרושים</span>
            </div>
            {loading && sites.length === 0 ? (
              <p className="site-loading">טוען אתרים זמינים…</p>
            ) : (
              <div className="site-options" role="group" aria-label="בחירת אתרי חיפוש">
                {displaySites.map((site) => {
                  const checked = selected.includes(site.id);
                  return (
                    <label
                      key={site.id}
                      className={`site-option ${checked ? "selected" : ""} ${site.enabled ? "" : "site-option-disabled"}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={!site.enabled}
                        onChange={() => toggle(site.id)}
                      />
                      <span
                        className={`icon-bubble icon-bubble-sm ${
                          checked ? "icon-bubble-blue" : "icon-bubble-slate"
                        }`}
                        aria-hidden
                      >
                        {site.id === "linkedin" ? (
                          <Globe size={18} />
                        ) : (
                          <Briefcase size={18} />
                        )}
                      </span>
                      <span className="site-option-text">
                        <span className="site-option-title">{site.label_he}</span>
                        <span className="site-option-desc">
                          {site.enabled ? site.description_he : "לא זמין בשרת"}
                        </span>
                      </span>
                      {checked && (
                        <Check size={16} color="var(--accent)" aria-hidden />
                      )}
                    </label>
                  );
                })}
              </div>
            )}
          </div>

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

          <div className="scan-modal-section">
            <div className="scan-modal-section-head">
              <span className="scan-config-label">תחומים מומלצים</span>
              {suggestedDomains.length > 0 && (
                <label className="domain-select-all-toggle">
                  <input
                    type="checkbox"
                    checked={allSuggestedSelected}
                    onChange={toggleAllSuggestedDomains}
                  />
                  <span>
                    {allSuggestedSelected ? "בטל בחירת הכל" : "בחר הכל"}
                  </span>
                </label>
              )}
            </div>

            {candidateSummary && <p className="domain-summary">{candidateSummary}</p>}

            {analyzing ? (
              <div className="scan-modal-analyzing" role="status">
                <Loader2 className="domain-analyzing-spinner" size={22} aria-hidden />
                <span>מנתח את קורות החיים ומציע תחומי חיפוש…</span>
              </div>
            ) : suggestedDomains.length > 0 || selectedDomains.length > 0 ? (
              <div className="domain-chip-grid" role="group" aria-label="בחירת תחומים">
                {suggestedDomains.map((domain) => {
                  const isSelected = selectedDomains.includes(domain);
                  return (
                    <button
                      key={domain}
                      type="button"
                      className={`domain-chip ${isSelected ? "selected" : ""}`}
                      onClick={() => toggleDomain(domain)}
                      aria-pressed={isSelected}
                      dir="auto"
                    >
                      {isSelected && <Check size={14} strokeWidth={3} aria-hidden />}
                      <span className="domain-chip-label">{domain}</span>
                    </button>
                  );
                })}
                {selectedDomains
                  .filter((domain) => !suggestedDomains.includes(domain))
                  .map((domain) => (
                    <button
                      key={`custom-${domain}`}
                      type="button"
                      className="domain-chip selected"
                      onClick={() => toggleDomain(domain)}
                      aria-pressed
                      dir="auto"
                    >
                      <Check size={14} strokeWidth={3} aria-hidden />
                      <span className="domain-chip-label">{domain}</span>
                    </button>
                  ))}
              </div>
            ) : (
              <p className="site-loading">לא נמצאו תחומים מוצעים עדיין — אפשר להוסיף ידנית.</p>
            )}

            <form className="domain-custom-row" onSubmit={addCustomDomain}>
              <input
                type="text"
                className="domain-custom-input"
                placeholder="הוסיפו תחום מותאם אישית"
                value={customDomain}
                onChange={(e) => setCustomDomain(e.target.value)}
                dir="auto"
              />
              <button
                type="submit"
                className="btn btn-secondary domain-custom-add"
                disabled={!customDomain.trim()}
              >
                <Plus size={16} aria-hidden />
                הוסף
              </button>
            </form>

            {analysisError && <div className="error-box">{analysisError}</div>}
            {selectedDomains.length === 0 && !analyzing && (
              <div className="error-box site-error">יש לבחור לפחות תחום אחד.</div>
            )}
          </div>
        </div>

        <div className="modal-actions run-agent-modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            ביטול
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={loading || analyzing || selected.length === 0 || selectedDomains.length === 0}
            onClick={() => onConfirm(selected, selectedDomains)}
          >
            <Play size={16} aria-hidden />
            {hasPriorResults ? "סריקה מחדש" : "סרוק עכשיו"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
